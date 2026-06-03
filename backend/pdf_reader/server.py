from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import limits
from .auth import require_user
from .library import DEFAULT_USER, Library, _safe_name
from .schemas import Document, DocumentSummary, Project

log = logging.getLogger(__name__)

DATA_ROOT = Path(os.environ.get("PDF_READER_DATA", str(Path.home() / "pdf-reader-data")))
INBOX_ROOT = Path(os.environ.get("PDF_READER_INBOX", str(DATA_ROOT / "inbox")))
FRONTEND_DIR = Path(os.environ.get("PDF_READER_FRONTEND", str(Path(__file__).resolve().parents[1] / "static")))
LOAD_TTS = os.environ.get("PDF_READER_LOAD_TTS", "1") != "0"

if LOAD_TTS:
    from .pipeline_vllm import parse_doc, synthesize_doc
    from .tts_vllm import OrpheusTTS
else:
    parse_doc = None
    synthesize_doc = None
    OrpheusTTS = None


class AppState:
    library: Library | None = None
    tts: OrpheusTTS | None = None
    jobs: dict[str, dict] = {}
    watcher_thread: threading.Thread | None = None
    watcher_stop: threading.Event | None = None
    # Batch-aware ingest worker
    ingest_queue: "queue.Queue[tuple[Path, str, str, str]]" = None  # type: ignore[assignment]
    ingest_worker: threading.Thread | None = None


import queue as _queue_mod  # noqa: E402  (after AppState for type)
state = AppState()


def _load_tts() -> None:
    """Load vLLM TTS into GPU. Idempotent — no-op if already loaded."""
    if state.tts is not None or not LOAD_TTS:
        return
    gpu_util = float(os.environ.get("PDF_READER_GPU_UTIL", "0.55"))
    log.info("Loading Orpheus TTS via vLLM (gpu_util=%s)...", gpu_util)
    state.tts = OrpheusTTS(voice="tara", gpu_memory_utilization=gpu_util)


def _unload_tts() -> None:
    """Free vLLM GPU memory so Marker can use the GPU during parse."""
    if state.tts is None:
        return
    log.info("Unloading Orpheus TTS to free GPU for Marker...")
    try:
        # Tear down internal vLLM state explicitly
        engine = getattr(state.tts.llm, "llm_engine", None)
        if engine is not None and hasattr(engine, "shutdown"):
            try: engine.shutdown()
            except Exception: pass
    except Exception:
        pass
    state.tts = None
    import gc
    gc.collect()
    try:
        import torch
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()
    except Exception:
        pass


