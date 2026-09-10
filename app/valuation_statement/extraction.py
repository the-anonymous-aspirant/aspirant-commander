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


# The two slots that carry semantics rather than a value the operator would
# otherwise type. A document where only these fired still hands the review
# step a blank form, so they do not count toward "something was extracted".
SEMANTIC_PRIMITIVE_KEYS = frozenset({"source_class", "property_shape"})

OUTCOME_EXTRACTED = "extracted"
OUTCOME_PARTIAL = "partial"
OUTCOME_RECOGNISED_NO_FIELDS = "recognised_no_fields"
OUTCOME_UNRECOGNISED = "unrecognised"
OUTCOME_NO_TEXT = "no_text"


@dataclass
class ExtractionDiagnostics:
    """What the extractor saw, with no document content in it.

    These are real client valuation documents, so nothing here carries text
    from the PDF: a content hash to correlate a report with an upload, the
    per-guard predicate outcomes, and size metrics. That is enough to tell
    the two failure branches apart without retaining a page of someone's
    property details (#5359 item 5).

    `outcome` is the discrimination the incident turned on:
      * `extracted`             — every value slot the document's
                                  `property_shape` is expected to fill did,
                                  so the review step opens on a complete form.
      * `partial`               — at least one value slot filled, but a slot
                                  the shape is expected to fill missed (#5662).
                                  The operator retypes only the missed slots,
                                  which looks like success from HTTP 200 and
                                  is silent unless this outcome is separated
                                  out; `missed_expected_slots` names which.
      * `recognised_no_fields`  — a content guard matched and every value
                                  slot still missed: a strategy bug.
      * `unrecognised`          — no guard matched at all, on a document
                                  that did carry text: a layout the strategy
                                  library does not cover.
      * `no_text`               — neither text projection yielded a single
                                  non-whitespace character. A scan or a
                                  photograph; no fingerprint can ever match
                                  it, so it is not a coverage gap and adding
                                  a strategy would not help.
    """

    outcome: str
    content_sha256: str
    byte_length: int
    page_count: int
    page1_text_length: int
    full_text_length: int
    guards_matched: list[str]
    guards_evaluated: dict[str, bool]
    value_fields_filled: int
    value_fields_total: int
    # Slot KEYS (never values) the document's shape was expected to fill and
    # did not. Empty on a complete or a total-miss run; non-empty is what makes
    # `partial` a partial. Keys are schema identifiers, not document content.
    missed_expected_slots: list[str] = field(default_factory=list)


@dataclass
class ExtractionResult:
    filename: str
    fields: list[ExtractedField] = field(default_factory=list)
    extras: dict = field(default_factory=dict)
    diagnostics: ExtractionDiagnostics | None = None


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
