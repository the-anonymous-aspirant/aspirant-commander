"""End-to-end classify+extract regression harness against real PDFs.

For each `<sample>.expected.json` in `tests/fixtures/golden/`:

  1. Locate the sibling PDF in the operator's sample directory
     (`/tmp/vardeutlatande` by default; override with
     `VALUATION_SAMPLE_DIR`).
  2. Run the full classify_pdf + extract_document pipeline.
  3. Assert the classified `document_type` and every per-slot value
     match the golden JSON exactly. `comparable_sales_count` pins the
     UC BR page-2 row count without dragging the entire table into
     the golden (the row-parser unit tests cover row shape).

Adding a new sample is the operator-facing contract this harness
encodes: drop the new PDF into the sample directory, author its
`.expected.json` capturing every slot's expected value, and the test
fails until the parser strategy chain handles the new layout. Forces
the new shape into the strategy library — never lets it silently
fall through to UNKNOWN or to a partial extraction.

PDFs containing personnummer / property details live outside the
repo, so missing samples are SKIPPED per-fixture (not silenced). CI
without the operator's sample directory will skip every fixture —
which is loud enough to notice if the directory was supposed to be
mounted but wasn't.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.valuation_statement.classifier import DocumentType, classify_pdf
from app.valuation_statement.extraction import extract_document


GOLDEN_DIR = Path(__file__).parent / "fixtures" / "golden"
SAMPLE_DIR = Path(os.environ.get("VALUATION_SAMPLE_DIR", "/tmp/vardeutlatande"))


def _golden_files() -> list[Path]:
    return sorted(GOLDEN_DIR.glob("*.expected.json"))


@pytest.mark.parametrize(
    "golden_path",
    _golden_files(),
    ids=lambda p: p.stem,
)
def test_classify_and_extract_matches_golden(golden_path: Path):
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    pdf_name = golden_path.name.replace(".expected.json", ".pdf")
    pdf_path = SAMPLE_DIR / pdf_name
    if not pdf_path.exists():
        pytest.skip(f"Sample PDF not present: {pdf_path}")

    pdf_bytes = pdf_path.read_bytes()
    actual_type = classify_pdf(pdf_bytes)
    assert actual_type.value == golden["document_type"], (
        f"{pdf_name}: classifier returned {actual_type.value}, "
        f"golden expected {golden['document_type']}"
    )

    result = extract_document(pdf_bytes, actual_type, pdf_name)
    actual_fields = {f.key: f.value for f in result.fields}

    expected_fields = golden["fields"]
    assert actual_fields == expected_fields, (
        f"{pdf_name}: slot extraction diverged from golden.\n"
        f"  unexpected slots: {set(actual_fields) - set(expected_fields)}\n"
        f"  missing slots:    {set(expected_fields) - set(actual_fields)}\n"
        f"  value diffs:      "
        f"{ {k: (expected_fields[k], actual_fields.get(k)) for k in expected_fields if k in actual_fields and expected_fields[k] != actual_fields[k]} }"
    )

    expected_comparables = golden.get("comparable_sales_count", 0)
    actual_comparables = len(result.extras.get("comparable_sales", []))
    assert actual_comparables == expected_comparables, (
        f"{pdf_name}: comparable_sales row count drift — "
        f"got {actual_comparables}, golden expected {expected_comparables}"
    )


def test_golden_set_is_nonempty():
    """Guard against the directory accidentally going empty (e.g. a
    bad mv during refactor). Without this guard the parametrize
    would generate zero tests and the suite would report green with
    no coverage.
    """
    assert _golden_files(), (
        f"No `.expected.json` files found in {GOLDEN_DIR}. The golden "
        f"harness needs at least one fixture to be meaningful."
    )


# DocumentTypes without a dedicated parser yet — the dispatcher
# returns an empty `ExtractionResult` for these so the operator
# types every field at review time. Add to this set when a new
# parser-less DocumentType is introduced (and remove when its
# parser + golden land).
_PARSERLESS_TYPES = frozenset({DocumentType.FASTIGHETSUTDRAG.value})


def test_every_parser_backed_document_type_has_a_golden():
    """Every parser-backed DocumentType must be represented by at
    least one golden fixture. Without this, a new DocumentType
    could ship with a strategy chain but no integration coverage
    against a real PDF — and the operator's "verify patterns
    generalize" guard would be gone for that type.
    """
    covered = set()
    for path in _golden_files():
        golden = json.loads(path.read_text(encoding="utf-8"))
        covered.add(golden["document_type"])
    expected = {
        t.value for t in DocumentType
        if t != DocumentType.UNKNOWN and t.value not in _PARSERLESS_TYPES
    }
    missing = expected - covered
    assert not missing, (
        f"Parser-backed DocumentType(s) without a golden fixture: "
        f"{sorted(missing)}. Add tests/fixtures/golden/<sample>.expected.json "
        f"for each, capturing every slot's expected value against a real PDF."
    )
