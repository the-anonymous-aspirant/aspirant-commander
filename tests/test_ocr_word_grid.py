"""The OCR word-box grid that lets positional slot strategies recover on scans
(system_3 #5932).

The OCR fallback (#5907) recovers linearised *text*; the positional strategies
read a word-box GRID (`ParseContext.page1_words`) and so missed on every scan.
`ocr_pdf_words` rebuilds that grid from `pytesseract.image_to_data`. These tests
pin the two calibrations that make it a drop-in for pdfplumber's grid — cells
split at the column gutter, coordinates in PDF points — and that the strategies
then recover an OCR'd value, marked `uncertain`, without touching the digital
path.

The reassembly and the reader are exercised on hand-built grids (no engine); a
single skippable smoke drives the real Tesseract end to end.
"""

from __future__ import annotations

import shutil

import fitz
import pytest

from app.valuation_statement._context import ParseContext
from app.valuation_statement._ocr import _reassemble_cells, ocr_pdf_words
from app.valuation_statement.field_extractor import (
    _canonical_via_fitz,
    _objekt_fastighetsrapport_beteckning,
)


def _word(text, x0, top, *, w=40.0, h=8.0, line=(0, 0, 0)) -> dict:
    return {"text": text, "x0": x0, "x1": x0 + w, "top": top, "bottom": top + h, "_line": line}


# ---------- the gutter calibration ----------


def test_reassemble_glues_intra_cell_words_and_splits_at_the_column_gutter():
    """Words a few points apart are one cell; a wide gap starts a new one.

    Calibrated on the real scans: gaps inside a value cell are 2.4-4.1 pt while
    inter-column gaps are 43-210 pt, so the 12 pt gutter separates them. The two
    words of a cell glue WITHOUT spaces (pdfplumber's shape); a distant word is a
    separate cell, never glued onto the first."""
    words = [
        _word("Bengtsfors", 42.0, 101.6, w=45.0),  # x1 = 87.0
        _word("NÄRSIDAN", 90.4, 101.6, w=45.0),     # gap 3.4 pt -> same cell
        _word("1:21", 138.0, 101.6, w=12.0),        # gap ~2.6 pt -> same cell
        _word("2019-02-11", 304.1, 101.6, w=50.0),  # gap >200 pt -> new cell
    ]
    cells = _reassemble_cells(words)

    texts = [c["text"] for c in cells]
    assert texts == ["BengtsforsNÄRSIDAN1:21", "2019-02-11"]
    # the glued cell is anchored at the leftmost x0 and spans to the rightmost x1
    assert cells[0]["x0"] == 42.0
    assert cells[0]["x1"] == 150.0


def test_reassemble_keeps_separate_tesseract_lines_apart():
    """A cell never spans two lines even at the same x."""
    words = [
        _word("Beteckning", 42.0, 90.0, line=(0, 0, 0)),
        _word("Bengtsfors", 42.0, 101.6, line=(0, 0, 1)),
    ]
    cells = _reassemble_cells(words)
    assert sorted(c["text"] for c in cells) == ["Bengtsfors", "Beteckning"]


# ---------- _canonical_via_fitz gains an OCR-only substring arm ----------


def _ctx(*, page1_words=(), page1_text="", fitz_full_text="", ocr_used=False) -> ParseContext:
    return ParseContext(
        page1_text=page1_text,
        page1_words=tuple(page1_words),
        page_texts=(page1_text,),
        fitz_full_text=fitz_full_text,
        ocr_used=ocr_used,
    )


def test_canonical_via_fitz_restores_spacing_from_a_linearised_ocr_row():
    """OCR linearises a whole table row onto one line, so the digital
    line-equality lookup cannot match a single cell; the OCR arm finds the value
    as a space-stripped substring and returns just that span, spaced."""
    row = "Bengtsfors NÄRSIDAN 1:21 2019-02-11 2 089"
    ctx = _ctx(fitz_full_text=row, ocr_used=True)
    assert _canonical_via_fitz(ctx, "BengtsforsNÄRSIDAN1:21") == "Bengtsfors NÄRSIDAN 1:21"


def test_canonical_via_fitz_substring_arm_is_ocr_gated():
    """The digital path is byte-for-byte unchanged: with `ocr_used` False the
    substring arm never runs, so a value embedded in a longer line is not
    matched and the raw token is returned."""
    row = "Bengtsfors NÄRSIDAN 1:21 2019-02-11 2 089"
    ctx = _ctx(fitz_full_text=row, ocr_used=False)
    assert _canonical_via_fitz(ctx, "BengtsforsNÄRSIDAN1:21") == "BengtsforsNÄRSIDAN1:21"


