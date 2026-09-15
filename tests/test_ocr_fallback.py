"""OCR fallback for image-only PDFs (system_3 #5907).

A scanned or photographed valuation carries no text layer, so both native
projections are empty and every content guard misses — until now the operator
retyped every field by hand. These tests pin the fallback:

  * it runs only on the empty-projection branch, never on a digital PDF;
  * it re-runs the existing content guards over the OCR text;
  * every value it recovers is flagged `uncertain` with a provenance note,
    never surfaced as `confident`;
  * a scan it still cannot read keeps the `no_text` outcome the client renders
    its scan hint on, with `ocr_used` recording that OCR was tried;
  * a broken OCR environment degrades to `no_text`, never a 500.

The OCR engine is monkeypatched in every test but the last, so the branch logic
is pinned without depending on Tesseract's exact character output. The final
test is a skippable end-to-end smoke over the real engine.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import fitz
import pytest

from app.valuation_statement import _context
from app.valuation_statement._context import build_context
from app.valuation_statement.extraction import (
    OUTCOME_NO_TEXT,
    ExtractedField,
    extract_document,
)
from app.valuation_statement.field_extractor import (
    _OCR_CONFIDENCE_NOTE,
    _mark_ocr_confidence,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "ocr" / "datavardering_prose_ocr.json"


# ---------- PDF builders (no client document required) ----------


def _digital_pdf() -> bytes:
    """A PDF that carries a real text layer — the normal path."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 60), "VÄRDEUTLÅTANDE", fontsize=11, fontname="helv")
    page.insert_text((60, 80), "Värderingsobjekt", fontsize=11, fontname="helv")
    return doc.tobytes()


def _scanned_pdf() -> bytes:
    """One page, one raster image, zero text objects — a print-and-scan.

    Its native projections are empty by construction, so it takes the OCR
    branch. The OCR itself is monkeypatched in the tests that use this, so the
    raster content is irrelevant — only its lack of a text layer matters.
    """
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    pix = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 1200, 1700), False)
    pix.clear_with(220)
    page.insert_image(fitz.Rect(0, 0, 595, 842), pixmap=pix)
    return doc.tobytes()


def _scanned_text_pdf(lines: list[str]) -> bytes:
    """A scan whose raster *is* readable text: render text, rasterise, re-embed.

    The output carries the picture of the text at 300 DPI but no text layer, so
    both native projections are empty and Tesseract has something legible to
    read. Used only by the real-engine smoke test.
    """
    src = fitz.open()
    page = src.new_page(width=595, height=842)
    y = 90
    for line in lines:
        page.insert_text((60, y), line, fontsize=20, fontname="helv")
        y += 44
    pix = page.get_pixmap(dpi=300)
    out = fitz.open()
    opage = out.new_page(width=595, height=842)
    opage.insert_image(fitz.Rect(0, 0, 595, 842), pixmap=pix)
    return out.tobytes()


def _fixture() -> dict:
    return json.loads(_FIXTURE.read_text())


# ---------- the digital path is untouched ----------


def test_digital_pdf_never_invokes_ocr(monkeypatch):
    """A PDF with a text layer must never raster or call the OCR engine.

    This is the structural guarantee behind acceptance 2 (no OCR, no added
    latency on the normal path): the engine is replaced with a call that fails
    the test if it fires.
    """
    def _must_not_run(*_args, **_kwargs):
        raise AssertionError("OCR ran on a digital PDF")

    monkeypatch.setattr(_context._ocr, "ocr_pdf_pages", _must_not_run, raising=True)

    ctx = build_context(_digital_pdf())

    assert ctx.ocr_used is False
    assert "VÄRDEUTLÅTANDE" in ctx.page1_text


# ---------- the scan path ----------


def test_scanned_pdf_populates_projections_and_sets_ocr_used(monkeypatch):
    pages = ["FÖRSTA SIDAN text", "andra sidan text"]
    monkeypatch.setattr(
        _context._ocr, "ocr_pdf_pages", lambda *_a, **_k: pages, raising=True
    )

    ctx = build_context(_scanned_pdf())

    assert ctx.ocr_used is True
    assert ctx.page1_text == pages[0]
    assert ctx.fitz_full_text == "\n".join(pages)