def _ingest_worker_loop() -> None:
    """Drains the ingest queue in batches: parse-all (vLLM unloaded) → load vLLM → synth-all → unload.

    Big win for batches of papers: vLLM warm-up cost is paid once per drain, and Marker gets
    full GPU during parse (no contention with vLLM CUDA graphs).
    """
    while True:
        # Block until at least one job, then collect more within a 5s debounce window
        try:
            first = state.ingest_queue.get()
        except Exception:
            return
        batch: list[tuple[Path, str, str]] = [first]
        try:
            while True:
                batch.append(state.ingest_queue.get(timeout=5.0))
        except _queue_mod.Empty:
            pass

        log.info("Ingest batch: %d job(s)", len(batch))

        # Phase 1: parse all (vLLM not loaded → Marker has full GPU)
        _unload_tts()
        parsed: list[tuple[Document, str, str, str]] = []  # (doc, job_id, project, user_id)
        for pdf_path, project, job_id, user_id in batch:
            try:
                state.jobs[job_id] = {"status": "parsing", "filename": pdf_path.name, "project": project, "user_id": user_id}
                doc = parse_doc(pdf_path, state.library, project=project, user_id=user_id)
                parsed.append((doc, job_id, project, user_id))
            except Exception as e:
                log.exception("Parse failed for %s", pdf_path)
                state.jobs[job_id] = {"status": "error", "message": f"parse: {e}", "filename": pdf_path.name, "project": project, "user_id": user_id}

        # Phase 2: load vLLM, synth all
        if parsed:
            _load_tts()
            for doc, job_id, project, user_id in parsed:
                pdf_name = state.jobs[job_id]["filename"]
                try:
                    state.jobs[job_id] = {"status": "synthesizing", "filename": pdf_name, "project": project, "user_id": user_id}
                    doc = synthesize_doc(doc, state.library, state.tts)
                    state.jobs[job_id] = {"status": "done", "doc_id": doc.id, "project": project, "filename": pdf_name, "user_id": user_id}
                    try:
                        limits.record_ingest(user_id, state.library.user_storage_bytes(user_id))
                    except Exception:
                        log.exception("Failed to record ingest stats for %s", user_id)
                except Exception as e:
                    log.exception("Synth failed for doc %s", doc.id)
                    state.jobs[job_id] = {"status": "error", "message": f"synth: {e}", "filename": pdf_name, "project": project, "user_id": user_id}

        log.info("Ingest batch done; idling for next job")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Data root: %s", DATA_ROOT)
    log.info("Inbox: %s", INBOX_ROOT)
    state.library = Library(DATA_ROOT)
    limits.init(DATA_ROOT)
    INBOX_ROOT.mkdir(parents=True, exist_ok=True)

    if LOAD_TTS:
        state.ingest_queue = _queue_mod.Queue()
        state.ingest_worker = threading.Thread(target=_ingest_worker_loop, daemon=True)
        state.ingest_worker.start()
        log.info("Ingest worker started (lazy TTS load)")
    else:
        log.warning("PDF_READER_LOAD_TTS=0 — TTS disabled (serve-only)")

    state.watcher_stop = threading.Event()
    if LOAD_TTS:
        state.watcher_thread = threading.Thread(target=_watcher_loop, daemon=True)
        state.watcher_thread.start()
        log.info("Inbox watcher started on %s", INBOX_ROOT)
    else:
        log.info("Watcher disabled (LOAD_TTS=0) — serve-only mode")

    yield

    state.watcher_stop.set()
    if state.watcher_thread:
        state.watcher_thread.join(timeout=2)


