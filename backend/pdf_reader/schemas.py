from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

BlockType = Literal["heading", "paragraph", "image", "list_item", "code", "equation", "table", "caption"]


class Sentence(BaseModel):
    id: str
    text: str  # display text — preserves inline $...$ math for KaTeX rendering
    tts_text: str | None = None  # cleaned text passed to TTS (no math). None → fall back to text.
    audio: str | None = None
    duration_ms: int = 0
    start_offset_ms: int = 0


class Block(BaseModel):
    id: str
    type: BlockType
    level: int = 0
    sentences: list[Sentence] = Field(default_factory=list)
    image_path: str | None = None
    caption: str | None = None
    raw: str | None = None
    latex: str | None = None  # for type=="equation"
    table_md: str | None = None  # for type=="table"
    list_marker: str | None = None  # for list_item: "1.", "2.", "•", etc.
    pause_at_ms: int | None = None  # for equation/table — playback time when this should pause


class Document(BaseModel):
    id: str
    title: str
    project: str = "default"
    user_id: str = "default"  # email of the owning user (verified by Cloudflare Access)
    abstract: str | None = None
    authors: list[str] = Field(default_factory=list)
    blocks: list[Block]
    total_duration_ms: int = 0
    voice: str = "tara"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    source_pdf: str | None = None
    markdown_file: str | None = None


class DocumentSummary(BaseModel):
    id: str
    title: str
    project: str
    user_id: str = "default"
    created_at: datetime
    total_duration_ms: int
    block_count: int


class Project(BaseModel):
    name: str
    user_id: str = "default"
    doc_count: int
    total_duration_ms: int
    created_at: datetime


class UserStats(BaseModel):
    """Per-user usage tracking for rate limits + storage caps."""
    user_id: str
    pdfs_today: int = 0
    pdfs_today_reset_at: datetime = Field(default_factory=datetime.utcnow)
    storage_bytes: int = 0
    project_count: int = 0
    doc_count: int = 0
