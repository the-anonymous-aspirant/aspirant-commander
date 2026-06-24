"""Tests for the classifier + parser transparency registry.

Confidence covered:

  * Every DocumentType the classifier can return surfaces a description.
  * Each Category's fingerprint regexes round-trip as plain strings the
    operator can read.
  * SlotExtractor priority ordering is preserved on the rendered shape.
  * lgh_utdrag's label-stem registry is translated into the uniform
    slot/strategy shape with the synthesised slots appended.

The registry is consumed at build time by aspirant-client's
`scripts/regen-valuation-about.sh`, which serialises `registry_as_dict()`
into the bundled About snapshot (Wordweaver pattern; no runtime API).
"""

from __future__ import annotations

from app.valuation_statement.classifier import DocumentType
from app.valuation_statement.parsers import bostadsratt, smahus
from app.valuation_statement.transparency import (
    DocumentTypeDescription,
    get_transparency_registry,
    registry_as_dict,
)


def _by_type(reg: tuple[DocumentTypeDescription, ...]) -> dict[str, DocumentTypeDescription]:
    return {d.document_type: d for d in reg}


def test_registry_covers_every_classifier_document_type():
    reg = get_transparency_registry()
    by_type = _by_type(reg)

    expected = {
        DocumentType.DATAVARDERING_BR.value,
        DocumentType.DATAVARDERING_SMAHUS.value,
        DocumentType.LGH_UTDRAG.value,
        DocumentType.FASTIGHETSUTDRAG.value,
    }
    assert set(by_type.keys()) == expected
    for d in reg:
        assert d.title  # human-readable; no empty placeholder leaked


def test_br_fingerprints_round_trip_as_regex_strings():
    by_type = _by_type(get_transparency_registry())
    br = by_type[DocumentType.DATAVARDERING_BR.value]

    # Both BR categories (Fastighetsbyrån prose + UC tabular) appear, with
    # their raw regex pattern strings — not compiled Pattern objects — so
    # the operator can read them directly.
    cat_names = [c.name for c in br.categories]
    assert any("Fastighetsbyrån" in n for n in cat_names)
    assert any("UC Bostad" in n for n in cat_names)

    fb_prose = next(c for c in br.categories if "Fastighetsbyrån" in c.name)
    assert "VÄRDEUTLÅTANDE" in fb_prose.fingerprints
    assert any("Värderingsobjekt" in fp for fp in fb_prose.fingerprints)
    assert any("Bostadsr" in fp for fp in fb_prose.fingerprints)


def test_slot_strategy_ordering_matches_parser_registry():
    by_type = _by_type(get_transparency_registry())
    br = by_type[DocumentType.DATAVARDERING_BR.value]

    # Slot order must match the parser's _SLOTS so the operator sees
    # strategies in the priority order they actually fire.
    assert [s.slot_key for s in br.slots] == [s.slot_key for s in bostadsratt._SLOTS]

    # Strategy order within a slot must also match.
    for desc, source in zip(br.slots, bostadsratt._SLOTS):
        assert [st.name for st in desc.strategies] == [
            s.name for s in source.strategies
        ]


def test_smahus_strategies_present_in_priority_order():
    by_type = _by_type(get_transparency_registry())
    sh = by_type[DocumentType.DATAVARDERING_SMAHUS.value]

    assert [s.slot_key for s in sh.slots] == [s.slot_key for s in smahus._SLOTS]
    # Spot-check: `upplatelseform` for Småhus only has the prose strategy
    # (UC tabular doesn't expose it).
    upp = next(s for s in sh.slots if s.slot_key == "upplatelseform")
    assert len(upp.strategies) == 1


def test_lgh_utdrag_translates_label_stems_and_appends_synthesised_slots():
    by_type = _by_type(get_transparency_registry())
    lgh = by_type[DocumentType.LGH_UTDRAG.value]

    keys = [s.slot_key for s in lgh.slots]
    # Canonical keys from _LABEL_STEMS.
    assert "lgh_internal_hsb" in keys
    assert "lgh_skatteverket" in keys
    assert "forening_namn" in keys
    # Synthesised slots derived from the parser body, not from _LABEL_STEMS.
    assert "postnummer" in keys
    assert "postort" in keys
    assert "document_date" in keys

    # `lgh_skatteverket` has two stems (ligature-damaged + clean), so the
    # rendered slot lists both as separate strategies.
    skv = next(s for s in lgh.slots if s.slot_key == "lgh_skatteverket")
    assert len(skv.strategies) == 2


def test_fastighetsutdrag_classifier_only_no_extracted_slots():
    by_type = _by_type(get_transparency_registry())
    fu = by_type[DocumentType.FASTIGHETSUTDRAG.value]

    assert len(fu.categories) == 1
    assert fu.slots == ()


def test_registry_as_dict_is_json_serialisable_and_shape_stable():
    import json

    payload = registry_as_dict()
    # Round-trip via JSON to assert no Pattern / dataclass leaked through.
    json.loads(json.dumps(payload))

    assert set(payload.keys()) == {"document_types"}
    for d in payload["document_types"]:
        assert set(d.keys()) == {"document_type", "title", "categories", "slots"}
        for c in d["categories"]:
            assert set(c.keys()) == {"name", "fingerprints"}
            assert all(isinstance(fp, str) for fp in c["fingerprints"])
        for s in d["slots"]:
            assert set(s.keys()) == {"slot_key", "note", "strategies"}
            for st in s["strategies"]:
                assert set(st.keys()) == {"name", "confidence"}
