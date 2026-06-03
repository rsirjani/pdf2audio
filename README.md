# pdf2audio

Turn academic PDFs into structured reading + high-quality follow-along audio.

Live service: **[pdf2audio.ca](https://pdf2audio.ca)** — operated by [Ramtin Sirjani](mailto:ramtin.sirjani@gmail.com), PhD student at Western University. Built on personal GPU hardware. Donations: [Ko-fi](https://ko-fi.com/REPLACE-ME).

## What it does

You upload a PDF, the pipeline:

1. **Parses** the PDF with [Marker](https://github.com/datalab-to/marker) into structured blocks (headings, paragraphs, equations, tables, images, lists, captions).
2. **Cleans front matter** — drops author affiliations, license footers, "Proceedings of…", duplicate title + abstract, etc., so what gets read aloud is the actual paper body.
3. **Splits into sentences** with [pysbd](https://github.com/nipunsadvilkar/pySBD).
4. **Narrates each sentence** with [Orpheus-3B TTS](https://github.com/canopyai/Orpheus-TTS) on vLLM, decoded through SNAC at 24 kHz.
5. **Serves** a PWA-installable reader (web + iOS + Android) with KaTeX math, image rendering, auto-pause on equations, sentence-level scrubbing, and offline-MP3 download.

## Architecture

```
                  ┌───────────────────────────┐
                  │  pdf2audio.ca (public)    │
                  └──────────────┬────────────┘
                                 │ Cloudflare Access (Google sign-in)
                  ┌──────────────▼────────────┐
                  │  Cloudflare Tunnel        │
                  └──────────────┬────────────┘
                                 │ inside WSL2
                  ┌──────────────▼────────────┐
                  │  FastAPI backend          │
                  │  + Marker (Parse)         │
                  │  + vLLM Orpheus (TTS)     │
                  │  on RTX 5090              │
                  └──────────────┬────────────┘
                                 │ tar over SSH after each ingest
                  ┌──────────────▼────────────┐
                  │  UGREEN NAS               │
                  │  serve-only Docker        │
                  │  data: users/<email>/...  │
                  └───────────────────────────┘
```

## Repository layout

```
backend/                FastAPI app, Marker pipeline, Orpheus TTS
  pdf_reader/
    auth.py             Cloudflare Access JWT verification
    library.py          Per-user storage layout
    limits.py           Rate limits + storage caps
    parser.py           PDF → structured Document (Marker wrapper + front-matter cleanup)
    pipeline_vllm.py    Parse + TTS phases, NAS sync
    schemas.py          Pydantic models (Document, Block, Sentence, ...)
    server.py           FastAPI endpoints
    tts_vllm.py         vLLM-driven Orpheus TTS
  Dockerfile.serve      Serve-only image (no TTS, runs on the NAS)
frontend/               React + Vite PWA
LICENSE                 AGPL-3.0
```

## Running locally

```bash
# Backend
cd backend
uv sync
PDF_READER_DEV_USER=you@example.com uv run python main.py

# Frontend
cd frontend
npm install
npm run dev
```

`PDF_READER_DEV_USER` bypasses Cloudflare Access JWT verification for local dev.
In production, set `PDF_READER_CF_TEAM` and `PDF_READER_CF_AUD` instead.

### Environment variables

| Var | Default | Notes |
|---|---|---|
| `PDF_READER_DATA` | `~/pdf-reader-data` | Per-user data root |
| `PDF_READER_CF_TEAM` | (unset) | Cloudflare Access team slug |
| `PDF_READER_CF_AUD` | (unset) | Access application audience tag |
| `PDF_READER_DEV_USER` | (unset) | Bypass JWT, use this as user_id — dev only |
| `PDF_READER_PDFS_PER_DAY` | `5` | Per-user daily upload cap |
| `PDF_READER_MAX_STORAGE_MB` | `1024` | Per-user storage cap |
| `PDF_READER_MAX_PDF_SIZE_MB` | `30` | Single-file upload cap |
| `PDF_READER_NAS_TARGET` | (unset) | SSH target to sync each finished doc to |
| `PDF_READER_GPU_UTIL` | `0.55` | vLLM `gpu_memory_utilization` |
| `PDF_READER_LOAD_TTS` | `1` | Set to `0` for serve-only deployment |

## License

[AGPL-3.0](LICENSE). If you host this code as a service, you must keep your changes open. See [LICENSE](LICENSE) for full text.

## Contributing

Issues and PRs welcome. This is a personal project so response time varies — bear with me. For abuse reports or takedown requests, please email instead.

## Acknowledgments

- [Marker](https://github.com/datalab-to/marker) for PDF → markdown
- [Orpheus-TTS](https://github.com/canopyai/Orpheus-TTS) for high-fidelity narration
- [SNAC](https://github.com/hubertsiuzdak/snac) for the 24 kHz neural decoder
- Catppuccin for the color palette
