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
        ("vardeutlatande_northmill_br.txt", DocumentType.VARDEUTLATANDE_NORTHMILL_BR),
        ("vardeutlatande_northmill_smahus.txt", DocumentType.VARDEUTLATANDE_NORTHMILL_SMAHUS),
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


def test_northmill_banner_wins_over_uc_when_both_words_present():
    # The Northmill template contains the literal word "Bostadsrätt" in the
    # Upplåtelseform line. Without the explicit Northmill ordering, the
    # broader `Värdeutlåtande Bostadsrätt` fingerprint would also match
    # the Northmill PDF on those tokens appearing somewhere on the page.
    # The CATEGORIES table puts Northmill first, so this asserts the
    # ordering survives any future edit.
    northmill = (FIXTURE_DIR / "vardeutlatande_northmill_br.txt").read_text(encoding="utf-8")
    document_type, _ = classify_text(northmill)
    assert document_type == DocumentType.VARDEUTLATANDE_NORTHMILL_BR


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
