"""Entry point. Honors PDF_READER_HOST/PDF_READER_PORT env vars."""
import logging
import os

import uvicorn

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

if __name__ == "__main__":
    uvicorn.run(
        "pdf_reader.server:app",
        host=os.environ.get("PDF_READER_HOST", "0.0.0.0"),
        port=int(os.environ.get("PDF_READER_PORT", "8000")),
        reload=False,
    )
