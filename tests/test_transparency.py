"""Tests for the field-first transparency registry.

The registry drives the snapshot the aspirant-client About page renders.
Confidence covered:
  * Every docx-template slot is surfaced with its description.
  * Strategy order is preserved exactly as declared in the extractor.
  * Each strategy carries a human-readable description (the rendered
    Right-column text of the Om verktyget table).
  * The JSON form is shaped as `{slots: [{key, description, strategies: [...]}]}`.
"""

from __future__ import annotations

from app.valuation_statement.field_extractor import SLOTS
from app.valuation_statement.transparency import (
    get_transparency_registry,
    registry_as_dict,
)


def test_registry_covers_every_extractor_slot():
    reg = get_transparency_registry()
    assert [s.key for s in reg] == [s.key for s in SLOTS]
    for desc in reg:
        assert desc.description, f"slot {desc.key} missing description"


def test_strategy_order_matches_extractor():
    reg = get_transparency_registry()
    for desc, source in zip(reg, SLOTS):
        assert [st.name for st in desc.strategies] == [
            s.name for s in source.strategies
        ], f"chain order drifted for slot {desc.key}"


def test_every_strategy_has_a_human_description():
    reg = get_transparency_registry()
    for desc in reg:
        for st in desc.strategies:
            assert st.description, (
                f"strategy {desc.key}.{st.name} missing the human-readable "
                f"description that the Om verktyget table renders"
            )


def test_registry_as_dict_is_field_first():
    data = registry_as_dict()
    assert set(data) == {"slots"}
    keys = [slot["key"] for slot in data["slots"]]
    assert "objekt" in keys
    assert "marknadsvarde_kr" in keys
    assert "source_class" in keys
    assert "property_shape" in keys
    # No leftover document_type fingerprint table — the field-first
    # rewrite must drop it (operator directive on #1113).
    assert "document_types" not in data
    assert "categories" not in data


def test_objekt_chain_lists_all_five_source_layouts():
    reg = get_transparency_registry()
    objekt = next(s for s in reg if s.key == "objekt")
    names = [st.name for st in objekt.strategies]
    # Five strategies in priority order — prose first (highest signal),
    # UC BR + UC Småhus + Fastighetsrapport + LGH after.
    assert names == [
        "prose_objekt_bullet",
        "uc_br_assemble_from_cells",
        "uc_smahus_fastighetsbeteckning",
        "fastighetsrapport_beteckning",
        "lgh_assemble_from_cells",
    ]
