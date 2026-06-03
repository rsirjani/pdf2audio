from __future__ import annotations

import hashlib
import logging
import re
import shutil
from pathlib import Path

from .schemas import Block, Document, Sentence

log = logging.getLogger(__name__)


_SENTENCE_RE = re.compile(
    r"(?<=[.!?])\s+(?=[A-Z\"'(\[])"
)
_ABBREV = {"Fig.", "Eq.", "Ref.", "vs.", "e.g.", "i.e.", "et al.", "cf.", "Dr.", "Mr.", "Mrs.", "Ms.", "Prof.", "St.", "etc.", "approx.", "vol.", "no.", "pp."}


def _split_sentences(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    for abbr in _ABBREV:
        text = text.replace(abbr, abbr.replace(".", "\x00"))
    parts = _SENTENCE_RE.split(text)
    return [p.replace("\x00", ".").strip() for p in parts if p.strip()]


# Parenthetical citation lists (multiple cited works separated by ";") — clearly bibliographic clutter
_CITATION_LIST_RE = re.compile(
    r"\s*\((?=[^()]*?(?:19|20)\d{2})[^()]*?;[^()]*?\)"
)
# Citation at end of sentence (followed by ./,/;/:/!/?). Doesn't eat ones mid-sentence.
_CITATION_AT_END_RE = re.compile(
    r"\s*\((?:[^()]*?\b(?:19|20)\d{2}[a-z]?\b[^()]*?)\)(?=\s*[.,;:!?])"
)
# Numbered bracket refs [3] or [3, 7] or [3-7] — bibliography style, always safe to drop
_CITATION_BRACKETS_RE = re.compile(r"\[\s*\d+(?:\s*,\s*\d+)*(?:\s*[-–]\s*\d+)?\s*\]")
# Orphan equation labels like "(1)" — strip unless preceded by "Equation" / "Eq." (keeps reference readable)
_ORPHAN_EQ_LABEL_RE = re.compile(
    r"(?<!Equation\s)(?<!Eq\.\s)(?<!Eq\s)(?<!equation\s)\(\d+\)"
)
_SKIP_LINE_RE = re.compile(
    r"^\s*(?:©|copyright\b|all rights reserved\b|arxiv:\s*\d|doi:\s*10\.|"
    r"preprint\.\s*under review|under review at|accepted at|preprint submitted)",
    re.IGNORECASE,
)
# Front-matter venue / license / proceedings footers Marker tends to pull into the body.
_VENUE_BOILERPLATE_RE = re.compile(
    r"(?ix)\b(?:"
    r"this\s+work\s+is\s+licensed\s+under"
    r"|proceedings\s+of\s+the\b[^.]{0,80}\b(?:endowment|conference|workshop|symposium|society|association|ieee|acm)"
    r"|issn[\s:]+\d"
    r"|isbn[\s:]+[\d-]"
    r"|copyright\s+(?:is\s+)?held\s+by"
    r"|published\s+(?:by\s+)?(?:the\s+)?(?:acm|ieee|usenix|association)"
    r"|permission\s+to\s+make\s+digital\s+or\s+hard\s+copies"
    r"|to\s+copy\s+otherwise"
    r"|conference\s+acronym\s+['\"]?\d"
    r"|preprint\s+(?:submitted|under\s+review)"
    r"|publication\s+rights\s+licensed"
    r")",
)
# Heading lines that mark front-matter boilerplate sections (the body paragraph after them is dropped too).
_VENUE_HEADING_RE = re.compile(
    r"(?ix)^\s*(?:(?:vldb|acm|ieee)\s+)?(?:"
    r"workshop\s+(?:reference\s+format|artifact\s+availability)"
    r"|reference\s+format|artifact\s+availability"
    r"|ccs\s+concepts"
    r"|additional\s+key\s+words"
    r"|authors[''’]\s+addresses"
    r")\b"
)
_EMAIL_IN_TEXT_RE = re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+")
_AFFILIATION_KEYWORDS = (
    "university", "institute", "college", "laboratory", " labs",
    "department of", "school of", "research center", "research lab",
    "microsoft", "google", "meta ai", "openai", "deepmind", "anthropic",
    "ibm research", "amazon", "nvidia",
)
# IEEE-style "Abstract—..." or ACM "ABSTRACT—..." inline abstract (no separate heading).
_INLINE_ABSTRACT_RE = re.compile(r"^\s*abstract\s*[—–\-:]\s*(.+)$", re.I | re.S)
# "Index Terms—..." (IEEE keywords) or "Keywords:..." block.
_INDEX_TERMS_RE = re.compile(r"^\s*(?:index\s+terms|keywords|key\s+words)\s*[—–\-:]\s", re.I)
# First "real" section heading, used to mark end of front matter.
_FIRST_SECTION_RE = re.compile(
    r"(?ix)^\s*(?:(?:\d+(?:\.\d+)*|[IVX]+)[.\s]\s*)?"
    r"(introduction|background|related\s+work|preliminaries|motivation|"
    r"methodology|methods?|problem\s+(?:statement|setup)|approach|overview)\b"
)


def _is_boilerplate(text: str) -> bool:
    """Heuristic: copyright notices, arXiv/DOI badges, license footers, venue/ISSN/proceedings strings."""
    s = text.strip()
    if not s:
        return True
    if _SKIP_LINE_RE.match(s):
        return True
    if _VENUE_BOILERPLATE_RE.search(s):
        return True
    if "rights reserved" in s.lower():
        return True
    return False


def _looks_like_affiliation(text: str) -> bool:
    """Author/affiliation block: has email, or short text with university/institute keyword."""
    if not text:
        return False
    has_email = bool(_EMAIL_IN_TEXT_RE.search(text))
    has_kw = any(k in text.lower() for k in _AFFILIATION_KEYWORDS)
    return has_email or (has_kw and len(text) < 220)


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_ENTITIES = {
    "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'",
    "&nbsp;": " ", "&mdash;": "—", "&ndash;": "–", "&hellip;": "…",
    "&times;": "×", "&divide;": "÷", "&minus;": "−", "&plusmn;": "±",
}


# Any single parenthetical citation containing a year — used to strip from TTS aggressively
_CITATION_ANY_RE = re.compile(r"\s*\([^()]*?\b(?:19|20)\d{2}[a-z]?\b[^()]*?\)")
# LaTeX inline math delimiters \(...\) and \[...\]
_LATEX_INLINE_RE = re.compile(r"\\\((.*?)\\\)", flags=re.DOTALL)
_LATEX_DISPLAY_RE = re.compile(r"\\\[.+?\\\]", flags=re.DOTALL)
# LaTeX spacing commands that leak as visible artifacts
_LATEX_SPACE_RE = re.compile(r"\\[,;:!]|\\quad\b|\\qquad\b|\\hspace\{[^}]*\}")
# Heuristic: does this look like actual math or just a citation/paren clipped by \(...\)
_MATH_SIGNAL_RE = re.compile(
    r"\\[A-Za-z]+|[\^_]|[αβγδεζηθλμνξπρστφχψω∑∏∫∂∇±≤≥≠≈⊂⊃∈∉⊕⊗√∞]"
)


def _looks_like_math(s: str) -> bool:
    return bool(_MATH_SIGNAL_RE.search(s))


def _clean_for_display(text: str) -> str:
    """Display-side cleanup: keep $...$ math, strip HTML/markdown wrappers and citation lists."""
    # Block-level math wrappers (those are now their own blocks)
    text = re.sub(r"\$\$.+?\$\$", " ", text, flags=re.DOTALL)
    text = _LATEX_DISPLAY_RE.sub(" ", text)
    # LaTeX inline math \(...\) — only wrap as $...$ if content is actually math.
    def _inline_math_swap(m: re.Match) -> str:
        inner = m.group(1).strip()
        if not inner:
            return ""
        if _looks_like_math(inner):
            return f"${inner}$"
        return f"({inner})"
    text = _LATEX_INLINE_RE.sub(_inline_math_swap, text)
    # LaTeX spacing commands
    text = _LATEX_SPACE_RE.sub("", text)
    # Inline HTML: handle <sup>...</sup> footnote markers specially before generic strip
    text = re.sub(r"<sup>\s*\d+\s*</sup>", " ", text)  # numeric footnote refs → drop
    text = _HTML_TAG_RE.sub("", text)
    for ent, ch in _HTML_ENTITIES.items():
        text = text.replace(ent, ch)
    text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)
    text = re.sub(r"&#x([0-9a-fA-F]+);", lambda m: chr(int(m.group(1), 16)), text)
    # Stash $...$ math so markdown emphasis stripping doesn't eat asterisks inside math (e.g. \omega^*)
    math_stash: list[str] = []

    def _stash(m: re.Match) -> str:
        math_stash.append(m.group(0))
        return f"\x00M{len(math_stash) - 1}\x00"
    text = re.sub(r"\$[^$\n]{1,500}\$", _stash, text)
    # Markdown emphasis / code / links — safe now that math is stashed
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    # Restore math
    text = re.sub(r"\x00M(\d+)\x00", lambda m: math_stash[int(m.group(1))], text)
    # Citations
    text = _CITATION_LIST_RE.sub("", text)
    text = _CITATION_AT_END_RE.sub("", text)
    text = _CITATION_BRACKETS_RE.sub("", text)
    text = _ORPHAN_EQ_LABEL_RE.sub(" ", text)
    # Strip lone backslashes followed by punctuation (Marker OCR artifacts)
    text = re.sub(r"\\([.,;:!?()])", r"\1", text)
    text = re.sub(r"\s+([.,;:])", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _tts_from_display(text: str) -> str:
    """TTS-side cleanup: strip everything Orpheus shouldn't read aloud.

    Display already removed lists/end-of-sentence citations. TTS is more
    aggressive: also strip *any* inline parenthetical citation (e.g. 'in
    (Smith 2024) they propose' → 'in they propose'), all $...$ math, and any
    leftover backslash artifacts.
    """
    text = re.sub(r"\$[^$]+\$", " ", text)
    text = _CITATION_ANY_RE.sub("", text)
    # Drop any remaining LaTeX-looking commands
    text = re.sub(r"\\[A-Za-z]+\{[^}]*\}", " ", text)
    text = re.sub(r"\\[A-Za-z]+", " ", text)
    text = re.sub(r"\\.", " ", text)
    text = re.sub(r"\s+([.,;:])", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# Backwards-compat alias used elsewhere
def _clean_for_tts(text: str) -> str:
    return _tts_from_display(_clean_for_display(text))


def _doc_id(pdf_path: Path) -> str:
    h = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    return h[:16]


def parse_pdf(pdf_path: Path, output_dir: Path, project: str = "default") -> tuple[Document, str]:
    """Parse PDF into structured Document. Returns (doc, markdown_text)."""
    import os
    # Marker can run on GPU here because parse_in_proc is a fresh subprocess (no vLLM in scope).
    os.environ.setdefault("TORCH_DEVICE", os.environ.get("PDF_READER_MARKER_DEVICE", "cuda"))
    from marker.converters.pdf import PdfConverter
    from marker.models import create_model_dict
    from marker.output import text_from_rendered

    output_dir.mkdir(parents=True, exist_ok=True)
    images_dir = output_dir / "images"
    images_dir.mkdir(exist_ok=True)

    # OCR ON: catches inline math (subscripts, superscripts, Greek letters) as $...$ in markdown.
    config = {"disable_ocr": False}
    converter = PdfConverter(artifact_dict=create_model_dict(), config=config)
    rendered = converter(str(pdf_path))
    markdown, _, images = text_from_rendered(rendered)

    for name, img in images.items():
        safe = name.replace("/", "_").replace("\\", "_")
        target = images_dir / safe
        try:
            img.save(target)
        except Exception as e:
            log.warning("failed to save image %s: %s", name, e)

    doc = _markdown_to_document(markdown, pdf_path, output_dir)
    doc.project = project
    return doc, markdown


def _cuda_available() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


_REFS_HEADING_RE = re.compile(
    r"^\s*(references|bibliography|works cited|citations)\s*$",
    re.IGNORECASE,
)


def _markdown_to_document(markdown: str, pdf_path: Path, output_dir: Path) -> Document:
    lines = markdown.split("\n")
    blocks: list[Block] = []
    title: str | None = None
    abstract: str | None = None
    buffer: list[str] = []
    block_counter = 0
    in_refs_section = False

    def flush_paragraph():
        nonlocal block_counter, abstract
        if not buffer:
            return
        text = " ".join(buffer).strip()
        buffer.clear()
        if not text:
            return
        if _is_boilerplate(text):
            return
        display = _clean_for_display(text)
        if not display or len(display) < 5:
            return
        if re.fullmatch(r"[\d\s\(\)\[\]\.\-+=,;:]+", display):
            return
        # If this paragraph is "Figure N: ..." or "Fig. N: ..." and the previous block
        # is an uncaptioned image, attach as caption instead of making a new paragraph.
        fig_match = re.match(r"^\s*(?:Figure|Fig\.?)\s*\d+[.:\s]\s*(.+)$", display, re.IGNORECASE)
        if fig_match and blocks and blocks[-1].type == "image" and not blocks[-1].caption:
            blocks[-1].caption = display
            return
        sentences = _split_sentences(display)
        if not sentences:
            return
        block_id = f"b{block_counter}"
        block_counter += 1
        sentence_objs = []
        for i, s in enumerate(sentences):
            tts = _tts_from_display(s)
            sentence_objs.append(Sentence(
                id=f"{block_id}_s{i}",
                text=s,
                tts_text=tts if tts != s else None,
            ))
        block = Block(
            id=block_id,
            type="paragraph",
            sentences=sentence_objs,
            raw=text,
        )
        blocks.append(block)
        if abstract is None and any(
            "abstract" in b.raw.lower()[:30] if b.raw else False for b in blocks[-2:-1]
        ):
            abstract = text

    in_table = False
    table_lines: list[str] = []
    in_eq = False
    eq_lines: list[str] = []
    expect_eq_label = False  # if True, the next "(N)" line is treated as an equation tag
    list_run_next_num = 0  # >0 while in a numeric list run; lets us auto-number stray bullets

    def flush_table():
        nonlocal block_counter, in_table, table_lines
        if not table_lines:
            in_table = False
            return
        md = "\n".join(table_lines)
        table_lines = []
        in_table = False
        block_id = f"b{block_counter}"
        block_counter += 1
        blocks.append(Block(id=block_id, type="table", table_md=md))

    def flush_eq():
        nonlocal block_counter, in_eq, eq_lines, expect_eq_label
        if not eq_lines:
            in_eq = False
            return
        latex = "\n".join(eq_lines).strip()
        eq_lines = []
        in_eq = False
        # If the latex already contains a \tag, don't double-up
        if not re.search(r"\\tag\b", latex):
            expect_eq_label = True
        block_id = f"b{block_counter}"
        block_counter += 1
        blocks.append(Block(id=block_id, type="equation", latex=latex))

    _ANCHOR_SPAN_RE = re.compile(r"^\s*(?:<span[^>]*>\s*</span>\s*)+")
    for raw_line in lines:
        line = raw_line.rstrip()
        # Strip leading anchor spans like <span id="page-X"></span> that appear before
        # images, headings, and equations — they prevent block-level regex matches.
        line = _ANCHOR_SPAN_RE.sub("", line)
        stripped = line.strip()

        # Inside an equation block: gather until closing $$
        if in_eq:
            if stripped.endswith("$$"):
                inner = stripped[:-2].strip()
                if inner:
                    eq_lines.append(inner)
                flush_eq()
            else:
                eq_lines.append(line)
            continue

        if not stripped:
            flush_paragraph()
            if in_table:
                flush_table()
            continue

        # Eq label like "(2)" right after an equation block — attach as \tag
        if expect_eq_label:
            m = re.match(r"^\((\d+[a-z]?)\)\s*$", stripped)
            if m and blocks and blocks[-1].type == "equation":
                blocks[-1].latex = f"{blocks[-1].latex} \\tag{{{m.group(1)}}}"
                expect_eq_label = False
                continue
            expect_eq_label = False

        # Display-math equation: $$ ... $$ on its own (single or multi-line)
        if stripped.startswith("$$"):
            flush_paragraph()
            if in_table:
                flush_table()
            # Single-line: $$<content>$$ optionally followed by  (label)
            single = re.match(r"^\$\$(.+?)\$\$\s*(?:\((\d+[a-z]?)\))?\s*$", stripped)
            if single:
                content = single.group(1).strip()
                label = single.group(2)
                eq_lines.append(content)
                flush_eq()
                if label and blocks and blocks[-1].type == "equation":
                    blocks[-1].latex = f"{blocks[-1].latex} \\tag{{{label}}}"
                    expect_eq_label = False
                continue
            # Multi-line: collect until $$
            inner = stripped[2:]
            in_eq = True
            if inner.strip():
                eq_lines.append(inner)
            continue

        # Markdown table row
        if stripped.startswith("|") and stripped.count("|") >= 2:
            flush_paragraph()
            in_table = True
            table_lines.append(stripped)
            continue
        if in_table:
            # blank or non-pipe line ends the table — handled in blank-line branch above
            flush_table()

        # Numbered or bulleted list item — split each item into its own paragraph block
        list_match = re.match(r"^(\s*)([-*•]|\d+[.)])\s+(.+)$", line)
        if list_match:
            flush_paragraph()
            if in_refs_section:
                continue
            marker = list_match.group(2)
            content = list_match.group(3).strip()
            # Marker is generic bullet but content begins with "N." — promote the number
            if marker in ("-", "*", "•"):
                nested = re.match(r"^(\d+[.)])\s+(.+)$", content)
                if nested:
                    marker = nested.group(1)
                    content = nested.group(2).strip()
            # Track / auto-fill numeric run.  "1." opens it, "- " inside continues it.
            num_match = re.match(r"^(\d+)([.)])$", marker)
            if num_match:
                list_run_next_num = int(num_match.group(1)) + 1
            elif marker in ("-", "*", "•") and list_run_next_num > 0:
                marker = f"{list_run_next_num}."
                list_run_next_num += 1
            block_id = f"b{block_counter}"
            block_counter += 1
            cleaned = _clean_for_tts(content)
            if cleaned and len(cleaned) >= 3:
                sentences = _split_sentences(cleaned) or [cleaned]
                sentence_objs = [
                    Sentence(id=f"{block_id}_s{i}", text=s) for i, s in enumerate(sentences)
                ]
                blocks.append(Block(
                    id=block_id,
                    type="list_item",
                    sentences=sentence_objs,
                    raw=content,
                    list_marker=marker,
                ))
            continue

        heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading_match:
            flush_paragraph()
            list_run_next_num = 0
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()
            if _REFS_HEADING_RE.match(heading_text):
                in_refs_section = True
                continue
            in_refs_section = False
            # Title detection: take the first heading that looks like a real paper title.
            # Strip HTML, reject obvious section headings ("1 Introduction", "3.3 X", "Abstract").
            if title is None and level <= 2:
                clean = _HTML_TAG_RE.sub("", heading_text).strip()
                clean = re.sub(r"^\\\*+", "", clean).strip()
                is_section = bool(re.match(r"^\d+(?:\.\d+)*\s+\S", clean))
                is_meta = clean.lower() in {"abstract", "introduction", "related work", "background", "conclusion", "references"}
                if clean and len(clean) >= 10 and not is_section and not is_meta:
                    title = clean
            block_id = f"b{block_counter}"
            block_counter += 1
            cleaned = _clean_for_tts(heading_text)
            blocks.append(Block(
                id=block_id,
                type="heading",
                level=level,
                sentences=[Sentence(id=f"{block_id}_s0", text=cleaned)] if cleaned else [],
                raw=heading_text,
            ))
            continue

        image_match = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$", line.strip())
        if image_match:
            flush_paragraph()
            if in_refs_section:
                continue
            alt = image_match.group(1)
            path = image_match.group(2)
            block_id = f"b{block_counter}"
            block_counter += 1
            blocks.append(Block(
                id=block_id,
                type="image",
                image_path=path,
                caption=alt or None,
            ))
            continue

        if in_refs_section:
            continue

        list_run_next_num = 0  # any non-list, non-blank line ends a numeric list run
        buffer.append(line.strip())

    flush_paragraph()

    if title is None:
        title = pdf_path.stem

    # ---- Front-matter cleanup: strip blocks that duplicate header (doc.title / doc.abstract)
    # or that are author/affiliation/venue-format metadata. Frontend renders title+abstract
    # in the header, so leaving them as blocks shows them twice and reads them aloud.
    drop: set[int] = set()

    # Title heading: drop the (first) heading whose text equals doc.title.
    if title:
        for i, b in enumerate(blocks):
            if b.type == "heading" and b.raw:
                clean = _HTML_TAG_RE.sub("", b.raw).strip()
                if clean == title:
                    drop.add(i)
                    break

    # Abstract heading + the paragraph(s) directly under it.
    for i, b in enumerate(blocks):
        if b.type == "heading" and b.raw and re.fullmatch(r"\s*abstract\s*", b.raw.strip(), re.I):
            drop.add(i)
            for j in range(i + 1, min(i + 4, len(blocks))):
                if blocks[j].type == "paragraph" and blocks[j].raw:
                    if abstract is None:
                        abstract = blocks[j].raw
                    drop.add(j)
                    break
                if blocks[j].type == "heading":
                    break
            break

    # Venue-format / artifact-availability heading + the paragraph under it.
    for i, b in enumerate(blocks):
        if b.type == "heading" and b.raw and _VENUE_HEADING_RE.match(b.raw):
            drop.add(i)
            for j in range(i + 1, min(i + 3, len(blocks))):
                if blocks[j].type == "paragraph":
                    drop.add(j)
                    break
                if blocks[j].type == "heading":
                    break

    # IEEE-style inline abstract: paragraph starts with "Abstract—...". Extract + drop.
    for i, b in enumerate(blocks[:30]):
        if b.type == "paragraph" and b.raw:
            m = _INLINE_ABSTRACT_RE.match(b.raw)
            if m:
                if abstract is None:
                    abstract = m.group(1).strip()
                drop.add(i)
                break

    # Index Terms / Keywords block.
    for i, b in enumerate(blocks[:30]):
        if b.type == "paragraph" and b.raw and _INDEX_TERMS_RE.match(b.raw):
            drop.add(i)

    # Catch-all: drop every paragraph that appears BEFORE the first real section heading
    # ("1 Introduction" / "Introduction" / "Background" / etc.). Catches multi-author bylines,
    # affiliations without keywords, CCS/Index Terms continuations, and the author footnote line.
    # BUT: if no abstract has been extracted yet, promote the longest substantive pre-section
    # paragraph into doc.abstract first — handles papers (e.g. ACM TOG) with no "Abstract"
    # heading and no "Abstract—" inline marker.
    first_section = None
    for i, b in enumerate(blocks):
        if b.type == "heading" and b.raw and _FIRST_SECTION_RE.match(b.raw.strip()):
            first_section = i
            break
    if first_section is not None:
        pre_paragraphs = [
            (i, blocks[i]) for i in range(first_section)
            if blocks[i].type == "paragraph" and blocks[i].raw
        ]
        if abstract is None and pre_paragraphs:
            cands = [
                (i, b) for i, b in pre_paragraphs
                if not _looks_like_affiliation(b.raw)
                and not _INDEX_TERMS_RE.match(b.raw)
                and not re.match(r"^\s*(?:ACM|ISBN|DOI|https?://|<sup>)", b.raw)
                and len(b.raw) > 100
            ]
            if cands:
                abstract = max(cands, key=lambda x: len(x[1].raw))[1].raw
        for i, _ in pre_paragraphs:
            drop.add(i)

    # Author/affiliation paragraphs anywhere in the first ~30 blocks (multi-column PDFs
    # can place the right-column author lower in the reading order — keeps catching
    # affiliations that slipped past the section-heading cutoff).
    for i in range(min(30, len(blocks))):
        b = blocks[i]
        if b.type == "paragraph" and b.raw and _looks_like_affiliation(b.raw):
            drop.add(i)

    if drop:
        blocks = [b for i, b in enumerate(blocks) if i not in drop]

    return Document(
        id=_doc_id(pdf_path),
        title=title,
        abstract=abstract,
        blocks=blocks,
        source_pdf=pdf_path.name,
    )
