"""Extract 10-K Item 1A (Risk Factors) from EDGAR HTML using only the standard library."""

from __future__ import annotations

import re
from html.parser import HTMLParser

BLOCK_TAGS = frozenset({"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section", "article", "ul", "ol", "hr", "title"})
SKIP_TAGS = frozenset({"script", "style", "head", "ix:header"})
_WS = re.compile(r"[ \t\r\f\v\u00a0\u2002\u2003\u2009\u200b]+")
# Heading lines: "Item 1A.", "ITEM 1A. RISK FACTORS", "Item 1A — Risk Factors". Anchored at line start so
# in-text cross references ("see Part I, Item 1A of this Form 10-K") never match.
START_RE = re.compile(r"^\s*item\s*1a\s*[.:\-\u2013\u2014]?\s*(risk\s+factors)?\s*[.:]?\s*$", re.IGNORECASE)
START_INLINE_RE = re.compile(r"^\s*item\s*1a\s*[.:\-\u2013\u2014]?\s*risk\s+factors\b", re.IGNORECASE)
END_RE = re.compile(r"^\s*item\s*(1b|1c|2)\s*[.:\-\u2013\u2014]?\s*(unresolved\s+staff\s+comments|cybersecurity|properties)?\s*[.:]?\s*$", re.IGNORECASE)
END_INLINE_RE = re.compile(r"^\s*item\s*(1b|1c|2)\s*[.:\-\u2013\u2014]?\s*(unresolved\s+staff\s+comments|cybersecurity|properties)\b", re.IGNORECASE)
FOOTER_RE = re.compile(r"(form\s+10-k\s*\|\s*\d+$)|(^\d{1,3}$)|(^page\s+\d+)|(^table of contents$)", re.IGNORECASE)
MIN_SECTION_CHARS = 1500
MIN_PARAGRAPH_CHARS = 40


class RiskFactorExtractionError(ValueError):
    """Item 1A could not be located confidently; callers must treat this as no data."""


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_tag: str | None = None
        self._skip_nest = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._skip_tag is not None:
            if tag == self._skip_tag: self._skip_nest += 1
            return
        style = (dict(attrs).get("style") or "").replace(" ", "").lower()
        if tag in SKIP_TAGS or "display:none" in style:
            self._skip_tag, self._skip_nest = tag, 1
            return
        if tag in BLOCK_TAGS: self.parts.append("\n")
        elif tag in {"td", "th"}: self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._skip_tag is not None:
            if tag == self._skip_tag:
                self._skip_nest -= 1
                if self._skip_nest == 0: self._skip_tag = None
            return
        if tag in BLOCK_TAGS: self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        # Source-level line wraps are not paragraph breaks; only block tags create newlines.
        if self._skip_tag is None: self.parts.append(data.replace("\r", " ").replace("\n", " "))


def html_to_text(document: str | bytes) -> str:
    """Strip HTML to newline-separated blocks with normalized whitespace."""
    if isinstance(document, bytes): document = document.decode("utf-8", errors="replace")
    parser = _TextExtractor()
    parser.feed(document); parser.close()
    text = "".join(parser.parts)  # entities already decoded (convert_charrefs=True)
    text = text.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    lines = [_WS.sub(" ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def _heading_positions(lines: list[str], full: re.Pattern[str], inline: re.Pattern[str]) -> list[int]:
    return [i for i, line in enumerate(lines) if len(line) < 120 and (full.match(line) or inline.match(line))]


def extract_item_1a(document: str | bytes) -> str:
    """Return Item 1A text: from an Item 1A heading to the next Item 1B/1C/2 heading.

    Table-of-contents hits produce tiny spans, so the longest candidate span wins; spans below
    MIN_SECTION_CHARS are rejected (fail closed rather than diffing a TOC fragment).
    """
    lines = html_to_text(document).split("\n")
    starts = _heading_positions(lines, START_RE, START_INLINE_RE)
    ends = _heading_positions(lines, END_RE, END_INLINE_RE)
    if not starts: raise RiskFactorExtractionError("no Item 1A heading found")
    best: tuple[int, int, int] | None = None
    for start in starts:
        end = next((e for e in ends if e > start), None)
        if end is None: continue
        body = lines[start + 1:end]
        if body and re.fullmatch(r"risk\s+factors\.?", body[0], re.IGNORECASE): body = body[1:]
        size = sum(len(line) for line in body)
        if best is None or size > best[0]: best = (size, start, end)
    if best is None: raise RiskFactorExtractionError("Item 1A heading found but no terminating Item 1B/1C/2 heading")
    size, start, end = best
    if size < MIN_SECTION_CHARS: raise RiskFactorExtractionError(f"Item 1A section too short ({size} chars); likely table-of-contents only")
    body = lines[start + 1:end]
    if body and re.fullmatch(r"risk\s+factors\.?", body[0], re.IGNORECASE): body = body[1:]
    return "\n".join(body)


def split_paragraphs(section: str, min_chars: int = MIN_PARAGRAPH_CHARS) -> list[str]:
    """One paragraph per text block; drop page footers, page numbers, and fragments shorter than min_chars."""
    out = []
    for line in section.split("\n"):
        line = line.strip()
        if len(line) < min_chars or FOOTER_RE.search(line): continue
        out.append(line)
    return out
