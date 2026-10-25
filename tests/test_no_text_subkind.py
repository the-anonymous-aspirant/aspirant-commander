"""no_text sub-kind: re-printed vector vs true raster scan (system_3 #5910).

An image-only PDF reaches `no_text` for two very different reasons, and they
want opposite advice: a UC report re-printed through "Print To PDF" has its
glyphs as vector outlines and the right answer is "upload the original file"
(it extracts cleanly), while a photo/scan is a page-filling raster where OCR is
the only route. These tests pin the classifier that tells them apart and that
the flag rides on the diagnostics. `unknown` (and an absent flag) must be safe:
the client falls back to its current copy, so a miss is never worse than today.
"""

from __future__ import annotations

import fitz

from app.valuation_statement._ocr import (
    SUBKIND_RASTER_SCAN,
    SUBKIND_REPRINTED_VECTOR,
    SUBKIND_UNKNOWN,
    classify_no_text_subkind,
)
from app.valuation_statement.extraction import extract_document


def _reprinted_vector_pdf(pages: int = 2, draws_per_page: int = 300) -> bytes:
    """Outlined-glyph shape: no text, no fonts, many vector paths, no big image.

    Mirrors what "Microsoft: Print To PDF" does to a UC report — every glyph
    becomes drawn shapes. A tiny logo image is added to match the real files.
    """
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page(width=595, height=842)
        for i in range(draws_per_page):
            y = 20 + (i % 800)
            page.draw_line((30, y), (560, y))
        logo = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 20), False)
        logo.clear_with(120)
        page.insert_image(fitz.Rect(20, 20, 60, 40), pixmap=logo)  # frac ~0.005
    return doc.tobytes()


def _raster_scan_pdf(pages: int = 1) -> bytes:
    """Photo/scan shape: one page-filling raster, no vector drawings, no text."""
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page(width=595, height=842)
        pix = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 1653, 2338), False)
        pix.clear_with(230)
        page.insert_image(fitz.Rect(0, 0, 595, 842), pixmap=pix)
    return doc.tobytes()


def _digital_text_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 60), "VÄRDEUTLÅTANDE", fontsize=11, fontname="helv")
    return doc.tobytes()


# ---------- the classifier ----------


def test_reprinted_vector_is_classified_reprinted():
    assert classify_no_text_subkind(_reprinted_vector_pdf()) == SUBKIND_REPRINTED_VECTOR


def test_raster_scan_is_classified_scan():
    assert classify_no_text_subkind(_raster_scan_pdf()) == SUBKIND_RASTER_SCAN


def test_a_text_pdf_is_unknown_not_reprinted():
    # Only called on native-empty documents in practice; but if fonts are
    # present the re-printed signature (fonts==0) must not fire.
    assert classify_no_text_subkind(_digital_text_pdf()) == SUBKIND_UNKNOWN


# ---------- it rides on the diagnostics ----------


def test_subkind_reaches_diagnostics_on_a_no_text_document():
    """A re-printed-vector no_text document carries the sub-kind on diagnostics.

    OCR may or may not run (tesseract-dependent); the sub-kind is computed from
    page structure independently, so this holds either way.
    """
    diag = extract_document(_reprinted_vector_pdf(), "reprinted.pdf").diagnostics
    assert diag.no_text_subkind == SUBKIND_REPRINTED_VECTOR


def test_a_digital_document_has_no_subkind():
    """A document that carried text never reaches the classifier — sub-kind None."""
    diag = extract_document(_digital_text_pdf(), "digital.pdf").diagnostics
    assert diag.no_text_subkind is None


def test_classifier_stops_counting_paths_once_the_threshold_is_reached(monkeypatch):
    """The decision asks only "at least _SUBKIND_MIN_DRAWINGS paths?", so on a
    re-printed vector document the path count must settle on the first page and
    the remaining pages must not be walked for paths (#8748: get_drawings on
    every page was 1.13 s of a 1.43 s /decide on the smoke corpus). Fonts and
    the full-page-image check still run on every page."""
    import fitz

    from app.valuation_statement import _ocr

    calls = {"bboxlog": 0, "fonts": 0}
    real_bboxlog = fitz.Page.get_bboxlog
    real_fonts = fitz.Page.get_fonts

    def counting_bboxlog(self, *a, **k):
        calls["bboxlog"] += 1
        return real_bboxlog(self, *a, **k)

    def counting_fonts(self, *a, **k):
        calls["fonts"] += 1
        return real_fonts(self, *a, **k)

    monkeypatch.setattr(fitz.Page, "get_bboxlog", counting_bboxlog)
    monkeypatch.setattr(fitz.Page, "get_fonts", counting_fonts)
    assert _ocr.classify_no_text_subkind(_reprinted_vector_pdf(pages=3, draws_per_page=300)) == SUBKIND_REPRINTED_VECTOR
    assert calls["bboxlog"] == 1, calls
    assert calls["fonts"] == 3, calls


def test_path_count_matches_get_drawings_on_a_vector_page():
    """The bbox-log count is the same number get_drawings() returned, so the
    calibrated threshold keeps its meaning (#8748)."""
    import fitz

    from app.valuation_statement._ocr import _count_paths

    with fitz.open(stream=_reprinted_vector_pdf(pages=1, draws_per_page=120), filetype="pdf") as doc:
        page = doc[0]
        assert _count_paths(page) == len(page.get_drawings()) == 120
