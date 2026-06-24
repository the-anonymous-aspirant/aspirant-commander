"""Field-first registry surfaced to the operator's 'Om verktyget' section.

Drives the JSON snapshot consumed at build time by aspirant-client's
About page (Wordweaver pattern; no runtime API). One row per docx
template slot, with the priority-ordered strategy chain rendered as
a compact human-readable list.

Replaces the per-DocumentType fingerprint+strategy table (operator
directive on system_3 #1113): the classifier-then-dispatch model is
gone, and the introspection surface follows the same shape change —
field-first, with each strategy carrying its own content-fingerprint
guard rather than a top-level dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.valuation_statement.field_extractor import SLOTS, Slot


@dataclass(frozen=True)
class StrategyDescription:
    name: str
    description: str


@dataclass(frozen=True)
class SlotDescription:
    key: str
    description: str
    strategies: tuple[StrategyDescription, ...]


def _describe_slot(slot: Slot) -> SlotDescription:
    return SlotDescription(
        key=slot.key,
        description=slot.description,
        strategies=tuple(
            StrategyDescription(name=s.name, description=s.note)
            for s in slot.strategies
        ),
    )


def get_transparency_registry() -> tuple[SlotDescription, ...]:
    """Return one SlotDescription per docx-template slot, chain-first."""
    return tuple(_describe_slot(s) for s in SLOTS)


def registry_as_dict() -> dict:
    """JSON-serialisable form of the registry for the build-time snapshot."""
    return {
        "slots": [
            {
                "key": s.key,
                "description": s.description,
                "strategies": [
                    {"name": st.name, "description": st.description}
                    for st in s.strategies
                ],
            }
            for s in get_transparency_registry()
        ]
    }
