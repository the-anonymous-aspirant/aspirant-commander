"""Per-slot extractor with priority-ordered strategy chains.

The docx output template (`TemplateFields`) defines a fixed slot list
— operator-pinned. Each parser exposes one `SlotExtractor` per slot
that the slot needs from PDFs. Every extractor walks its strategies
in priority order and returns the first non-None value.

This shape decouples WHAT we extract (the slot list, stable across
issuers) from HOW we extract it (the strategy library, grows freely
as new sample layouts appear). A new issuer's layout breaks one
strategy in a chain at most; the other strategies still try, and on
total miss the slot lands as `not_found` so the operator types it
manually — never a broken default.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.valuation_statement.extraction import ExtractedField


@dataclass(frozen=True)
class Strategy:
    """One way to fill a slot from a parsed page.

    `name` surfaces in `ExtractedField.note` so the CLI debugger and
    operator review can see which strategy fired (or none did).
    `confidence` defaults to "confident"; set to "uncertain" when the
    strategy is a heuristic / lossy regex / cross-field fallback that
    deserves operator verification.
    """

    name: str
    extract: Callable[["ParseContext"], str | None]
    confidence: str = "confident"


@dataclass(frozen=True)
class SlotExtractor:
    """One docx-template slot with its priority-ordered strategies.

    `note` is the slot-level explanation surfaced in the review step
    regardless of which strategy fired (e.g. "Includes the `(Tax<YY>)`
    qualifier"). The matched strategy name is appended.
    """

    slot_key: str
    strategies: tuple[Strategy, ...]
    note: str | None = None

    def run(self, ctx: "ParseContext", filename: str) -> ExtractedField:
        for strategy in self.strategies:
            value = strategy.extract(ctx)
            if value is not None:
                return ExtractedField(
                    key=self.slot_key,
                    value=value,
                    confidence=strategy.confidence,
                    source_filename=filename,
                    source_page=1,
                    note=_merge_note(self.note, strategy.name),
                )
        return ExtractedField(
            key=self.slot_key,
            value=None,
            confidence="not_found",
            source_filename=filename,
            source_page=None,
            note=self.note,
        )


def _merge_note(slot_note: str | None, strategy_name: str) -> str:
    tag = f"strategy: {strategy_name}"
    return f"{slot_note} ({tag})" if slot_note else tag


def run_slots(
    slots: tuple[SlotExtractor, ...],
    ctx: "ParseContext",
    filename: str,
) -> list[ExtractedField]:
    return [slot.run(ctx, filename) for slot in slots]


# `ParseContext` is defined in _context.py — forward-referenced in the
# Callable signature above so callers can import either order without
# a circular dependency.
from app.valuation_statement.parsers._context import ParseContext  # noqa: E402,F401
