"""Library + storage helpers — no TTS/parser deps so serve-only deployments can import this.

Layout:
    <root>/
      users/
        <user_id>/
          projects/
            <project>/
              <doc_id>/
                document.json
                audio/...
                images/...
                source.pdf
                audiobook.mp3

`user_id` is the user's verified email (from Cloudflare Access JWT). The default
user `default` is used by the legacy single-tenant deployment and the migration
helper promotes the legacy `projects/` tree to `users/default/projects/`.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from .schemas import Document

log = logging.getLogger(__name__)

DEFAULT_USER = "default"


def _safe_name(s: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in s)
    return safe.strip("._") or "default"


def _safe_user(user_id: str | None) -> str:
    """Email-safe user-id slug. `a@b.com` → `a@b.com` (kept), `User Smith` → `User_Smith`."""
    if not user_id:
        return DEFAULT_USER
    return "".join(c if c.isalnum() or c in "-_.@" else "_" for c in user_id).strip("._") or DEFAULT_USER


class Library:
    def __init__(self, root: Path):
        self.root = root
        self.users_dir = root / "users"
        self.users_dir.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_layout()

    def _migrate_legacy_layout(self) -> None:
        """If a legacy `<root>/projects/` directory exists (pre-multi-tenant deployments),
        move it under `users/default/projects/` so old data stays accessible."""
        legacy = self.root / "projects"
        if not legacy.exists() or not legacy.is_dir():
            return
        if any(legacy.iterdir()):
            target = self.users_dir / DEFAULT_USER / "projects"
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                # Both old and new exist — merge: move each project dir individually.
                for proj in legacy.iterdir():
                    dest = target / proj.name
                    if dest.exists():
                        log.warning("Skipping legacy %s — destination exists", proj)
                        continue
                    shutil.move(str(proj), str(dest))
            else:
                shutil.move(str(legacy), str(target))
                log.info("Migrated legacy projects/ → users/%s/projects/", DEFAULT_USER)
        try:
            legacy.rmdir()
        except OSError:
            pass

    # ---- Paths ----

    def user_dir(self, user_id: str) -> Path:
        p = self.users_dir / _safe_user(user_id)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def projects_dir(self, user_id: str) -> Path:
        p = self.user_dir(user_id) / "projects"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def project_dir(self, user_id: str, project: str) -> Path:
        p = self.projects_dir(user_id) / _safe_name(project)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def doc_dir(self, user_id: str, project: str, doc_id: str) -> Path:
        return self.project_dir(user_id, project) / doc_id

    # ---- I/O ----

    def save_document(self, doc: Document) -> None:
        d = self.doc_dir(doc.user_id or DEFAULT_USER, doc.project, doc.id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "document.json").write_text(doc.model_dump_json(indent=2))

    def load_document(self, user_id: str, project: str, doc_id: str) -> Document | None:
        path = self.doc_dir(user_id, project, doc_id) / "document.json"
        if not path.exists():
            return None
        return Document.model_validate_json(path.read_text())

    def list_users(self) -> list[str]:
        if not self.users_dir.exists():
            return []
        return sorted(d.name for d in self.users_dir.iterdir() if d.is_dir())

    def list_projects(self, user_id: str) -> list[str]:
        d = self.projects_dir(user_id)
        if not d.exists():
            return []
        return sorted(p.name for p in d.iterdir() if p.is_dir())

    def list_documents(self, user_id: str, project: str | None = None) -> list[Document]:
        docs: list[Document] = []
        projects = [project] if project else self.list_projects(user_id)
        for p in projects:
            pdir = self.project_dir(user_id, p)
            for doc_path in sorted(pdir.glob("*/document.json")):
                try:
                    docs.append(Document.model_validate_json(doc_path.read_text()))
                except Exception as e:
                    log.warning("Failed to load %s: %s", doc_path, e)
        return docs

    # ---- Per-user usage ----

    def user_storage_bytes(self, user_id: str) -> int:
        total = 0
        for f in self.user_dir(user_id).rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
        return total
