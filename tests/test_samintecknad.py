"""Samintecknad detection on a Fastighetsrapport (system_3 #6006).

The operator's rule: a fastighetsutdrag reading `Belastar även` (Inteckningar) or
`avser även annan fastighet` (the current owners' purchase) gets an "OBS!
samintecknad" warning. The real report's pdfplumber text layer is SPACELESS
(`Belastaräven`, `Köp,avserävenannanfastighet`) while PyMuPDF and OCR read it
spaced, so every case below runs against both renderings. The same purchase phrase
under `Tidigare ägare` is history and must not fire — the operator's example
carries it there too.

Contexts are hand-built from the example's line structure with invented names and
beteckningar; the real document carries personnummer and is not a fixture.
"""

from __future__ import annotations

import re
from pathlib import Path

import fitz
import pytest

from app.valuation_statement._context import ParseContext
from app.valuation_statement.extraction import extract_document
from app.valuation_statement.field_extractor import detect_samintecknad

BANNER = ["Fastighetsrapport", "Plus R", "Fastighet"]

FIXTURES = Path(__file__).parent / "fixtures" / "samintecknad"


def _fixture(name: str) -> list[str]:
    return (FIXTURES / name).read_text(encoding="utf-8").splitlines()


# The example report's layout (names and beteckningar invented), rendered the way
# PyMuPDF reads it: spaced, one cell per line, the ownership tables' `Ägare`
# COLUMN header on its own line.
SPACED = _fixture("fastighetsrapport_pymupdf.txt")


# The same report as pdfplumber reads it (the example's real shape): words inside
# a cell run together, cells on a row stay space-separated, the `Ägare` column
# header is merged into its row, and the beteckning wraps onto its own line.
SPACELESS = _fixture("fastighetsrapport_pdfplumber.txt")


def _ctx(lines: list[str], *, fitz_lines: list[str] | None = None) -> ParseContext:
    text = "\n".join(lines)
    return ParseContext(
        page1_text=text,
        page1_words=(),
        page_texts=(text,),
        fitz_full_text="\n".join(fitz_lines if fitz_lines is not None else lines),
    )


def _plumber(line: str) -> str:
    """Approximate pdfplumber's rendering of a hand-written spaced line: drop the
    spaces between letters (inside a cell), keep those next to digits (between
    cells). Used for the short negative cases; SPACELESS above is the faithful
    whole-report rendering."""
    return re.sub(r"(?<=[^\d\s]) (?=[^\d\s])", "", line)


def _variants(lines: list[str]) -> dict[str, ParseContext]:
    return {
        "spaced": _ctx(lines),
        "spaceless": _ctx(
            [line if line in BANNER else _plumber(line) for line in lines],
            fitz_lines=lines,
        ),
    }


def _report(rendering: str) -> ParseContext:
    # The spaceless context pairs pdfplumber's text with PyMuPDF's spaced text,
    # exactly as build_context does for a digital Fastighetsrapport.
    if rendering == "spaced":
        return _ctx(SPACED)
    return _ctx(SPACELESS, fitz_lines=SPACED)


@pytest.mark.parametrize("rendering", ["spaced", "spaceless"])
def test_mortgage_belastar_aven_fires_with_row_and_other_property(rendering):
    evidence = detect_samintecknad(_report(rendering))
    mortgages = [e for e in evidence if e["section"] == "inteckningar"]
    # Both Inteckningar rows, including the one after the page-2 header repeat.
    assert [e["row"] for e in mortgages] == ["2", "3"]
    # Re-spaced from the PyMuPDF projection when pdfplumber ran it together.
    assert [e["other_property"] for e in mortgages] == [
        "Testby EKLUNDA 1:11",
        "Testby EKLUNDA 1:11",
    ]


@pytest.mark.parametrize("rendering", ["spaced", "spaceless"])
def test_current_owner_purchase_fires_but_previous_owner_does_not(rendering):
    evidence = detect_samintecknad(_report(rendering))
    owners = [e for e in evidence if e["section"] == "agare"]
    # Only the 2022 current-owner purchase; the 1991 `Tidigare ägare` row is
    # history, even though the `Ägare` column header sits inside that table.
    assert [e["row"] for e in owners] == ["2022-04-28"]
    assert owners[0]["other_property"] is None


