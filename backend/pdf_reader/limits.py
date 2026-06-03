"""Per-user rate limits + storage caps.

Persistent JSON store at <data_root>/user_stats.json. Counters reset at UTC midnight.
Limits are env-tunable so we can raise them for invited testers without redeploying.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException

log = logging.getLogger(__name__)

# Default knobs, all overrideable per-deployment via env.
PDFS_PER_DAY = int(os.environ.get("PDF_READER_PDFS_PER_DAY", "5"))
MAX_STORAGE_MB = int(os.environ.get("PDF_READER_MAX_STORAGE_MB", "1024"))  # 1 GB / user
MAX_PDF_SIZE_MB = int(os.environ.get("PDF_READER_MAX_PDF_SIZE_MB", "30"))


class _StatsStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self._data: dict[str, dict] = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text())
            except Exception as e:
                log.warning("Could not read %s: %s — starting fresh", self.path, e)
                self._data = {}

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, default=str))
        tmp.replace(self.path)

    def _bucket(self, user_id: str) -> dict:
        b = self._data.setdefault(user_id, {})
        b.setdefault("pdfs_today", 0)
        b.setdefault("pdfs_today_reset_at", _midnight_utc().isoformat())
        b.setdefault("storage_bytes", 0)
        return b

    def get(self, user_id: str) -> dict:
        with self.lock:
            return dict(self._bucket(user_id))

    def reset_if_new_day(self, user_id: str) -> None:
        b = self._bucket(user_id)
        try:
            reset_at = datetime.fromisoformat(b["pdfs_today_reset_at"])
        except Exception:
            reset_at = _midnight_utc()
        if datetime.now(timezone.utc) >= reset_at:
            b["pdfs_today"] = 0
            b["pdfs_today_reset_at"] = _midnight_utc().isoformat()

    def increment_pdfs(self, user_id: str) -> None:
        with self.lock:
            self.reset_if_new_day(user_id)
            self._bucket(user_id)["pdfs_today"] += 1
            self._save()

    def set_storage(self, user_id: str, bytes_used: int) -> None:
        with self.lock:
            self._bucket(user_id)["storage_bytes"] = int(bytes_used)
            self._save()


_store: _StatsStore | None = None


def init(data_root: Path) -> None:
    global _store
    _store = _StatsStore(data_root / "user_stats.json")


def _midnight_utc() -> datetime:
    """Next UTC midnight."""
    now = datetime.now(timezone.utc)
    tomorrow = now.date() + timedelta(days=1)
    return datetime.combine(tomorrow, datetime.min.time(), tzinfo=timezone.utc)


def check_can_ingest(user_id: str, pdf_size_bytes: int, current_storage_bytes: int) -> None:
    """Raise HTTPException(429) if the user has hit per-day or per-storage limits."""
    if _store is None:
        return  # store not initialised, fail-open in dev
    if pdf_size_bytes > MAX_PDF_SIZE_MB * 1024 * 1024:
        raise HTTPException(
            413,
            f"PDF too large: {pdf_size_bytes / 1024 / 1024:.1f} MB exceeds {MAX_PDF_SIZE_MB} MB limit",
        )
    stats = _store.get(user_id)
    _store.reset_if_new_day(user_id)
    if stats["pdfs_today"] >= PDFS_PER_DAY:
        raise HTTPException(
            429,
            f"Daily upload limit reached ({PDFS_PER_DAY} PDFs / day). Try again tomorrow or donate to raise your limit.",
        )
    if current_storage_bytes + pdf_size_bytes > MAX_STORAGE_MB * 1024 * 1024:
        raise HTTPException(
            429,
            f"Storage cap reached ({MAX_STORAGE_MB} MB). Delete old documents or contact support.",
        )


def record_ingest(user_id: str, storage_bytes_after: int) -> None:
    if _store is None:
        return
    _store.increment_pdfs(user_id)
    _store.set_storage(user_id, storage_bytes_after)


def get_user_stats(user_id: str) -> dict:
    if _store is None:
        return {
            "user_id": user_id,
            "pdfs_today": 0,
            "pdfs_today_reset_at": _midnight_utc().isoformat(),
            "storage_bytes": 0,
            "pdfs_per_day_limit": PDFS_PER_DAY,
            "max_storage_mb": MAX_STORAGE_MB,
        }
    s = _store.get(user_id)
    s["user_id"] = user_id
    s["pdfs_per_day_limit"] = PDFS_PER_DAY
    s["max_storage_mb"] = MAX_STORAGE_MB
    return s
