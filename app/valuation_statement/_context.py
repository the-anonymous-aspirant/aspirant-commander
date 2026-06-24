"""Parsed-PDF view that field strategies query.

Built once per `extract_fields()` call and held immutable for the slot walk.
Strategies pick which projection they need — flat page text, the column/row
word grid, the PyMuPDF rendering — and ignore the rest.

Two text projections are carried, not one: pdfplumber for everything except
HSB's lägenhetsförteckning (which pdfplumber renders with each letter
quadrupled — `LLLLäääägggg...` — because of HSB's CMap), and PyMuPDF for
the lägenhetsförteckning where the same input surfaces as the cleaner
`Lägenhetsuppgi:ter` form with ligatures dropped instead of letters
quadrupled. Strategies that walk the word grid use pdfplumber; strategies
that label-stem-match against text use the PyMuPDF projection.

Pulling both behind this context means a strategy can be unit-tested by
feeding it a hand-built ParseContext, no PDF required.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from io import BytesIO

import pdfplumber


@dataclass(frozen=True)
class ParseContext:
    page1_text: str
    page1_words: tuple[dict, ...]
    page_texts: tuple[str, ...]
    fitz_full_text: str
    _pdf_bytes: bytes = field(repr=False, compare=False, default=b"")

    @property
    def page_count(self) -> int:
        return len(self.page_texts)

    @property
    def full_text(self) -> str:
        return "\n".join(self.page_texts)

    @property
    def collapsed_page1(self) -> str:
        """Page-1 text with runs of horizontal whitespace collapsed."""
        return re.sub(r"[ \t]+", " ", self.page1_text)


def build_context(pdf_bytes: bytes) -> ParseContext:
    """Open the PDF once with pdfplumber + PyMuPDF and project the views slots need."""
    import fitz

    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        page1 = pdf.pages[0]
        words = tuple(page1.extract_words())
        page_texts = tuple(p.extract_text() or "" for p in pdf.pages)
        page1_text = page_texts[0] if page_texts else ""
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        fitz_full_text = "\n".join((page.get_text() or "") for page in doc)
    return ParseContext(
        page1_text=page1_text,
        page1_words=words,
        page_texts=page_texts,
        fitz_full_text=fitz_full_text,
        _pdf_bytes=pdf_bytes,
    )
