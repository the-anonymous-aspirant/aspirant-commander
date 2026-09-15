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

import logging
import re
from dataclasses import dataclass, field
from io import BytesIO

import pdfplumber

from app.valuation_statement import _ocr

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParseContext:
    page1_text: str
    page1_words: tuple[dict, ...]
    page_texts: tuple[str, ...]
    fitz_full_text: str
    _pdf_bytes: bytes = field(repr=False, compare=False, default=b"")
    # True when the text projections above were rebuilt by OCR because both
    # native projections were empty — a scan or a photo (#5907). Off for every
    # digital PDF. Drives two things downstream: values recovered from OCR are
    # surfaced as `uncertain` rather than `confident`, and a `no_text` outcome
    # on such a row means "OCR was tried and recovered nothing", distinct from
    # "OCR was never attempted".
    ocr_used: bool = False

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

    ocr_used = False
    if not (page1_text.strip() or fitz_full_text.strip()):
        # No text under either native projection: a scan or a photograph. OCR
        # the rasterised pages so the existing guards and slot strategies get a
        # text projection to match against. A digital PDF never reaches here,
        # so it never rasters and never imports the OCR engine — zero added
        # latency on the normal path (#5907).
        ocr_pages = _ocr_pages_or_empty(pdf_bytes)
        if ocr_pages:
            page_texts = tuple(ocr_pages)
            page1_text = ocr_pages[0]
            # Both native projections were empty, so nothing is lost by giving
            # the fitz projection the same OCR text; guards that read
            # `fitz_full_text` (e.g. _is_lgh_utdrag) then see the OCR output.
            fitz_full_text = "\n".join(ocr_pages)
            ocr_used = True

    return ParseContext(
        page1_text=page1_text,
        page1_words=words,
        page_texts=page_texts,
        fitz_full_text=fitz_full_text,
        _pdf_bytes=pdf_bytes,
        ocr_used=ocr_used,
    )


def _ocr_pages_or_empty(pdf_bytes: bytes) -> list[str]:
    """OCR the pages, degrading to ``[]`` if the OCR engine is unavailable.

    A scan the pipeline cannot OCR (Tesseract or its Swedish data missing) must
    still return the `no_text` outcome and the operator's scan hint, never a
    500 — a fallback that fails is no worse than no fallback. The failure is
    logged loudly so a broken image surfaces rather than silently reverting the
    product to manual entry.
    """
    try:
        return _ocr.ocr_pdf_pages(pdf_bytes)
    except Exception:  # noqa: BLE001 — any OCR-environment failure degrades to no_text
        logger.exception("OCR fallback failed; treating document as no_text")
        return []