def test_ocr_text_reruns_guards_and_recovers_fields_as_uncertain(monkeypatch):
    """OCR text flows through the existing guards + slots; recovered = uncertain."""
    fx = _fixture()
    monkeypatch.setattr(
        _context._ocr,
        "ocr_pdf_pages",
        lambda *_a, **_k: list(fx["page_texts"]),
        raising=True,
    )

    result = extract_document(_scanned_pdf(), "scan.pdf")
    diag = result.diagnostics
    by_key = {f.key: f for f in result.fields}

    assert diag.ocr_used is True
    assert fx["expect_guard"] in diag.guards_matched
    for key, value in fx["expect_fields"].items():
        assert by_key[key].value == value, f"{key} not recovered from OCR text"
        assert by_key[key].confidence == "uncertain", f"{key} not flagged uncertain"
        assert _OCR_CONFIDENCE_NOTE in (by_key[key].note or "")
    # A run that recovered a value is not a scan miss.
    assert diag.outcome != OUTCOME_NO_TEXT
    assert diag.value_fields_filled >= 1


def test_ocr_recovers_nothing_stays_no_text(monkeypatch):
    """OCR ran (native projections empty) but read nothing → still `no_text`.

    Acceptance 3: the client's scan hint keys on `no_text`, so an OCR run that
    recovers no value must keep that outcome. `ocr_used` on the row records
    that OCR was tried and could not help.
    """
    monkeypatch.setattr(
        _context._ocr, "ocr_pdf_pages", lambda *_a, **_k: [""], raising=True
    )

    diag = extract_document(_scanned_pdf(), "blank_scan.pdf").diagnostics

    assert diag.ocr_used is True
    assert diag.outcome == OUTCOME_NO_TEXT


def test_ocr_unavailable_degrades_to_no_text(monkeypatch):
    """Tesseract missing must degrade to `no_text`, never a 500.

    `ocr_used` is False here — OCR was attempted but the environment could not
    run it — which is distinct from an OCR run that read nothing.
    """
    def _boom(*_a, **_k):
        raise RuntimeError("tesseract is not installed")

    monkeypatch.setattr(_context._ocr, "ocr_pdf_pages", _boom, raising=True)

    diag = extract_document(_scanned_pdf(), "scan.pdf").diagnostics

    assert diag.ocr_used is False
    assert diag.outcome == OUTCOME_NO_TEXT


# ---------- the confidence-marking rule, in isolation ----------


def test_mark_ocr_confidence_downgrades_only_recovered_values():
    fields = [
        ExtractedField(key="adress", value="Gryningsvägen 13", confidence="confident",
                       source_filename="s.pdf"),
        ExtractedField(key="kommun", value="Partille", confidence="uncertain",
                       source_filename="s.pdf", note="prior note"),
        ExtractedField(key="marknadsvarde_kr", value=None, confidence="not_found",
                       source_filename="s.pdf"),
        ExtractedField(key="source_class", value="datavardering", confidence="confident",
                       source_filename="s.pdf"),
    ]

    _mark_ocr_confidence(fields)
    by_key = {f.key: f for f in fields}

    # A recovered value is dropped to uncertain and annotated.
    assert by_key["adress"].confidence == "uncertain"
    assert by_key["adress"].note == _OCR_CONFIDENCE_NOTE
    # An already-uncertain value keeps uncertain and its note is appended, not lost.
    assert by_key["kommun"].confidence == "uncertain"
    assert "prior note" in by_key["kommun"].note
    assert _OCR_CONFIDENCE_NOTE in by_key["kommun"].note
    # Nothing was recovered for a not_found slot, so there is nothing to doubt.
    assert by_key["marknadsvarde_kr"].confidence == "not_found"
    assert by_key["marknadsvarde_kr"].note is None
    # Semantic primitives carry classifier state, not an operator-verified value.
    assert by_key["source_class"].confidence == "confident"


# ---------- real engine, end to end (skipped where Tesseract is absent) ----------


@pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="tesseract binary not installed"
)
def test_real_tesseract_recovers_text_from_a_rendered_scan():
    """Lenient smoke over the real engine: a rendered scan yields OCR text.

    Deliberately does not assert exact recovered values — that is the province
    of the dogfood run against the real client files. It pins that the wiring
    reaches Tesseract and produces a non-empty projection on this box's engine.
    """
    pdf = _scanned_text_pdf(
        ["VÄRDEUTLÅTANDE", "Värderingsobjekt", "Adress: Gryningsvägen 13", "Kommun: Partille"]
    )

    diag = extract_document(pdf, "rendered_scan.pdf").diagnostics

    assert diag.ocr_used is True
    assert diag.full_text_length > 0
