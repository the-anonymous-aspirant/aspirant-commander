"""Pre-flight `/decide`: announce OCR and estimate it before /extract (#5915).

`decide_extraction` makes the cheap decision the client needs — does this need
OCR, which sub-kind, how many pages, how long — without running OCR, so no
Tesseract is needed here. The digital path must stay a zero-estimate no-op.
"""

from __future__ import annotations

import io

import fitz

from app.valuation_statement.extraction import (
    OCR_SECONDS_PER_PAGE,
    decide_extraction,
)


def _digital_pdf(pages: int = 1) -> bytes:
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page()
        page.insert_text((60, 60), "VÄRDEUTLÅTANDE Värderingsobjekt", fontsize=11, fontname="helv")
    return doc.tobytes()


def _reprinted_vector_pdf(pages: int = 3) -> bytes:
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page(width=595, height=842)
        for i in range(300):
            page.draw_line((30, 20 + (i % 800)), (560, 20 + (i % 800)))
    return doc.tobytes()


def _raster_scan_pdf(pages: int = 2) -> bytes:
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page(width=595, height=842)
        pix = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 1653, 2338), False)
        pix.clear_with(230)
        page.insert_image(fitz.Rect(0, 0, 595, 842), pixmap=pix)
    return doc.tobytes()


# ---------- decide_extraction (no OCR) ----------


def test_digital_pdf_needs_no_ocr_and_estimates_zero():
    d = decide_extraction(_digital_pdf(pages=2))
    assert d.ocr_required is False
    assert d.no_text_subkind is None
    assert d.estimated_ocr_seconds == 0
    assert d.page_count == 2


def test_reprinted_vector_estimate_is_pages_times_the_vector_rate():
    d = decide_extraction(_reprinted_vector_pdf(pages=3))
    assert d.ocr_required is True
    assert d.no_text_subkind == "reprinted_vector"
    assert d.page_count == 3
    assert d.estimated_ocr_seconds == 3 * OCR_SECONDS_PER_PAGE["reprinted_vector"]


def test_raster_scan_estimate_is_pages_times_the_scan_rate():
    d = decide_extraction(_raster_scan_pdf(pages=2))
    assert d.ocr_required is True
    assert d.no_text_subkind == "raster_scan"
    assert d.estimated_ocr_seconds == 2 * OCR_SECONDS_PER_PAGE["raster_scan"]


def test_the_vector_rate_is_higher_than_the_scan_rate_per_page():
    # A 2-page scan and a 3-page re-printed doc must not quote the same number,
    # and per page the vector kind is the more expensive (rendering glyphs).
    assert OCR_SECONDS_PER_PAGE["reprinted_vector"] > OCR_SECONDS_PER_PAGE["raster_scan"]


# ---------- the /decide endpoint ----------


def test_decide_endpoint_aggregates_a_batch(client):
    files = [
        ("files", ("digital.pdf", io.BytesIO(_digital_pdf()), "application/pdf")),
        ("files", ("scan.pdf", io.BytesIO(_raster_scan_pdf(pages=2)), "application/pdf")),
    ]
    resp = client.post("/valuation-statement/decide", files=files)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["any_ocr_required"] is True  # the scan needs OCR
    assert body["estimated_seconds"] == 2 * OCR_SECONDS_PER_PAGE["raster_scan"]
    by_name = {d["filename"]: d for d in body["documents"]}
    assert by_name["digital.pdf"]["ocr_required"] is False
    assert by_name["scan.pdf"]["no_text_subkind"] == "raster_scan"


def test_decide_endpoint_rejects_a_non_pdf(client):
    files = [("files", ("x.txt", io.BytesIO(b"not a pdf"), "text/plain"))]
    resp = client.post("/valuation-statement/decide", files=files)
    assert resp.status_code == 415
