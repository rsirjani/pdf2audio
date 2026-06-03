"""Batched ingest pipeline using vLLM TTS."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from .library import Library, _safe_name
from .schemas import Document
from .tts_vllm import OrpheusTTS


def _parse_pdf_subprocess(pdf_path: Path, output_dir: Path, project: str) -> tuple[Document, str]:
    """Run parse in a fresh Python subprocess to avoid vLLM/torch state conflicts."""
    import subprocess
    output_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [sys.executable, "-m", "pdf_reader.parse_in_proc",
         str(pdf_path), str(output_dir), project],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        log.error("parse_in_proc stderr: %s", proc.stderr[-2000:])
        raise RuntimeError(f"parse subprocess failed (rc={proc.returncode}): {proc.stderr[-400:]}")
    doc_path = output_dir / "document.json"
    md_path = output_dir / "document.md"
    if not doc_path.exists():
        raise RuntimeError(f"parse subprocess produced no document.json: {proc.stdout[-400:]}")
    doc = Document.model_validate_json(doc_path.read_text())
    md = md_path.read_text() if md_path.exists() else ""
    return doc, md

log = logging.getLogger(__name__)


def _sync_to_nas(local_doc_dir: Path, project: str, doc_id: str, user_id: str = "default") -> None:
    """Tar local doc dir and stream to NAS via SSH. Controlled by PDF_READER_NAS_TARGET env var."""
    target = os.environ.get("PDF_READER_NAS_TARGET")
    if not target:
        return
    remote_proj = f"/volume1/docker/pdf-reader/data/users/{user_id}/projects/{project}"
    cmd = (
        f"mkdir -p '{remote_proj}' && tar xf - -C '{remote_proj}'"
    )
    log.info("Syncing %s -> %s:%s/%s", local_doc_dir, target, remote_proj, doc_id)
    t0 = time.time()
    tar = subprocess.Popen(
        ["tar", "cf", "-", "-C", str(local_doc_dir.parent), doc_id],
        stdout=subprocess.PIPE,
    )
    ssh = subprocess.Popen(
        ["ssh", target, cmd],
        stdin=tar.stdout,
    )
    tar.stdout.close()
    ssh_rc = ssh.wait()
    tar_rc = tar.wait()
    if ssh_rc != 0 or tar_rc != 0:
        log.warning("NAS sync FAILED (tar=%d ssh=%d) — doc still on local disk", tar_rc, ssh_rc)
    else:
        log.info("NAS sync complete in %.1fs", time.time() - t0)


def parse_doc(pdf_path: Path, library: Library, project: str = "default", user_id: str = "default") -> Document:
    """Parse-only phase: PDF -> Document with no audio yet. GPU-friendly when vLLM is unloaded."""
    project = _safe_name(project)
    log.info("Parsing %s into user=%s project=%s", pdf_path, user_id, project)
    t0 = time.time()

    proj_dir = library.project_dir(user_id, project)
    stored_pdf = proj_dir / pdf_path.name
    if pdf_path.resolve() != stored_pdf.resolve():
        stored_pdf.write_bytes(pdf_path.read_bytes())

    import shutil
    import tempfile
    tmp_parse_dir = Path(tempfile.mkdtemp(prefix="_parse_", dir=proj_dir))
    doc, markdown_text = _parse_pdf_subprocess(stored_pdf, tmp_parse_dir, project=project)
    doc.user_id = user_id
    log.info("Parse complete in %.1fs", time.time() - t0)

    target_dir = library.doc_dir(user_id, project, doc.id)
    target_dir.mkdir(parents=True, exist_ok=True)

    tmp_images = tmp_parse_dir / "images"
    if tmp_images.exists():
        final_images = target_dir / "images"
        final_images.mkdir(exist_ok=True)
        for img in tmp_images.iterdir():
            img.replace(final_images / img.name)
    if tmp_parse_dir.exists():
        shutil.rmtree(tmp_parse_dir, ignore_errors=True)

    md_path = target_dir / "document.md"
    md_path.write_text(markdown_text)
    doc.markdown_file = "document.md"

    final_pdf = target_dir / "source.pdf"
    if not final_pdf.exists():
        stored_pdf.replace(final_pdf)
    doc.source_pdf = "source.pdf"

    library.save_document(doc)  # checkpoint after parse so partial work survives crashes
    return doc


def synthesize_doc(doc: Document, library: Library, tts: OrpheusTTS) -> Document:
    """TTS phase: synthesize audio for an already-parsed Document. Requires vLLM loaded."""
    project = doc.project
    user_id = doc.user_id or "default"
    target_dir = library.doc_dir(user_id, project, doc.id)
    audio_dir = target_dir / "audio"
    audio_dir.mkdir(exist_ok=True)

    texts: list[str] = []
    paths: list[Path] = []
    sent_refs: list = []
    for block in doc.blocks:
        for s in block.sentences:
            speak = s.tts_text if s.tts_text is not None else s.text
            if not speak.strip():
                continue
            texts.append(speak)
            paths.append(audio_dir / f"{s.id}.wav")
            sent_refs.append(s)

    log.info("Synthesizing %d sentences for doc=%s...", len(texts), doc.id)
    t1 = time.time()
    durations = tts.synthesize_batch(texts, paths)
    synth_elapsed = time.time() - t1
    total_audio = sum(durations)
    log.info(
        "TTS complete: %.1fs wall, %.1fs audio, batched RTF=%.3fx",
        synth_elapsed, total_audio,
        synth_elapsed / total_audio if total_audio > 0 else float("inf"),
    )

    cumulative_ms = 0
    for s, dur in zip(sent_refs, durations):
        s.audio = f"audio/{s.id}.wav"
        s.duration_ms = int(dur * 1000)
        s.start_offset_ms = cumulative_ms
        cumulative_ms += s.duration_ms

    next_audible_offset: int | None = None
    for block in reversed(doc.blocks):
        if block.sentences and any(s.audio for s in block.sentences):
            next_audible_offset = block.sentences[0].start_offset_ms
        elif block.type in ("equation", "table"):
            if next_audible_offset is not None:
                block.pause_at_ms = next_audible_offset

    doc.total_duration_ms = cumulative_ms
    doc.voice = tts.voice
    library.save_document(doc)
    log.info("Done. user=%s project=%s doc_id=%s duration=%.1fs", user_id, project, doc.id, cumulative_ms / 1000)

    _sync_to_nas(target_dir, project, doc.id, user_id=user_id)
    return doc


def ingest_pdf(pdf_path: Path, library: Library, tts: OrpheusTTS, project: str = "default", user_id: str = "default") -> Document:
    """Legacy single-shot: parse + synth in one call. Use parse_doc + synthesize_doc for batches."""
    doc = parse_doc(pdf_path, library, project=project, user_id=user_id)
    return synthesize_doc(doc, library, tts)
