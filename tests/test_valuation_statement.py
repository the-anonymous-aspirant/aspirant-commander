"""Acceptance tests for the Valuation Statement (Värdeutlåtande) tool.

Two layers:
  * unit-level tests of the template populator (no fixtures required)
  * integration-level smoke tests of /extract and /generate; the /extract
    test is skipped when the operator-supplied PDF fixtures aren't
    present (they contain real personnummer + property details so we do
    not commit them to the repo).
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from docx import Document

from app.valuation_statement.template import TemplateFields, populate


FIXTURE_ROOT = Path("/tmp/vardeutlatande")
HAS_FIXTURES = (FIXTURE_ROOT / "Datavardering.pdf").exists() and (
    FIXTURE_ROOT / "LGH_utdrag.pdf"
).exists()


# ---------- populator unit tests (no PDF fixtures) ----------


def _br_fields() -> TemplateFields:
    return TemplateFields(
        objekt="LGH 1303 HSB Brf Långpannan i Stockholm (7696097448)",
        objekt_short="LGH 1303 HSB Brf Långpannan i Stockholm",
        adress="Hanna Rydhs gata 12",
        kommun="Hägersten",
        upplatelseform="Bostadsrätt",
        datavardering_date="2026-06-02",
        fastighetsutdrag_date=None,
        lagenhetsforteckning_date="2026-06-09",
        bilder_note=None,
        likviditet="normal",
        marknadsvarde_kr="3 050 000",
        intervall_kr="50 000",
        ort="",
        datum="18/6/2026",
        maklare_namn="Jenny Wiklund",
        maklare_titel="Registrerad fastighetsmäklare",
        foretag="Fastighetsbyrån",
        mode="bostadsratt",
    )


def _hok_fields() -> TemplateFields:
    return TemplateFields(
        objekt="Vaggeryd Hok 2:139",
        objekt_short="Vaggeryd Hok 2:139",
        adress="Lillholmsvägen 12",
        kommun="Hok",
        upplatelseform="Friköpt",
        datavardering_date="2026-06-09",
        fastighetsutdrag_date="2026-06-09",
        lagenhetsforteckning_date=None,
        bilder_note="Bilder har inhämtats från kund som underlag.",
        likviditet="normal",
        marknadsvarde_kr="2 200 000",
        intervall_kr="50 000",
        ort="",
        datum="18/6/2026",
        maklare_namn="Jenny Wiklund",
        maklare_titel="Registrerad fastighetsmäklare",
        foretag="Fastighetsbyrån",
        mode="frikopt",
    )


def _paragraph_texts(docx_bytes: bytes) -> list[str]:
    doc = Document(BytesIO(docx_bytes))
    return [p.text.strip() for p in doc.paragraphs if p.text.strip()]


class TestPopulateBR:
    def test_no_placeholders_remain(self):
        text = "\n".join(_paragraph_texts(populate(_br_fields())))
        assert "[" not in text, "Unfilled placeholder remains in BR output"

    def test_object_row_uses_full_id(self):
        text = _paragraph_texts(populate(_br_fields()))
        assert any(
            p == "Objekt: LGH 1303 HSB Brf Långpannan i Stockholm (7696097448)"
            for p in text
        )

    def test_value_sentence_uses_short_id(self):
        text = "\n".join(_paragraph_texts(populate(_br_fields())))
        assert (
            "Vid värdering av LGH 1303 HSB Brf Långpannan i Stockholm i Hägersten "
            "har ortprismetoden använts."
        ) in text
        # The full id (with orgnr) must NOT appear in the running text.
        assert "Vid värdering av LGH 1303 HSB Brf Långpannan i Stockholm (7696097448)" not in text

    def test_description_drops_fastighetsutdrag_clause(self):
        text = "\n".join(_paragraph_texts(populate(_br_fields())))
        assert (
            "datavärdering per datum 2026-06-02 och "
            "lägenhetsförteckning per datum 2026-06-09 beaktats"
        ) in text
        assert "fastighetsutdrag" not in text

    def test_value_block_uses_supplied_amounts(self):
        text = "\n".join(_paragraph_texts(populate(_br_fields())))
        assert "Marknadsvärdet bedöms till 3 050 000 kr" in text
        assert "intervall om ± 50 000 kr" in text

    def test_empty_ort_yields_datum_only(self):
        # The Ort line should be just the date, not ",18/6/2026" or "Stockholm,18/6/2026".
        paragraphs = [p.strip() for p in _paragraph_texts(populate(_br_fields()))]
        assert "18/6/2026" in paragraphs
        assert not any(p.startswith(",") for p in paragraphs)


class TestPopulateHok:
    def test_object_row_uses_kommun_fastighet(self):
        paragraphs = _paragraph_texts(populate(_hok_fields()))
        assert "Objekt: Vaggeryd Hok 2:139" in paragraphs

    def test_description_drops_lagenhetsforteckning_clause(self):
        text = "\n".join(_paragraph_texts(populate(_hok_fields())))
        assert (
            "datavärdering per datum 2026-06-09 och "
            "fastighetsutdrag per datum 2026-06-09 beaktats"
        ) in text
        assert "lägenhetsförteckning" not in text

    def test_bilder_note_inserted(self):
        text = "\n".join(_paragraph_texts(populate(_hok_fields())))
        assert "Bilder har inhämtats från kund som underlag." in text

    def test_no_metacomments_remain(self):
        text = "\n".join(_paragraph_texts(populate(_hok_fields())))
        assert "Enbart bostadsrätt" not in text
        assert "Fyll på ifall bilder" not in text


# ---------- /generate API smoke test (no PDF fixtures needed) ----------


def test_generate_endpoint_returns_docx(client):
    body = {
        "objekt": "LGH 1303 HSB Brf Långpannan i Stockholm (7696097448)",
        "objekt_short": "LGH 1303 HSB Brf Långpannan i Stockholm",
        "adress": "Hanna Rydhs gata 12",
        "kommun": "Hägersten",
        "upplatelseform": "Bostadsrätt",
        "mode": "bostadsratt",
        "datavardering_date": "2026-06-02",
        "lagenhetsforteckning_date": "2026-06-09",
        "likviditet": "normal",
        "marknadsvarde_kr": "3 050 000",
        "intervall_kr": "50 000",
        "ort": "",
        "datum": "18/6/2026",
        "maklare_namn": "Jenny Wiklund",
        "maklare_titel": "Registrerad fastighetsmäklare",
        "foretag": "Fastighetsbyrån",
    }
    r = client.post("/valuation-statement/generate", json=body)
    assert r.status_code == 200
    assert (
        "officedocument.wordprocessingml.document" in r.headers["content-type"]
    )
    assert r.headers["content-disposition"].startswith("attachment;")
    assert r.content[:4] == b"PK\x03\x04"  # docx is a ZIP

    text = "\n".join(_paragraph_texts(r.content))
    assert "Objekt: LGH 1303 HSB Brf Långpannan i Stockholm (7696097448)" in text


# ---------- /extract integration (skip when fixtures missing) ----------


@pytest.mark.skipif(not HAS_FIXTURES, reason="Operator-supplied PDF samples not present")
def test_extract_endpoint_parses_both_types(client):
    with (FIXTURE_ROOT / "Datavardering.pdf").open("rb") as a, (
        FIXTURE_ROOT / "LGH_utdrag.pdf"
    ).open("rb") as b:
        r = client.post(
            "/valuation-statement/extract",
            files=[
                ("files", ("Datavärdering.pdf", a.read(), "application/pdf")),
                ("files", ("LGH utdrag.pdf", b.read(), "application/pdf")),
            ],
        )
    assert r.status_code == 200
    data = r.json()
    types = {d["document_type"] for d in data["documents"]}
    assert types == {"datavardering", "lgh_utdrag"}

    fields_by_key = {
        d["document_type"]: {f["key"]: f["value"] for f in d["fields"]}
        for d in data["documents"]
    }
    assert fields_by_key["datavardering"]["forening_namn"] == (
        "HSB Brf Långpannan i Stockholm"
    )
    assert fields_by_key["datavardering"]["marknadsvarde_suggested"] == "2 350 000"
    assert fields_by_key["lgh_utdrag"]["lgh_skatteverket"] == "1303"
    assert fields_by_key["lgh_utdrag"]["postort"] == "Hägersten"


# ---------- comparable-sales row parser (unit, no PDF) ----------


class TestComparableRowParser:
    """The UC Bostad PDF has no column borders, so we parse the flat-text
    rows by anchoring on the trailing YYYY-MM and walking right-to-left.
    These tests pin both shapes that show up in the live sample."""

    def _parse(self, line):
        from app.valuation_statement.parsers.datavardering import _parse_comparable_row
        return _parse_comparable_row(line)

    def test_eight_column_row_with_balkong(self):
        row = self._parse("HSBBrfLångpannaniStockholm 72 Ja 6307 302610 2920000 40556 2026-04")
        assert row == {
            "forening": "HSB Brf Långpannan i Stockholm",
            "area_m2": "72",
            "balkong": "Ja",
            "avgift_kr_manad": "6307",
            "arsavgift_kr": "302610",
            "pris_kr": "2920000",
            "pris_per_m2": "40556",
            "salj_datum": "2026-04",
            "raw": "HSBBrfLångpannaniStockholm 72 Ja 6307 302610 2920000 40556 2026-04",
        }

    def test_seven_column_row_no_balkong(self):
        row = self._parse("HSBBrfLångpannaniStockholm 62,5 5002 262682 2400000 38400 2025-12")
        assert row["balkong"] is None
        assert row["area_m2"] == "62,5"
        assert row["avgift_kr_manad"] == "5002"
        assert row["pris_kr"] == "2400000"
        assert row["salj_datum"] == "2025-12"

    def test_row_with_balkong_nej(self):
        row = self._parse("HSBBrfLångpannaniStockholm 70 Nej 5500 250000 2500000 35714 2026-02")
        assert row["balkong"] == "Nej"
        assert row["area_m2"] == "70"

    def test_line_without_date_is_skipped(self):
        assert self._parse("Adress Boyta Avgift Pris Datum") is None

    def test_too_few_columns_falls_back_to_raw(self):
        # Malformed row — at least the raw line + the trailing date survive
        # so the operator still sees something rather than the row vanishing.
        row = self._parse("Onlyforening 2026-04")
        assert row is not None
        assert row["salj_datum"] == "2026-04"
        assert row["raw"] == "Onlyforening 2026-04"
        assert row.get("forening") is None


@pytest.mark.skipif(not HAS_FIXTURES, reason="Operator-supplied PDF samples not present")
def test_extract_endpoint_returns_structured_comparable_sales(client):
    """The /extract response carries the per-row column dict, not just `raw`."""
    with (FIXTURE_ROOT / "Datavardering.pdf").open("rb") as a:
        r = client.post(
            "/valuation-statement/extract",
            files=[("files", ("Datavärdering.pdf", a.read(), "application/pdf"))],
        )
    assert r.status_code == 200
    docs = r.json()["documents"]
    dv = next(d for d in docs if d["document_type"] == "datavardering")
    assert dv["comparable_sales"], "Expected at least one parsed comparable row"
    first = dv["comparable_sales"][0]
    # Structured columns are present on every row (some may be None).
    for col in ("forening", "area_m2", "pris_kr", "pris_per_m2", "salj_datum", "raw"):
        assert col in first