def test_canonical_via_fitz_span_is_bounded_by_the_target():
    """The returned span stops at the target's length, so a value never absorbs
    the neighbouring column that follows it on the linearised row."""
    row = "Bengtsfors NÄRSIDAN 1:21 2019-02-11"
    ctx = _ctx(fitz_full_text=row, ocr_used=True)
    got = _canonical_via_fitz(ctx, "BengtsforsNÄRSIDAN1:21")
    assert "2019" not in got


# ---------- a positional strategy recovers on the rebuilt OCR grid ----------

_FR_BANNER = "Fastighetsrapport Plus R"


def test_objekt_recovers_from_the_ocr_word_grid():
    """With a rebuilt grid the fastighetsrapport `objekt` strategy reads the cell
    below `Beteckning` by coordinate — exactly as on the digital path — and the
    OCR arm of `_canonical_via_fitz` restores its spacing and titlecase."""
    grid = [
        {"text": "Beteckning", "x0": 41.5, "x1": 87.5, "top": 90.0, "bottom": 98.0},
        {"text": "BengtsforsNÄRSIDAN1:21", "x0": 41.5, "x1": 150.0, "top": 101.6, "bottom": 110.0},
        # a neighbouring column that must not be read as the beteckning
        {"text": "Totalareal", "x0": 304.0, "x1": 360.0, "top": 90.0, "bottom": 98.0},
    ]
    ctx = _ctx(
        page1_words=grid,
        page1_text=_FR_BANNER,
        fitz_full_text=f"{_FR_BANNER}\nBengtsfors NÄRSIDAN 1:21 2019-02-11 2 089",
        ocr_used=True,
    )
    assert _objekt_fastighetsrapport_beteckning(ctx) == "Bengtsfors Närsidan 1:21"


def test_objekt_fails_to_none_when_the_value_cell_is_absent():
    """No value cell under the label (a cell the scan did not capture in-column)
    returns None — a miss, never a wrong value."""
    grid = [
        {"text": "Beteckning", "x0": 41.5, "x1": 87.5, "top": 90.0, "bottom": 98.0},
        # only a neighbouring column below, nothing in the Beteckning column
        {"text": "8", "x0": 304.0, "x1": 312.0, "top": 101.6, "bottom": 110.0},
    ]
    ctx = _ctx(page1_words=grid, page1_text=_FR_BANNER, fitz_full_text=_FR_BANNER, ocr_used=True)
    assert _objekt_fastighetsrapport_beteckning(ctx) is None


# ---------- real engine, end to end (skipped where Tesseract is absent) ----------


def _scanned_two_column_pdf() -> bytes:
    """Render a label row + value row as text, rasterise, re-embed as an image so
    the native projections are empty and the OCR branch fires."""
    src = fitz.open()
    page = src.new_page(width=595, height=842)
    page.insert_text((60, 90), "Beteckning", fontsize=12, fontname="helv")
    page.insert_text((300, 90), "Totalareal", fontsize=12, fontname="helv")
    page.insert_text((60, 116), "Bjuromossen 3:14", fontsize=12, fontname="helv")
    page.insert_text((300, 116), "10 000", fontsize=12, fontname="helv")
    pix = page.get_pixmap(dpi=300)
    out = fitz.open()
    opage = out.new_page(width=595, height=842)
    opage.insert_image(fitz.Rect(0, 0, 595, 842), pixmap=pix)
    return out.tobytes()


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract binary not installed")
def test_real_tesseract_builds_a_points_grid_with_glued_cells():
    """Smoke over the real engine: the rebuilt grid carries cells in PDF points
    (page 1 is 595 x 842 pt), a label matches exactly, and the value below it
    reassembles into a single glued cell rather than one token per word."""
    cells = ocr_pdf_words(_scanned_two_column_pdf())

    assert cells, "no cells recovered from the rendered scan"
    assert all(0 <= c["x0"] <= 595 and 0 <= c["top"] <= 842 for c in cells)
    assert any(c["text"] == "Beteckning" for c in cells)
    # the two words of the value cell glue into one spaceless token
    assert any(c["text"] == "Bjuromossen3:14" for c in cells)
