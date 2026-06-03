"""Subprocess entrypoint: parses a PDF and writes document.json + document.md.

Runs in a fresh Python process so Marker's torch imports aren't poisoned by
vLLM's CUDA-initialized torch in the main backend process.

Usage:
    python -m pdf_reader.parse_in_proc <pdf_path> <output_dir> <project>
"""
import os
import sys
from pathlib import Path

# Subprocess uses GPU by default — when invoked by the batch worker, vLLM is
# unloaded so Marker gets the full GPU. Set PDF_READER_MARKER_DEVICE=cpu to override.
os.environ.setdefault("TORCH_DEVICE", os.environ.get("PDF_READER_MARKER_DEVICE", "cuda"))


def main():
    if len(sys.argv) < 4:
        print("usage: parse_in_proc <pdf_path> <output_dir> <project>", file=sys.stderr)
        sys.exit(2)
    pdf_path = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])
    project = sys.argv[3]

    output_dir.mkdir(parents=True, exist_ok=True)

    from .parser import parse_pdf
    doc, markdown = parse_pdf(pdf_path, output_dir, project=project)

    (output_dir / "document.json").write_text(doc.model_dump_json(indent=2))
    (output_dir / "document.md").write_text(markdown)
    print(f"OK doc_id={doc.id} blocks={len(doc.blocks)} sentences={sum(len(b.sentences) for b in doc.blocks)}")


if __name__ == "__main__":
    main()
