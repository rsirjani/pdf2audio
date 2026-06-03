"""SQLite-backed user + document + tip metadata.

Schema:
  users         — one row per signed-in account (email is the PK).
  documents    — one row per ingested PDF, links to users.email.
  tips          — record of tips received (Ko-fi webhook or manual import).
  usage_daily   — replaces user_stats.json; resets per UTC date.

Backwards-compatibility on first run:
  • If `<data_root>/user_stats.json` exists, its counters are copied into usage_daily
    and the file is renamed to user_stats.json.migrated.
  • If `<data_root>/users/<email>/projects/.../document.json` exists for any user not
    yet in the documents table, the row is inserted from the JSON.

All queries go through this module so future migrations are easier to reason about.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    email              TEXT PRIMARY KEY,
    display_name       TEXT,
    created_at         TEXT NOT NULL,
    last_seen_at       TEXT NOT NULL,
    total_tips_cents   INTEGER NOT NULL DEFAULT 0,
    tip_count          INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS documents (
    doc_id             TEXT NOT NULL,
    user_id            TEXT NOT NULL,
    project            TEXT NOT NULL,
    title              TEXT,
    created_at         TEXT NOT NULL,
    total_duration_ms  INTEGER NOT NULL DEFAULT 0,
    block_count        INTEGER NOT NULL DEFAULT 0,
    storage_bytes      INTEGER NOT NULL DEFAULT 0,
    source_filename    TEXT,
    PRIMARY KEY (user_id, doc_id),
    FOREIGN KEY (user_id) REFERENCES users(email) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_documents_user ON documents(user_id);
CREATE INDEX IF NOT EXISTS idx_documents_created ON documents(created_at);

CREATE TABLE IF NOT EXISTS tips (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT,
    amount_cents    INTEGER NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'CAD',
    source          TEXT NOT NULL,        -- 'ko-fi' | 'stripe' | 'manual'
    external_id     TEXT,                 -- provider-side unique id, dedupes webhook retries
    message         TEXT,
    occurred_at     TEXT NOT NULL,
    raw_payload     TEXT,                 -- original webhook JSON for forensics
    FOREIGN KEY (user_id) REFERENCES users(email) ON DELETE SET NULL,
    UNIQUE (source, external_id)
);

CREATE INDEX IF NOT EXISTS idx_tips_user ON tips(user_id);
CREATE INDEX IF NOT EXISTS idx_tips_occurred ON tips(occurred_at);

CREATE TABLE IF NOT EXISTS usage_daily (
    user_id          TEXT NOT NULL,
    day              TEXT NOT NULL,       -- YYYY-MM-DD (UTC)
    pdfs_processed   INTEGER NOT NULL DEFAULT 0,
    bytes_uploaded   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day),
    FOREIGN KEY (user_id) REFERENCES users(email) ON DELETE CASCADE
);
"""


