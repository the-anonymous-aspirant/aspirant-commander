"""Unit tests for the content-based valuation classifier.

The deterministic kernel `classify_text(page1_text)` is exercised against
captured first-page text from every PDF in the operator's audit sample
(`docs/VALUATION_CLASSIFIER_AUDIT.md`). Fixtures are the raw output of
PyMuPDF on each PDF — same routine the runtime classifier uses — so the
test catches any regression in fingerprint coverage without bundling
megabytes of source PDFs in the repo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.valuation_statement.classifier import (
    DocumentType,
    classify_text,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "classifier"


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("datavardering_br.txt", DocumentType.DATAVARDERING_BR),
        ("datavardering_smahus.txt", DocumentType.DATAVARDERING_SMAHUS),
        # Both Fastighetsbyrån prose appraisals classify into the same
        # DocumentType as their UC Bostad tabular counterpart; the per-
        # slot strategy chain inside the parser handles both layouts
        # (one parser branch per content type regardless of issuer).
        ("vardeutlatande_northmill_br.txt", DocumentType.DATAVARDERING_BR),
        ("vardeutlatande_northmill_smahus.txt", DocumentType.DATAVARDERING_SMAHUS),
        ("fastighetsutdrag.txt", DocumentType.FASTIGHETSUTDRAG),
        ("lgh_utdrag.txt", DocumentType.LGH_UTDRAG),
    ],
)
def test_each_fixture_classifies_to_its_namesake(fixture: str, expected: DocumentType):
    page1 = (FIXTURE_DIR / fixture).read_text(encoding="utf-8")
    document_type, matched = classify_text(page1)
    assert document_type == expected
    assert matched, f"{fixture} matched {document_type} but recorded no patterns"


def test_classifier_returns_unknown_on_empty_text():
    document_type, matched = classify_text("")
    assert document_type == DocumentType.UNKNOWN
    assert matched == []


def test_classifier_returns_unknown_on_unrelated_pdf_text():
    # A page from a generic Swedish kreditupplysning PDF — no Värdeutlåtande
    # / Fastighetsrapport / Lägenhetsuppgi*ter / Bostadsrättsförening
    # signals — must NOT match any category.
    document_type, matched = classify_text(
        "Kreditupplysning\nUC AB\nPersonnummer: 19470701-XXXX\n"
        "Inkomst enligt taxering 2024: 450 000 kr\n"
    )
    assert document_type == DocumentType.UNKNOWN
    assert matched == []


def test_classifier_is_content_only_not_issuer_branded():
    # Regression for the operator correction on #1060: the classifier
    # MUST NOT key off issuer branding ("Northmill Bank", "UC Bostad").
    # Stripping the bank-name banner from the Northmill prose template
    # must still classify the PDF the same way — the Upplåtelseform
    # line and "Värderingsobjekt"/"VÄRDEUTLÅTANDE" headers are what
    # carry the content signal.
    for fixture, expected in (
        ("vardeutlatande_northmill_smahus.txt", DocumentType.DATAVARDERING_SMAHUS),
        ("vardeutlatande_northmill_br.txt", DocumentType.DATAVARDERING_BR),
    ):
        northmill = (FIXTURE_DIR / fixture).read_text(encoding="utf-8")
        debranded = northmill.replace("Northmill Bank AB", "Generic Bank XYZ")
        document_type, _ = classify_text(debranded)
        assert document_type == expected, fixture


def test_lgh_fingerprints_tolerate_pdftotext_ligature_glitches():
    # PyMuPDF on at least one HSB PDF emits "Lägenhetsuppgifter" cleanly,
    # but pdftotext renders the `ﬁ` ligature as a stray character (`9`,
    # `;`, `'`). The fingerprint uses a 1–3 char wildcard so both
    # renderings are recognised. Assert the wildcard form still matches
    # the canonical ligature.
    glitched = (
        "HSB Brf Långpannan i Stockholm\n"
        "Lägenhetsuppgi9ter\n"
        "Lgh-nr Skatteverkets lgh-nr Antal rum\n"
        "Bostadsrä;tsförening och fastighet\n"
    )
    document_type, matched = classify_text(glitched)
    assert document_type == DocumentType.LGH_UTDRAG
    assert len(matched) == 2