@pytest.mark.parametrize("rendering", ["spaced", "spaceless"])
def test_previous_owner_phrase_alone_does_not_fire(rendering):
    lines = [
        *BANNER,
        "Ägare",
        "Lagfart",
        "Anna Exempel",
        "2022-04-28 2150000 1/1 Köp Beviljad 2022-05-23",
        "Tidigare ägare",
        "Typ",
        "Ägare",
        "1991-05-31 725000 1/2 Köp, avser även annan fastighet Beviljad 1991-06-10",
        "Tomträttsupplåtelse",
        "Inteckningar",
        "2 510 000 510 000 Digitalt 2022-05-23 D-2022-00216991:6",
    ]
    assert detect_samintecknad(_variants(lines)[rendering]) == []


@pytest.mark.parametrize("rendering", ["spaced", "spaceless"])
def test_belastar_aven_outside_inteckningar_does_not_fire(rendering):
    lines = [
        *BANNER,
        "Inteckningar",
        "2 510 000 510 000 Digitalt 2022-05-23 D-2022-00216991:6",
        "Avtalsrättigheter*",
        "1 Avtalsservitut Belastar även 1963-11-06 63/2584",
    ]
    assert detect_samintecknad(_variants(lines)[rendering]) == []


def test_no_phrase_is_not_samintecknad():
    lines = [line for line in SPACED if "även" not in line]
    assert detect_samintecknad(_ctx(lines)) == []


def test_non_fastighetsrapport_document_never_fires():
    lines = [line for line in SPACED if line not in ("Fastighetsrapport", "Plus R")]
    assert detect_samintecknad(_ctx(["Värdeutlåtande", "Småhus", *lines])) == []


def test_ocr_linearised_row_reads_other_property_on_the_same_line():
    lines = [
        *BANNER,
        "Inteckningar",
        "2 510 000 510 000 Digitalt Belastar även Testby EKLUNDA 1:11 2022-05-23 D-2022-00216991:6",
    ]
    ctx = ParseContext(
        page1_text="\n".join(lines),
        page1_words=(),
        page_texts=("\n".join(lines),),
        fitz_full_text="\n".join(lines),
        ocr_used=True,
    )
    assert detect_samintecknad(ctx) == [
        {"section": "inteckningar", "row": "2", "other_property": "Testby EKLUNDA 1:11"}
    ]


def _pdf(lines: list[str]) -> bytes:
    doc = fitz.open()
    page = doc.new_page(height=1000)
    y = 40
    for line in lines:
        page.insert_text((40, y), line, fontsize=9, fontname="helv")
        y += 14
    return doc.tobytes()


def test_extract_document_carries_the_evidence():
    result = extract_document(_pdf(SPACED), "FastighetPlusR_Testby.pdf")
    sections = [e["section"] for e in result.extras["samintecknad_evidence"]]
    assert sections == ["agare", "inteckningar", "inteckningar"]


def test_extract_endpoint_serves_samintecknad_flag_and_evidence(client):
    body = _pdf(SPACED)
    response = client.post(
        "/valuation-statement/extract",
        files=[("files", ("FastighetPlusR_Testby.pdf", body, "application/pdf"))],
    )
    assert response.status_code == 200, response.text
    doc = response.json()["documents"][0]
    assert doc["samintecknad"] is True
    assert {"section": "inteckningar", "row": "2", "other_property": "Testby EKLUNDA 1:11"} in doc[
        "samintecknad_evidence"
    ]


def test_extract_endpoint_negative_control(client):
    body = _pdf([line for line in SPACED if "även" not in line])
    response = client.post(
        "/valuation-statement/extract",
        files=[("files", ("FastighetPlusR_Testby.pdf", body, "application/pdf"))],
    )
    assert response.status_code == 200, response.text
    doc = response.json()["documents"][0]
    assert doc["samintecknad"] is False
    assert doc["samintecknad_evidence"] == []