app = FastAPI(title="pdf-reader", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- API ----

class IngestResponse(BaseModel):
    job_id: str
    status: str
    doc_id: str | None = None
    project: str | None = None
    message: str | None = None


@app.get("/api/health")
def health():
    q = state.ingest_queue
    return {
        "ok": True,
        "voice": state.tts.voice if state.tts else "tara",
        "tts_loaded": state.tts is not None,
        "tts_available": LOAD_TTS,
        "queue_depth": q.qsize() if q is not None else 0,
        "data_root": str(DATA_ROOT),
        "inbox": str(INBOX_ROOT),
    }


@app.get("/api/me")
def me(user_id: str = Depends(require_user)):
    """Return the currently signed-in user identity and usage."""
    return {"email": user_id, **limits.get_user_stats(user_id)}


@app.get("/api/usage")
def usage(user_id: str = Depends(require_user)):
    return limits.get_user_stats(user_id)


@app.get("/api/projects", response_model=list[Project])
def list_projects(user_id: str = Depends(require_user)):
    from datetime import datetime
    out = []
    for name in state.library.list_projects(user_id):
        docs = state.library.list_documents(user_id, project=name)
        total_ms = sum(d.total_duration_ms for d in docs)
        created = min((d.created_at for d in docs), default=datetime.utcnow())
        out.append(Project(name=name, user_id=user_id, doc_count=len(docs), total_duration_ms=total_ms, created_at=created))
    return out


@app.get("/api/projects/{project}/docs", response_model=list[DocumentSummary])
def list_docs(project: str, user_id: str = Depends(require_user)):
    project = _safe_name(project)
    docs = state.library.list_documents(user_id, project=project)
    return [
        DocumentSummary(
            id=d.id,
            title=d.title,
            project=d.project,
            user_id=d.user_id,
            created_at=d.created_at,
            total_duration_ms=d.total_duration_ms,
            block_count=len(d.blocks),
        )
        for d in docs
    ]


@app.get("/api/docs", response_model=list[DocumentSummary])
def list_all_docs(user_id: str = Depends(require_user)):
    docs = state.library.list_documents(user_id)
    return [
        DocumentSummary(
            id=d.id,
            title=d.title,
            project=d.project,
            user_id=d.user_id,
            created_at=d.created_at,
            total_duration_ms=d.total_duration_ms,
            block_count=len(d.blocks),
        )
        for d in docs
    ]


@app.get("/api/projects/{project}/docs/{doc_id}", response_model=Document)
def get_doc(project: str, doc_id: str, user_id: str = Depends(require_user)):
    project = _safe_name(project)
    doc = state.library.load_document(user_id, project, doc_id)
    if doc is None:
        raise HTTPException(404, "document not found")
    return doc


@app.get("/api/projects/{project}/docs/{doc_id}/audio/{sentence_id}")
def get_audio(project: str, doc_id: str, sentence_id: str, user_id: str = Depends(require_user)):
    project = _safe_name(project)
    audio_path = state.library.doc_dir(user_id, project, doc_id) / "audio" / f"{sentence_id}.wav"
    if not audio_path.exists():
        raise HTTPException(404, "audio not found")
    return FileResponse(audio_path, media_type="audio/wav")


@app.get("/api/projects/{project}/docs/{doc_id}/image/{name}")
def get_image(project: str, doc_id: str, name: str, user_id: str = Depends(require_user)):
    project = _safe_name(project)
    img_path = state.library.doc_dir(user_id, project, doc_id) / "images" / name
    if not img_path.exists():
        raise HTTPException(404, "image not found")
    return FileResponse(img_path)


@app.get("/api/projects/{project}/docs/{doc_id}/source.pdf")
def get_pdf(project: str, doc_id: str, user_id: str = Depends(require_user)):
    project = _safe_name(project)
    pdf_path = state.library.doc_dir(user_id, project, doc_id) / "source.pdf"
    if not pdf_path.exists():
        raise HTTPException(404, "PDF not found")
    return FileResponse(pdf_path, media_type="application/pdf", filename=f"{doc_id}.pdf")


@app.get("/api/projects/{project}/docs/{doc_id}/markdown")
def get_markdown(project: str, doc_id: str, user_id: str = Depends(require_user)):
    project = _safe_name(project)
    md_path = state.library.doc_dir(user_id, project, doc_id) / "document.md"
    if not md_path.exists():
        raise HTTPException(404, "markdown not found")
    doc = state.library.load_document(user_id, project, doc_id)
    fname = f"{doc.title if doc else doc_id}.md"
    return FileResponse(md_path, media_type="text/markdown; charset=utf-8", filename=fname)


@app.get("/api/projects/{project}/docs/{doc_id}/audiobook.mp3")
def get_audiobook(project: str, doc_id: str, user_id: str = Depends(require_user)):
    """Concatenated MP3 of all sentence WAVs — for offline phone listening."""
    project = _safe_name(project)
    d = state.library.doc_dir(user_id, project, doc_id)
    mp3_path = d / "audiobook.mp3"
    if not mp3_path.exists():
        doc = state.library.load_document(user_id, project, doc_id)
        if doc is None:
            raise HTTPException(404, "document not found")
        _build_audiobook(doc, d, mp3_path)
    if not mp3_path.exists():
        raise HTTPException(500, "audiobook generation failed")
    doc = state.library.load_document(user_id, project, doc_id)
    fname = f"{doc.title if doc else doc_id}.mp3"
    return FileResponse(mp3_path, media_type="audio/mpeg", filename=fname)


@app.delete("/api/projects/{project}/docs/{doc_id}")
def delete_doc(project: str, doc_id: str, user_id: str = Depends(require_user)):
    project = _safe_name(project)
    d = state.library.doc_dir(user_id, project, doc_id)
    if not d.exists():
        raise HTTPException(404, "document not found")
    shutil.rmtree(d, ignore_errors=True)
    return {"ok": True, "deleted": doc_id}


def _build_audiobook(doc: Document, doc_dir: Path, mp3_path: Path) -> None:
    """Concatenate all sentence WAVs + encode to MP3 via ffmpeg."""
    audio_dir = doc_dir / "audio"
    if not audio_dir.exists():
        return
    files: list[Path] = []
    for block in doc.blocks:
        for s in block.sentences:
            if s.audio:
                p = doc_dir / s.audio
                if p.exists():
                    files.append(p)
    if not files:
        return
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as listf:
        for p in files:
            listf.write(f"file '{p.as_posix()}'\n")
        list_path = listf.name
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
             "-ac", "1", "-b:a", "64k", str(mp3_path)],
            check=True,
            capture_output=True,
        )
    finally:
        os.unlink(list_path)


