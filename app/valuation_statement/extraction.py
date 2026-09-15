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
      * `no_text`               — neither NATIVE text projection yielded a
                                  single non-whitespace character. A scan or a
                                  photograph. Since #5907 the OCR fallback runs
                                  on this branch: if it recovers a value the
                                  outcome becomes `extracted`/`partial` with
                                  `ocr_used` set; `no_text` now means the
                                  native projections were empty AND OCR either
                                  recovered nothing or was unavailable. Adding
                                  a text-matching strategy still would not help
                                  a row where OCR recovered nothing — there is
                                  no text for any fingerprint to fire on — but
                                  `ocr_used` tells the two apart.
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
    # True when the text projections were OCR-rebuilt from a rasterised scan
    # because both native projections were empty (#5907). Distinguishes a
    # `no_text` row where OCR was tried and recovered nothing from one where
    # OCR was never attempted, and marks an `extracted`/`partial` row whose
    # values came from OCR — those values are surfaced as `uncertain`.
    ocr_used: bool = False
    # For an image-only PDF (native projections empty), which kind: a digital
    # PDF re-printed to outlined glyphs (`reprinted_vector` — the answer is to
    # upload the original), a photo/scan (`raster_scan` — OCR territory), or
    # `unknown`. None when the document carried text. The client keys its hint
    # on this and treats absent/`unknown` as "keep the current copy" (#5910).
    no_text_subkind: str | None = None


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


# Per-page OCR wall-time by sub-kind, seconds (#5915). A starting point measured
# at the served endpoint: reprinted_vector ~8s/page (rendering outlined glyphs
# is the expensive half), raster_scan ~4s/page; unknown takes the conservative
# middle. These are re-measurable constants, not a budget — revise here.
OCR_SECONDS_PER_PAGE = {
    "reprinted_vector": 8,
    "raster_scan": 4,
    "unknown": 6,
}


@dataclass
class ExtractionDecision:
    """The cheap half of extraction, surfaced before OCR runs (#5915)."""

    ocr_required: bool
    no_text_subkind: str | None
    page_count: int
    estimated_ocr_seconds: int


def decide_extraction(pdf_bytes: bytes) -> ExtractionDecision:
    """Decide whether OCR is required and estimate its duration, WITHOUT OCR.

    Detects a text layer, classifies the image-only sub-kind, and counts pages —
    everything the extractor knows within ~2s. `/extract` is a single blocking
    POST that returns only when the ~24s OCR is done; this is the same decision,
    made cheaply, so the client can announce the image-scanning phase and
    estimate it instead of spinning silently (#5915). Estimate 0 / sub-kind None
    when OCR is not required.

    Text detection is fitz-only, NOT pdfplumber: on a re-printed vector PDF
    pdfplumber's `extract_text` spends ~18s walking thousands of outlined-glyph
    paths to return zero characters, which would put the "fast decision" at ~17s
    and defeat the whole point (caught dogfooding DV.pdf, #5915). fitz reads the
    same text layer in ~0.25s, and fitz-empty is a reliable OCR trigger: every
    text-bearing PDF this pipeline sees — including HSB's lägenhetsförteckning,
    which fitz renders as ligature-damaged but non-empty text — leaves fitz
    non-empty, and only the two OCR cases (outlined-vector, raster scan) leave it
    empty. If a document ever had a pdfplumber-only text layer, decide would say
    "scanning" and `/extract` would then read it and skip OCR — a brief cosmetic
    mismatch, never a wrong extraction.
    """
    import fitz

    from app.valuation_statement._ocr import classify_no_text_subkind

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        page_count = doc.page_count
        fitz_native = "\n".join((page.get_text() or "") for page in doc)

    ocr_required = not fitz_native.strip()
    subkind = classify_no_text_subkind(pdf_bytes) if ocr_required else None
    per_page = (
        OCR_SECONDS_PER_PAGE.get(subkind or "unknown", OCR_SECONDS_PER_PAGE["unknown"])
        if ocr_required
        else 0
    )
    return ExtractionDecision(
        ocr_required=ocr_required,
        no_text_subkind=subkind,
        page_count=page_count,
        estimated_ocr_seconds=per_page * page_count,
    )