class DB:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_DDL)
        self._conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('version', ?)",
            (str(SCHEMA_VERSION),),
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- Users ----

    def upsert_user(self, email: str, display_name: str | None = None) -> None:
        now = _utc_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO users(email, display_name, created_at, last_seen_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(email) DO UPDATE SET
                    last_seen_at = excluded.last_seen_at,
                    display_name = COALESCE(excluded.display_name, users.display_name)
                """,
                (email.lower(), display_name, now, now),
            )

    def get_user(self, email: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE email = ?", (email.lower(),)
            ).fetchone()
        return dict(row) if row else None

    # ---- Documents ----

    def upsert_document(
        self,
        *,
        user_id: str,
        doc_id: str,
        project: str,
        title: str | None,
        created_at: str,
        total_duration_ms: int,
        block_count: int,
        storage_bytes: int,
        source_filename: str | None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO documents(doc_id, user_id, project, title, created_at,
                                       total_duration_ms, block_count, storage_bytes, source_filename)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, doc_id) DO UPDATE SET
                    project = excluded.project,
                    title = excluded.title,
                    total_duration_ms = excluded.total_duration_ms,
                    block_count = excluded.block_count,
                    storage_bytes = excluded.storage_bytes,
                    source_filename = excluded.source_filename
                """,
                (doc_id, user_id.lower(), project, title, created_at,
                 total_duration_ms, block_count, storage_bytes, source_filename),
            )

    def list_user_documents(self, user_id: str, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM documents WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id.lower(), limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_document(self, user_id: str, doc_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM documents WHERE user_id = ? AND doc_id = ?",
                (user_id.lower(), doc_id),
            )

    # ---- Usage / rate limits ----

    def today_str(self) -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def get_today_usage(self, user_id: str) -> dict:
        day = self.today_str()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM usage_daily WHERE user_id = ? AND day = ?",
                (user_id.lower(), day),
            ).fetchone()
        if row:
            return dict(row)
        return {"user_id": user_id.lower(), "day": day, "pdfs_processed": 0, "bytes_uploaded": 0}

    def record_pdf(self, user_id: str, bytes_uploaded: int) -> None:
        day = self.today_str()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO usage_daily(user_id, day, pdfs_processed, bytes_uploaded)
                VALUES(?, ?, 1, ?)
                ON CONFLICT(user_id, day) DO UPDATE SET
                    pdfs_processed = pdfs_processed + 1,
                    bytes_uploaded = bytes_uploaded + excluded.bytes_uploaded
                """,
                (user_id.lower(), day, bytes_uploaded),
            )

    # ---- Tips ----

    def record_tip(
        self,
        *,
        user_id: str | None,
        amount_cents: int,
        currency: str,
        source: str,
        external_id: str | None,
        message: str | None,
        occurred_at: str | None = None,
        raw_payload: str | None = None,
    ) -> bool:
        """Insert a tip. Returns False if external_id is already recorded (idempotent webhook)."""
        if occurred_at is None:
            occurred_at = _utc_iso()
        user_lower = user_id.lower() if user_id else None
        try:
            with self._lock:
                self._conn.execute(
                    """
                    INSERT INTO tips(user_id, amount_cents, currency, source, external_id, message, occurred_at, raw_payload)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (user_lower, amount_cents, currency, source, external_id, message, occurred_at, raw_payload),
                )
                if user_lower:
                    self._conn.execute(
                        """
                        UPDATE users
                           SET total_tips_cents = total_tips_cents + ?,
                               tip_count = tip_count + 1
                         WHERE email = ?
                        """,
                        (amount_cents, user_lower),
                    )
        except sqlite3.IntegrityError as e:
            if "UNIQUE" in str(e):
                log.info("Tip already recorded (source=%s external_id=%s)", source, external_id)
                return False
            raise
        return True

    def list_user_tips(self, user_id: str, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, amount_cents, currency, source, message, occurred_at FROM tips "
                "WHERE user_id = ? ORDER BY occurred_at DESC LIMIT ?",
                (user_id.lower(), limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def total_tips_for_user(self, user_id: str) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT total_tips_cents, tip_count FROM users WHERE email = ?",
                (user_id.lower(),),
            ).fetchone()
        if not row:
            return {"total_tips_cents": 0, "tip_count": 0}
        return dict(row)

    def total_tips_overall(self) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(amount_cents), 0) AS cents, COUNT(*) AS n FROM tips"
            ).fetchone()
        return {"total_cents": row["cents"], "tip_count": row["n"]}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------- Migration helpers ----------------

def migrate_legacy_user_stats(db: DB, data_root: Path) -> None:
    """If <data_root>/user_stats.json exists, copy counters into usage_daily and users
    (existing data wins on conflict). Then rename the JSON file."""
    legacy = data_root / "user_stats.json"
    if not legacy.exists():
        return
    try:
        raw = json.loads(legacy.read_text())
    except Exception as e:
        log.warning("Could not read legacy user_stats.json: %s", e)
        return
    today = db.today_str()
    for user_id, bucket in raw.items():
        db.upsert_user(user_id)
        try:
            pdfs = int(bucket.get("pdfs_today", 0))
        except Exception:
            pdfs = 0
        if pdfs > 0:
            # one-off update — overwrite today's row from JSON values
            with db._lock:
                db._conn.execute(
                    """
                    INSERT INTO usage_daily(user_id, day, pdfs_processed, bytes_uploaded)
                    VALUES(?, ?, ?, ?)
                    ON CONFLICT(user_id, day) DO UPDATE SET
                        pdfs_processed = MAX(pdfs_processed, excluded.pdfs_processed),
                        bytes_uploaded = MAX(bytes_uploaded, excluded.bytes_uploaded)
                    """,
                    (user_id.lower(), today, pdfs, int(bucket.get("storage_bytes", 0))),
                )
    legacy.rename(legacy.with_suffix(".json.migrated"))
    log.info("Migrated legacy user_stats.json into DB")


def backfill_documents_from_filesystem(db: DB, data_root: Path) -> int:
    """Walk users/<email>/projects/.../document.json and insert any missing rows."""
    users_dir = data_root / "users"
    if not users_dir.exists():
        return 0
    n = 0
    for doc_json in users_dir.rglob("document.json"):
        try:
            d = json.loads(doc_json.read_text())
        except Exception:
            continue
        doc_id = d.get("id")
        user_id = d.get("user_id", "default")
        if not doc_id:
            continue
        db.upsert_user(user_id)
        # Compute storage from the doc's directory size.
        doc_dir = doc_json.parent
        storage = sum(
            (p.stat().st_size for p in doc_dir.rglob("*") if p.is_file()),
            start=0,
        )
        db.upsert_document(
            user_id=user_id,
            doc_id=doc_id,
            project=d.get("project") or "default",
            title=d.get("title"),
            created_at=d.get("created_at") or _utc_iso(),
            total_duration_ms=int(d.get("total_duration_ms") or 0),
            block_count=len(d.get("blocks") or []),
            storage_bytes=storage,
            source_filename=d.get("source_pdf"),
        )
        n += 1
    return n
