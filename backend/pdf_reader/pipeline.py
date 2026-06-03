from __future__ import annotations

import json
import logging
from pathlib import Path

from .parser import parse_pdf
from .schemas import Document
from .tts import OrpheusTTS

log = logging.getLogger(__name__)


class Library:
    def __init__(self, root: Path):
        self.root = root
        self.pdfs_dir = root / "pdfs"
        self.docs_dir = root / "docs"
        for d in (self.pdfs_dir, self.docs_dir):
            d.mkdir(parents=True, exist_ok=True)

    def doc_dir(self, doc_id: str) -> Path:
        return self.docs_dir / doc_id

    def save_document(self, doc: Document) -> None:
        d = self.doc_dir(doc.id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "document.json").write_text(doc.model_dump_json(indent=2))

    def load_document(self, doc_id: str) -> Document | None:
        path = self.doc_dir(doc_id) / "document.json"
        if not path.exists():
            return None
        return Document.model_validate_json(path.read_text())

    def list_documents(self) -> list[Document]:
        docs = []
        for path in sorted(self.docs_dir.glob("*/document.json")):
            try:
                docs.append(Document.model_validate_json(path.read_text()))
            except Exception as e:
                log.warning("Failed to load %s: %s", path, e)
        return docs


def ingest_pdf(pdf_path: Path, library: Library, tts: OrpheusTTS) -> Document:
    log.info("Parsing %s", pdf_path)
    stored_pdf = library.pdfs_dir / pdf_path.name
    if pdf_path.resolve() != stored_pdf.resolve():
        stored_pdf.write_bytes(pdf_path.read_bytes())

    doc = parse_pdf(stored_pdf, library.docs_dir / "_tmp")
    target_dir = library.doc_dir(doc.id)
    target_dir.mkdir(parents=True, exist_ok=True)
    tmp_images = library.docs_dir / "_tmp" / "images"
    final_images = target_dir / "images"
    if tmp_images.exists():
        final_images.mkdir(exist_ok=True)
        for img in tmp_images.iterdir():
            target = final_images / img.name
            img.replace(target)
        tmp_images.rmdir()
        (library.docs_dir / "_tmp").rmdir()

    log.info("Synthesizing audio for %d blocks", len(doc.blocks))
    audio_dir = target_dir / "audio"
    audio_dir.mkdir(exist_ok=True)

    cumulative_ms = 0
    total_sentences = sum(len(b.sentences) for b in doc.blocks)
    done = 0
    for block in doc.blocks:
        for sent in block.sentences:
            if not sent.text.strip():
                continue
            audio_path = audio_dir / f"{sent.id}.wav"
            duration_s = tts.synthesize(sent.text, audio_path)
            sent.audio = f"audio/{audio_path.name}"
            sent.duration_ms = int(duration_s * 1000)
            sent.start_offset_ms = cumulative_ms
            cumulative_ms += sent.duration_ms
            done += 1
            if done % 5 == 0 or done == total_sentences:
                log.info("  %d/%d sentences synthesized", done, total_sentences)

    doc.total_duration_ms = cumulative_ms
    doc.voice = tts.voice
    library.save_document(doc)
    log.info("Done. doc_id=%s duration=%.1fs", doc.id, cumulative_ms / 1000)
    return doc
