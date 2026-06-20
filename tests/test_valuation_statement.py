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
