"""Pre-flight `/decide`: announce OCR and estimate it before /extract (#5915).

`decide_extraction` makes the cheap decision the client needs — does this need
OCR, which sub-kind, how many pages, how long — without running OCR, so no
Tesseract is needed here. The digital path must stay a zero-estimate no-op.
"""

from __future__ import annotations

import io
import shutil

import fitz
import pytest

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


def test_decide_never_calls_pdfplumber(monkeypatch):
    """The text-layer detection is fitz-only. pdfplumber's extract_text spends
    ~18s walking a re-printed vector PDF's outlined glyphs to return zero chars,
    which would put the "fast decision" at ~17s and defeat it (caught dogfooding
    DV.pdf, #5915). If decide ever reaches for pdfplumber, this fails."""
    import pdfplumber

    def _boom(*_a, **_k):
        raise AssertionError("decide_extraction must not open pdfplumber (fitz-only)")

    monkeypatch.setattr(pdfplumber, "open", _boom)
    d = decide_extraction(_raster_scan_pdf(pages=2))
    assert d.ocr_required is True
    assert d.no_text_subkind == "raster_scan"


# ---------- served-path perf budgets (#5933) ----------
#
# A perf figure that gates a decision is an assertion nobody re-checks: #5915
# specified /decide around a ~1.4s decision cost measured with fitz, shipped it
# on pdfplumber (~18s on a real re-printed-vector doc), and the number — living
# only in a task body and a code comment — could not go red. These turn the two
# gating figures in the decide path into tests that fail on their own.


def _dense_vector_pdf(pages: int = 1, lines_per_page: int = 3000) -> bytes:
    """No text layer; thousands of vector paths per page — the reprinted-vector
    shape whose outlined glyphs make pdfplumber's `extract_text` walk while fitz
    reads it in milliseconds (#5915). Built in-test, so no client document is
    shipped; measured ~13x fitz/pdfplumber gap (served decide ~64ms vs pdfplumber
    ~0.82s at 3000 lines/page on the cell), the same order as the real
    regression."""
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page(width=595, height=842)
        for i in range(lines_per_page):
            page.draw_line(
                (20 + (i % 500), 20 + (i % 800)), (60 + (i % 500), 23 + (i % 800))
            )
    return doc.tobytes()


def _raster_text_pdf(pages: int = 1) -> bytes:
    """A page-filling raster of rendered text — the raster_scan shape OCR reads,
    with enough text that per-page OCR cost is representative rather than trivial."""
    src = fitz.open()
    for _ in range(pages):
        page = src.new_page(width=595, height=842)
        y = 60
        for _ in range(30):
            page.insert_text(
                (50, y),
                "Marknadsvärde 2 375 000 kr Fastighetsbeteckning Möln 2:1",
                fontsize=11,
            )
            y += 24
    pix = src[0].get_pixmap(dpi=300)
    out = fitz.open()
    opage = out.new_page(width=595, height=842)
    opage.insert_image(fitz.Rect(0, 0, 595, 842), pixmap=pix)
    return out.tobytes()


def test_decide_served_path_stays_far_faster_than_the_pdfplumber_read(client):
    """The perf figure that justified the pre-flight — a decision an order of
    magnitude faster than the full read — pinned as a SERVED-path budget, not a
    prose claim (#5933).

    A ratio, not an absolute wall-clock: the served decision and the pdfplumber
    read it must not be as slow as are measured on the same box and the same
    document in the same run, so the bound is machine- and load-independent (both
    inflate together under load) and does not flake. GREEN against the fitz
    implementation (decide ~13x faster) and RED against a pdfplumber regression
    (decide would BE the pdfplumber read, ratio ~1). Complements
    `test_decide_never_calls_pdfplumber`, which pins the library; this pins the
    consequence, measured the way the client calls it. The /4 bound sits well
    inside the measured ~13x gap so a real slowdown trips it with margin."""
    import io
    import time

    import pdfplumber

    pdf = _dense_vector_pdf(pages=1, lines_per_page=3000)

    t = time.perf_counter()
    resp = client.post(
        "/valuation-statement/decide",
        files=[("files", ("dv.pdf", io.BytesIO(pdf), "application/pdf"))],
    )
    decide_s = time.perf_counter() - t
    assert resp.status_code == 200, resp.text
    assert resp.json()["documents"][0]["ocr_required"] is True

    t = time.perf_counter()
    with pdfplumber.open(io.BytesIO(pdf)) as p:
        _ = p.pages[0].extract_text()
    pdfplumber_s = time.perf_counter() - t

    assert decide_s < pdfplumber_s / 4, (
        f"/decide ({decide_s * 1000:.0f}ms) is not >=4x faster than the pdfplumber "
        f"read ({pdfplumber_s:.2f}s) it replaced — the fast decision has regressed "
        f"(#5915 shipped this at ~18s by reading the text layer with pdfplumber)"
    )


@pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="tesseract binary not installed"
)
@pytest.mark.parametrize(
    "subkind, make_pdf",
    [
        ("raster_scan", lambda: _raster_text_pdf(pages=1)),
        ("reprinted_vector", lambda: _dense_vector_pdf(pages=1, lines_per_page=600)),
    ],
)
def test_ocr_per_page_cost_stays_within_its_estimate_budget(subkind, make_pdf):
    """`OCR_SECONDS_PER_PAGE` is a countdown shown to the user, so the harm is the
    estimate being too OPTIMISTIC — OCR taking materially longer than promised
    (the #5915 class, where the shown time elapsed and the user kept waiting).
    This measures real OCR per page and fails if it exceeds the constant by more
    than a wide factor.

    One-sided and wide by design: per-page cost is hardware- and content-
    dependent — the 8s/4s constants were measured at the deployed endpoint on
    real documents (task #5933 records the on-cell measurement: reprinted ~1.9s
    on a synthetic page vs ~8s on the real glyph-dense doc, raster ~3.4s vs 4s),
    so this is an order-of-magnitude regression guard, not a tight SLA. Under-
    running the estimate is cosmetically benign and not gated."""
    import time

    from app.valuation_statement._ocr import ocr_pdf_pages

    pdf = make_pdf()
    t = time.perf_counter()
    pages = ocr_pdf_pages(pdf)
    per_page = (time.perf_counter() - t) / max(len(pages), 1)

    budget = OCR_SECONDS_PER_PAGE[subkind]
    assert per_page <= budget * 3, (
        f"{subkind}: observed {per_page:.1f}s/page exceeds 3x its {budget}s/page "
        f"estimate — the user-facing countdown is now materially too short"
    )
