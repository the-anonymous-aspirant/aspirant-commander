"""End-to-end field-first extractor regression harness against real PDFs.

For each `<sample>.expected.json` in `tests/fixtures/golden/`:

  1. Locate the sibling PDF in the operator's sample directory
     (`/tmp/vardeutlatande` by default; override with
     `VALUATION_SAMPLE_DIR`).
  2. Run the full `extract_document()` pipeline (no classifier; one
     field-first chain per slot).
  3. Assert every per-slot value matches the golden JSON exactly.
     `comparable_sales_count` pins the UC BR page-2 row count without
     dragging the entire table into the golden (the row-parser unit
     tests cover row shape).

Adding a new sample is the operator-facing contract this harness
encodes: drop the new PDF into the sample directory, author its
`.expected.json` capturing every slot's expected value, and the test
fails until the chain handles the new layout. Forces the new shape
into the strategy library — never lets it silently land as a row of
None values that the operator has to retype.

PDFs containing personnummer / property details live outside the
repo, so missing samples are SKIPPED per-fixture. A box that holds no
samples at all reports skips and stays green — CI never runs this
suite (test execution is local-only per #11), so a hard failure there
would be permanent and meaningless.

Where the samples are
---------------------
This is the part that had been recorded nowhere, and its absence cost
an epic a false acceptance claim and an operator a request for
something already on the box (#5369). On the aspirant cell the
reference documents live in the user file store:

    VALUATION_SAMPLE_DIR=/data/aspirant/files/users/1/Värdeutlåtande \
        python -m pytest tests/test_golden_pipeline.py

With that set, all ten fixtures run. Without it every one of them
skips — which is why `test_sample_directory_is_not_misconfigured`
below turns the one unambiguous mistake (a directory that holds PDFs
but backs no golden at all, i.e. the wrong directory) into a failure
rather than ten more skips. Samples keep their download names; see
`tests/sample_resolver.py` for how a stem is matched to a file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.valuation_statement.extraction import extract_document
from tests.sample_resolver import (
    describe_attempt,
    load_aliases,
    resolve,
    sample_pdfs,
)


GOLDEN_DIR = Path(__file__).parent / "fixtures" / "golden"
DEFAULT_SAMPLE_DIR = Path("/tmp/vardeutlatande")
SAMPLE_DIR = Path(os.environ.get("VALUATION_SAMPLE_DIR") or DEFAULT_SAMPLE_DIR)
SAMPLE_DIR_IS_EXPLICIT = bool(os.environ.get("VALUATION_SAMPLE_DIR"))
ALIASES = load_aliases(GOLDEN_DIR)


def _golden_files() -> list[Path]:
    return sorted(GOLDEN_DIR.glob("*.expected.json"))


def _stem(golden_path: Path) -> str:
    return golden_path.name.replace(".expected.json", "")


def _sample_for(golden_path: Path) -> Path | None:
    return resolve(_stem(golden_path), SAMPLE_DIR, ALIASES)


@pytest.mark.parametrize(
    "golden_path",
    _golden_files(),
    ids=lambda p: p.stem,
)
def test_extract_matches_golden(golden_path: Path):
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    stem = _stem(golden_path)
    pdf_path = _sample_for(golden_path)
    if pdf_path is None:
        pytest.skip(describe_attempt(stem, SAMPLE_DIR, ALIASES))

    pdf_name = pdf_path.name
    pdf_bytes = pdf_path.read_bytes()
    result = extract_document(pdf_bytes, pdf_name)
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
    bad mv during refactor). Without this guard the parametrize would
    generate zero tests and the suite would report green with no
    coverage.
    """
    assert _golden_files(), (
        f"No `.expected.json` files found in {GOLDEN_DIR}. The golden "
        f"harness needs at least one fixture to be meaningful."
    )


def test_golden_covers_every_documented_sample_layout():
    """Pin the set of sample-PDF layouts the field-first chains must
    handle. New layout = new fixture; missing layout = silent gap.
    """
    expected_layouts = {
        "Datavardering",            # UC BR tabular (3-col, pre-2026)
        "Datavardering_2",          # UC BR tabular (6-col, post-2026)
        "VardeutlatandeBR",         # Fastighetsbyrån prose BR
        "VardeutlatandeHok",        # Fastighetsbyrån prose Friköpt
        "UCB_Bengtsfors",           # UC Småhus tabular, address missing
        "UCB_Katrineholm",          # UC Småhus tabular, address present
        "FastighetPlusR_Bengtsfors",   # Lantmäteriet fastighetsrapport
        "FastighetPlusR_Katrineholm",  # Lantmäteriet fastighetsrapport
        "LGH_utdrag",               # HSB lägenhetsförteckning (Långpannan)
        "Min_bostad",               # HSB lägenhetsförteckning (Hilda i Malmö)
    }
    covered = {p.name.replace(".expected.json", "") for p in _golden_files()}
    missing = expected_layouts - covered
    assert not missing, (
        f"Sample layouts without a golden fixture: {sorted(missing)}. "
        f"Drop the PDF into the sample directory and author its "
        f"`.expected.json` so the chain regressions are caught."
    )


def test_explicit_sample_dir_exists():
    """`VALUATION_SAMPLE_DIR` pointing nowhere is a mistake, not a skip.

    Somebody who sets the variable is asking for the real-document
    arm to run. Silently skipping ten fixtures because the path has a
    typo hands them a green suite that measured nothing.
    """
    if not SAMPLE_DIR_IS_EXPLICIT:
        pytest.skip("VALUATION_SAMPLE_DIR not set; using the default location")
    assert SAMPLE_DIR.is_dir(), (
        f"VALUATION_SAMPLE_DIR={SAMPLE_DIR} is not a directory. Unset it to "
        f"fall back to {DEFAULT_SAMPLE_DIR}, or point it at the sample store "
        f"(on the aspirant cell: /data/aspirant/files/users/1/Värdeutlåtande)."
    )


def test_sample_directory_is_not_misconfigured():
    """A directory full of PDFs that backs no golden is the wrong directory.

    This is the one case that cannot be innocent. No samples at all is
    ordinary — most checkouts have none, and every fixture skips. But
    PDFs present and *not one* of them answering to a golden stem means
    the harness is pointed somewhere it should not be, or the download
    names have drifted past what `sample_resolver` recognises. Either
    way the real-document arm is not running, and before #5369 that
    said `10 skipped` and nothing else.
    """
    present = sample_pdfs(SAMPLE_DIR)
    if not present:
        pytest.skip(f"No sample PDFs in {SAMPLE_DIR}; real-document arm not run")

    resolved = {
        _stem(g): _sample_for(g) for g in _golden_files()
    }
    matched = {stem: p for stem, p in resolved.items() if p is not None}
    assert matched, (
        f"{SAMPLE_DIR} holds {len(present)} PDF(s) but none of them backs any "
        f"of the {len(resolved)} golden fixtures. Files found: "
        f"{[p.name for p in present[:12]]}. Expected stems: "
        f"{sorted(resolved)}. Either this is the wrong directory, or a "
        f"download name has drifted — add its prefix to "
        f"tests/fixtures/golden/sample_index.json."
    )
