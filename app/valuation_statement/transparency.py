"""Read-only introspection of the classifier + parser strategy registry.

Powers the operator-facing 'About' transparency surface: a single render
of the canonical config (classifier `CATEGORIES` + each parser's slot
registry) so the operator can see WHY a given PDF was classified the way
it was and WHY each populated/empty field came out as it did.

Hand-maintained markdown would drift the moment a strategy was added;
this module reads the live registries so the surface auto-reflects every
parser change.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.valuation_statement.classifier import CATEGORIES, DocumentType
from app.valuation_statement.parsers._strategy import SlotExtractor


@dataclass(frozen=True)
class StrategyDescription:
    name: str
    confidence: str


@dataclass(frozen=True)
class SlotDescription:
    slot_key: str
    note: str | None
    strategies: tuple[StrategyDescription, ...]


@dataclass(frozen=True)
class CategoryDescription:
    name: str
    fingerprints: tuple[str, ...]


@dataclass(frozen=True)
class DocumentTypeDescription:
    document_type: str
    title: str
    categories: tuple[CategoryDescription, ...]
    slots: tuple[SlotDescription, ...]


# Operator-facing display order — surfaces datavärdering families first
# (most common in the operator's workflow), then the supporting documents.
_DISPLAY_ORDER: tuple[DocumentType, ...] = (
    DocumentType.DATAVARDERING_BR,
    DocumentType.DATAVARDERING_SMAHUS,
    DocumentType.LGH_UTDRAG,
    DocumentType.FASTIGHETSUTDRAG,
)


_DOCUMENT_TYPE_TITLES: dict[DocumentType, str] = {
    DocumentType.DATAVARDERING_BR: "Datavärdering Bostadsrätt",
    DocumentType.DATAVARDERING_SMAHUS: "Datavärdering Småhus",
    DocumentType.LGH_UTDRAG: "Lägenhetsförteckning (Bostadsrättsförening)",
    DocumentType.FASTIGHETSUTDRAG: "Fastighetsutdrag (Lantmäteriet)",
}


def _describe_slot_extractors(
    slots: tuple[SlotExtractor, ...],
) -> tuple[SlotDescription, ...]:
    return tuple(
        SlotDescription(
            slot_key=s.slot_key,
            note=s.note,
            strategies=tuple(
                StrategyDescription(name=st.name, confidence=st.confidence)
                for st in s.strategies
            ),
        )
        for s in slots
    )


def _describe_lgh_utdrag_slots() -> tuple[SlotDescription, ...]:
    """Translate `_LABEL_STEMS` into the uniform slot/strategy shape.

    The lgh_utdrag parser doesn't use SlotExtractor — its strategy IS the
    label-stem table (HSB's ligature-damaged labels stem to the same key
    as the clean form). One canonical key may be reachable from multiple
    stems; each stem surfaces as its own strategy entry so the operator
    can see which ligature variants are tolerated.
    """
    from app.valuation_statement.parsers import lgh_utdrag

    by_key: dict[str, list[str]] = {}
    for stem, key in lgh_utdrag._LABEL_STEMS.items():
        by_key.setdefault(key, []).append(stem)

    slots: list[SlotDescription] = []
    for key, stems in by_key.items():
        strategies = tuple(
            StrategyDescription(
                name=f"label stem '{stem}' → next non-label line",
                confidence="confident",
            )
            for stem in stems
        )
        slots.append(
            SlotDescription(
                slot_key=key,
                note=(
                    "Multiple stems map to the same key — HSB's font drops "
                    "`ff`/`ft`/`tt` ligatures, so both clean and damaged "
                    "spellings are tolerated."
                )
                if len(stems) > 1
                else None,
                strategies=strategies,
            )
        )

    # Synthesised slots that don't come from _LABEL_STEMS.
    slots.append(
        SlotDescription(
            slot_key="postnummer",
            note="Split from the 5-digit prefix on the line after 'Adress'.",
            strategies=(
                StrategyDescription(
                    name="adress-followup-line: leading 5-digit run",
                    confidence="confident",
                ),
            ),
        )
    )
    slots.append(
        SlotDescription(
            slot_key="postort",
            note=(
                "Split from the locality token on the line after 'Adress' "
                "(reformatted from UPPER to TitleCase to match the template)."
            ),
            strategies=(
                StrategyDescription(
                    name="adress-followup-line: trailing locality, TitleCased",
                    confidence="confident",
                ),
            ),
        )
    )
    slots.append(
        SlotDescription(
            slot_key="document_date",
            note="Date the lägenhetsförteckning extract was issued.",
            strategies=(
                StrategyDescription(
                    name="'Utskriftsdatum: YYYY-MM-DD' header regex",
                    confidence="confident",
                ),
            ),
        )
    )
    return tuple(slots)


def _slots_for(doc_type: DocumentType) -> tuple[SlotDescription, ...]:
    if doc_type == DocumentType.DATAVARDERING_BR:
        from app.valuation_statement.parsers import bostadsratt

        return _describe_slot_extractors(bostadsratt._SLOTS)
    if doc_type == DocumentType.DATAVARDERING_SMAHUS:
        from app.valuation_statement.parsers import smahus

        return _describe_slot_extractors(smahus._SLOTS)
    if doc_type == DocumentType.LGH_UTDRAG:
        return _describe_lgh_utdrag_slots()
    # FASTIGHETSUTDRAG is a classifier-only stub (no automatic extraction
    # yet); operator types every field during the review step.
    return ()


def get_transparency_registry() -> tuple[DocumentTypeDescription, ...]:
    cats_by_type: dict[DocumentType, list[CategoryDescription]] = {}
    for c in CATEGORIES:
        cats_by_type.setdefault(c.document_type, []).append(
            CategoryDescription(
                name=c.name,
                fingerprints=tuple(p.pattern for p in c.fingerprints),
            )
        )

    return tuple(
        DocumentTypeDescription(
            document_type=doc_type.value,
            title=_DOCUMENT_TYPE_TITLES[doc_type],
            categories=tuple(cats_by_type.get(doc_type, ())),
            slots=_slots_for(doc_type),
        )
        for doc_type in _DISPLAY_ORDER
    )


def registry_as_dict() -> dict:
    """JSON-serialisable form of the registry for the HTTP route."""
    return {
        "document_types": [
            {
                "document_type": d.document_type,
                "title": d.title,
                "categories": [
                    {"name": c.name, "fingerprints": list(c.fingerprints)}
                    for c in d.categories
                ],
                "slots": [
                    {
                        "slot_key": s.slot_key,
                        "note": s.note,
                        "strategies": [
                            {"name": st.name, "confidence": st.confidence}
                            for st in s.strategies
                        ],
                    }
                    for s in d.slots
                ],
            }
            for d in get_transparency_registry()
        ]
    }
