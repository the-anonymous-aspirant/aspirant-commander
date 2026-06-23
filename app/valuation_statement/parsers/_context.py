"""Parsed-PDF view that slot strategies query.

Built once per `parse()` call and held immutable for the slot walk.
Strategies pick which projection they need — flat page text, a
column/row cell grid, the full multi-page text — and ignore the rest.

Pulling pdfplumber/PyMuPDF behind this context means a strategy can
be unit-tested by feeding it a hand-built ParseContext, no PDF
required.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from io import BytesIO

import pdfplumber


@dataclass(frozen=True)
class ParseContext:
    """View over a parsed PDF the strategy chains read.

    * `page1_text` is pdfplumber's flat text (whitespace as-emitted).
    * `page1_words` is pdfplumber's `extract_words()` output — used
      by column/row-cluster strategies.
    * `page_texts` carries every page's flat text so multi-page
      strategies (comparable-sales tables, multi-page footers) don't
      have to re-open the PDF.
    """

    page1_text: str
    page1_words: tuple[dict, ...]
    page_texts: tuple[str, ...]
    _pdf_bytes: bytes = field(repr=False, compare=False, default=b"")

    @property
    def page_count(self) -> int:
        return len(self.page_texts)

    @property
    def collapsed_page1(self) -> str:
        """Page-1 text with runs of horizontal whitespace collapsed.

        Mirrors `classifier.classify_text`'s collapsing so regexes
        match the same shape in both places.
        """
        return re.sub(r"[ \t]+", " ", self.page1_text)


def build_context(pdf_bytes: bytes) -> ParseContext:
    """Open the PDF once with pdfplumber and project the views slots need."""
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        page1 = pdf.pages[0]
        words = tuple(page1.extract_words())
        page_texts = tuple(p.extract_text() or "" for p in pdf.pages)
        page1_text = page_texts[0] if page_texts else ""
    return ParseContext(
        page1_text=page1_text,
        page1_words=words,
        page_texts=page_texts,
        _pdf_bytes=pdf_bytes,
    )
