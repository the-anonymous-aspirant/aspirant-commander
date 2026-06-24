"""Result dataclasses surfaced to callers of the field-first extractor."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExtractedField:
    """A single key/value pair surfaced to the review step.

    `confidence` is one of:
      * "confident"  — single unambiguous match in the source
      * "uncertain"  — heuristic / multiple candidates / regex fallback
      * "not_found"  — source scanned but no value found
    """

    key: str
    value: str | None
    confidence: str
    source_filename: str
    source_page: int | None = None
    note: str | None = None


@dataclass
class ExtractionResult:
    filename: str
    fields: list[ExtractedField] = field(default_factory=list)
    extras: dict = field(default_factory=dict)


def extract_document(pdf_bytes: bytes, filename: str) -> ExtractionResult:
    """Run the field-first strategy chains over a PDF.

    The classifier-then-per-type-parser dispatch (#1060/#1079) is gone
    (operator directive 2026-06-24 on #1113): a single chain runs per
    slot on every PDF. Each strategy is a guarded predicate that only
    fires when its content fingerprint matches. Result fields whose
    chain misses land as `not_found` so the operator types them during
    review.
    """
    from app.valuation_statement.field_extractor import extract_fields

    return extract_fields(pdf_bytes, filename)
