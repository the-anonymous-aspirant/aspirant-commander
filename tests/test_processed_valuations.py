"""Integration tests for the processed-valuations store (epic system_3 #1154).

Backs the 'Tidigare värderingar' tab. Covers all 6 endpoints + the
edit-in-place semantics (PATCH mutates; no history snapshots) + CSV
export shape.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime

import pytest


PROCESSED = "/valuation-statement/processed"


def _br_payload(name: str | None = None, final_override: dict | None = None) -> dict:
    extracted = {
        "objekt": "LGH 1303 HSB Brf Långpannan i Stockholm (7696097448)",
        "objekt_short": "LGH 1303 HSB Brf Långpannan i Stockholm",
        "adress": "Hanna Rydhs gata 12",
        "marknadsvarde_kr": "3 050 000",
        "comparable_sales": [{"forening": "Brf X", "pris_kr": "2 900 000"}],
    }
    final = dict(extracted) if final_override is None else {**extracted, **final_override}
    return {
        "name": name,
        "input_files": ["Datavardering.pdf", "LGH_utdrag.pdf"],
        "extracted_values": extracted,
        "final_values": final,
    }


class TestCreate:
    def test_returns_id_and_auto_name(self, client):
        body = _br_payload()
        body["name"] = None
        r = client.post(PROCESSED, json=body)
        assert r.status_code == 201, r.text
        out = r.json()
        assert out["id"]
        # auto name: <YYYY-MM-DD>_<objekt_short or fastighetsbeteckning>
        assert "LGH 1303 HSB Brf Långpannan i Stockholm" in out["name"]
        assert out["was_manually_edited"] is False

    def test_explicit_name_overrides_auto(self, client):
        r = client.post(PROCESSED, json=_br_payload(name="My valuation"))
        assert r.status_code == 201
        assert r.json()["name"] == "My valuation"

    def test_manual_edit_flag_set_when_final_diverges(self, client):
        body = _br_payload(final_override={"marknadsvarde_kr": "3 200 000"})
        r = client.post(PROCESSED, json=body)
        assert r.json()["was_manually_edited"] is True


class TestList:
    def test_list_returns_newest_first_paginated(self, client):
        for i in range(5):
            client.post(PROCESSED, json=_br_payload(name=f"row-{i}"))
        r = client.get(PROCESSED, params={"limit": 3, "offset": 0})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 5
        assert body["limit"] == 3
        assert len(body["items"]) == 3
        # newest first: created row-4 last, so it leads
        assert body["items"][0]["name"] == "row-4"

    def test_list_empty(self, client):
        r = client.get(PROCESSED)
        assert r.status_code == 200
        body = r.json()
        assert body == {"items": [], "total": 0, "limit": 50, "offset": 0}


class TestGetDetail:
    def test_returns_full_audit_metadata(self, client):
        created = client.post(PROCESSED, json=_br_payload(name="auditme")).json()
        r = client.get(f"{PROCESSED}/{created['id']}")
        assert r.status_code == 200
        body = r.json()
        assert body["input_files"] == ["Datavardering.pdf", "LGH_utdrag.pdf"]
        assert body["extracted_values"]["objekt"].startswith("LGH 1303")
        assert body["final_values"]["objekt"].startswith("LGH 1303")
        assert "created_at" in body and "updated_at" in body

    def test_get_unknown_id_returns_404(self, client):
        r = client.get(f"{PROCESSED}/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 404


class TestPatch:
    def test_rename_updates_name_only(self, client):
        created = client.post(PROCESSED, json=_br_payload(name="old")).json()
        r = client.patch(f"{PROCESSED}/{created['id']}", json={"name": "renamed"})
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "renamed"
        # other fields preserved
        assert body["extracted_values"]["objekt"].startswith("LGH 1303")
        # updated_at moves forward (or equals — clock granularity); never goes back
        assert body["updated_at"] >= created["updated_at"]

    def test_patch_final_values_flips_edited_flag(self, client):
        created = client.post(PROCESSED, json=_br_payload(name="flip")).json()
        assert created["was_manually_edited"] is False
        new_final = dict(created["final_values"])
        new_final["marknadsvarde_kr"] = "3 100 000"
        r = client.patch(f"{PROCESSED}/{created['id']}", json={"final_values": new_final})
        assert r.status_code == 200
        assert r.json()["was_manually_edited"] is True

    def test_patch_final_back_to_extracted_clears_edited_flag(self, client):
        body = _br_payload(name="reset", final_override={"marknadsvarde_kr": "9 999 999"})
        created = client.post(PROCESSED, json=body).json()
        assert created["was_manually_edited"] is True
        r = client.patch(
            f"{PROCESSED}/{created['id']}",
            json={"final_values": created["extracted_values"]},
        )
        assert r.status_code == 200
        assert r.json()["was_manually_edited"] is False

    def test_patch_unknown_id_returns_404(self, client):
        r = client.patch(
            f"{PROCESSED}/00000000-0000-0000-0000-000000000000",
            json={"name": "nope"},
        )
        assert r.status_code == 404


class TestDelete:
    def test_delete_removes_row(self, client):
        created = client.post(PROCESSED, json=_br_payload(name="rm")).json()
        r = client.delete(f"{PROCESSED}/{created['id']}")
        assert r.status_code == 204
        assert client.get(f"{PROCESSED}/{created['id']}").status_code == 404

    def test_delete_unknown_id_returns_404(self, client):
        r = client.delete(f"{PROCESSED}/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 404


class TestExportCsv:
    def test_export_header_includes_metadata_and_flattened_values(self, client):
        client.post(PROCESSED, json=_br_payload(name="csv-1"))
        body2 = _br_payload(name="csv-2", final_override={"marknadsvarde_kr": "3 500 000"})
        client.post(PROCESSED, json=body2)

        r = client.get(f"{PROCESSED}/export.csv")
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]
        assert "attachment; filename=" in r.headers["content-disposition"]

        reader = csv.DictReader(io.StringIO(r.text))
        rows = list(reader)
        assert len(rows) == 2
        assert "name" in reader.fieldnames
        assert "was_manually_edited" in reader.fieldnames
        assert "input_files" in reader.fieldnames
        assert "final.objekt" in reader.fieldnames
        assert "extracted.objekt" in reader.fieldnames
        # the edited row has the divergent marknadsvärde
        edited = [r for r in rows if r["name"] == "csv-2"][0]
        assert edited["final.marknadsvarde_kr"] == "3 500 000"
        assert edited["extracted.marknadsvarde_kr"] == "3 050 000"
        # input_files is a JSON-encoded array
        assert json.loads(edited["input_files"]) == [
            "Datavardering.pdf",
            "LGH_utdrag.pdf",
        ]
        # nested comparable_sales got JSON-encoded too
        assert "Brf X" in edited["extracted.comparable_sales"]

    def test_export_empty_list_returns_header_only(self, client):
        r = client.get(f"{PROCESSED}/export.csv")
        assert r.status_code == 200
        text = r.text
        # exactly one line (the header), possibly with trailing newline
        assert len(text.strip().splitlines()) == 1
        assert "name" in text.split("\n", 1)[0]


# --------------------------------------------------------------------------
# Owner scoping + fail-closed on missing identity (system_3 #3096 / #3125)
# --------------------------------------------------------------------------

OWNER_A = 101
OWNER_B = 202


class TestFailClosedMissingIdentity:
    """No caller identity => 401 on every route, never served against the table."""

    def test_create_without_header_rejected(self, make_client):
        c = make_client(None)
        r = c.post(PROCESSED, json=_br_payload(name="x"))
        assert r.status_code == 401

    def test_list_without_header_rejected(self, make_client):
        assert make_client(None).get(PROCESSED).status_code == 401

    def test_export_without_header_rejected(self, make_client):
        assert make_client(None).get(f"{PROCESSED}/export.csv").status_code == 401

    def test_get_without_header_rejected(self, make_client):
        # Seed a row as a real owner, then probe it with no identity.
        owner = make_client(OWNER_A)
        created = owner.post(PROCESSED, json=_br_payload(name="seed")).json()
        r = make_client(None).get(f"{PROCESSED}/{created['id']}")
        assert r.status_code == 401

    def test_patch_without_header_rejected(self, make_client):
        owner = make_client(OWNER_A)
        created = owner.post(PROCESSED, json=_br_payload(name="seed")).json()
        r = make_client(None).patch(f"{PROCESSED}/{created['id']}", json={"name": "z"})
        assert r.status_code == 401

    def test_delete_without_header_rejected(self, make_client):
        owner = make_client(OWNER_A)
        created = owner.post(PROCESSED, json=_br_payload(name="seed")).json()
        r = make_client(None).delete(f"{PROCESSED}/{created['id']}")
        assert r.status_code == 401
        # …and the row is untouched.
        assert owner.get(f"{PROCESSED}/{created['id']}").status_code == 200


class TestOwnerScoping:
    """A's valuations are invisible and immutable to B across every verb."""

    def test_create_stamps_caller_as_owner_and_isolates_list(self, make_client):
        a = make_client(OWNER_A)
        b = make_client(OWNER_B)
        a.post(PROCESSED, json=_br_payload(name="a-1"))
        a.post(PROCESSED, json=_br_payload(name="a-2"))
        b.post(PROCESSED, json=_br_payload(name="b-1"))

        a_list = a.get(PROCESSED).json()
        b_list = b.get(PROCESSED).json()
        assert a_list["total"] == 2
        assert {i["name"] for i in a_list["items"]} == {"a-1", "a-2"}
        assert b_list["total"] == 1
        assert {i["name"] for i in b_list["items"]} == {"b-1"}

    def test_get_of_others_row_is_404(self, make_client):
        a = make_client(OWNER_A)
        b = make_client(OWNER_B)
        created = a.post(PROCESSED, json=_br_payload(name="a-only")).json()
        # Owner reads it fine…
        assert a.get(f"{PROCESSED}/{created['id']}").status_code == 200
        # …B gets 404 (not 403 — existence not confirmed).
        assert b.get(f"{PROCESSED}/{created['id']}").status_code == 404

    def test_patch_of_others_row_is_404_and_no_mutation(self, make_client):
        a = make_client(OWNER_A)
        b = make_client(OWNER_B)
        created = a.post(PROCESSED, json=_br_payload(name="a-name")).json()
        r = b.patch(f"{PROCESSED}/{created['id']}", json={"name": "hijacked"})
        assert r.status_code == 404
        # A's row is unchanged.
        assert a.get(f"{PROCESSED}/{created['id']}").json()["name"] == "a-name"

    def test_delete_of_others_row_is_404_and_no_deletion(self, make_client):
        a = make_client(OWNER_A)
        b = make_client(OWNER_B)
        created = a.post(PROCESSED, json=_br_payload(name="keepme")).json()
        assert b.delete(f"{PROCESSED}/{created['id']}").status_code == 404
        # Still there for the owner.
        assert a.get(f"{PROCESSED}/{created['id']}").status_code == 200

    def test_export_scopes_to_caller(self, make_client):
        a = make_client(OWNER_A)
        b = make_client(OWNER_B)
        a.post(PROCESSED, json=_br_payload(name="a-export"))
        b.post(PROCESSED, json=_br_payload(name="b-export"))

        rows_a = list(csv.DictReader(io.StringIO(a.get(f"{PROCESSED}/export.csv").text)))
        rows_b = list(csv.DictReader(io.StringIO(b.get(f"{PROCESSED}/export.csv").text)))
        assert [r["name"] for r in rows_a] == ["a-export"]
        assert [r["name"] for r in rows_b] == ["b-export"]