def _enqueue_ingest(pdf_path: Path, project: str, job_id: str, user_id: str) -> None:
    state.jobs[job_id] = {"status": "queued", "filename": pdf_path.name, "project": project, "user_id": user_id}
    state.ingest_queue.put((pdf_path, project, job_id, user_id))


@app.post("/api/projects/{project}/ingest", response_model=IngestResponse)
async def ingest(
    project: str,
    file: UploadFile,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(require_user),
):
    project = _safe_name(project)
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "expected a .pdf file")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    try:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)
        size = tmp_path.stat().st_size
    finally:
        tmp.close()

    # Check per-user limits before accepting the upload.
    try:
        limits.check_can_ingest(
            user_id,
            pdf_size_bytes=size,
            current_storage_bytes=state.library.user_storage_bytes(user_id),
        )
    except HTTPException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    proj_dir = state.library.project_dir(user_id, project)
    final_path = proj_dir / file.filename
    shutil.move(tmp_path, final_path)

    job_id = f"job_{int(time.time()*1000)}_{file.filename}"
    _enqueue_ingest(final_path, project, job_id, user_id)
    return IngestResponse(job_id=job_id, status="queued", project=project)


@app.get("/api/jobs/{job_id}", response_model=IngestResponse)
def get_job(job_id: str, user_id: str = Depends(require_user)):
    j = state.jobs.get(job_id)
    if j is None:
        raise HTTPException(404, "job not found")
    if j.get("user_id") and j["user_id"] != user_id:
        raise HTTPException(404, "job not found")
    return IngestResponse(
        job_id=job_id,
        status=j["status"],
        doc_id=j.get("doc_id"),
        project=j.get("project"),
        message=j.get("message"),
    )


@app.get("/api/jobs")
def list_jobs(user_id: str = Depends(require_user)):
    return [
        {"job_id": jid, **j}
        for jid, j in state.jobs.items()
        if j.get("user_id") == user_id
    ]


# ---- Inbox watcher: drop PDFs in <inbox>/<project>/ to auto-ingest ----

def _watcher_loop():
    """Drop-folder watcher (legacy single-tenant). Files dropped under
    <INBOX>/<project>/ get attributed to the DEFAULT user."""
    seen: set[Path] = set()
    while not state.watcher_stop.is_set():
        try:
            for proj_dir in INBOX_ROOT.iterdir():
                if not proj_dir.is_dir():
                    continue
                project = proj_dir.name
                for pdf in proj_dir.glob("*.pdf"):
                    if pdf in seen:
                        continue
                    seen.add(pdf)
                    job_id = f"watch_{int(time.time()*1000)}_{pdf.name}"
                    log.info("Inbox: picked up %s -> project=%s (user=%s)", pdf.name, project, DEFAULT_USER)
                    _enqueue_ingest(pdf, project, job_id, DEFAULT_USER)
        except Exception as e:
            log.warning("Watcher error: %s", e)
        state.watcher_stop.wait(timeout=3.0)


# ---- Static frontend (mounted last so /api/* still wins) ----
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
else:
    log.warning("No frontend at %s — UI will 404", FRONTEND_DIR)
