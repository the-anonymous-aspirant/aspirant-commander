from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ExtractedFieldOut(BaseModel):
    key: str
    value: str | None
    confidence: str
    source_filename: str
    source_page: int | None = None
    note: str | None = None


class ComparableSale(BaseModel):
    """One row of UC Bostad's "Sålda bostadsrätter i området" table.

    Structured columns are best-effort — `raw` always carries the
    original line so the operator has a fallback when a row fails to
    parse cleanly.
    """
    forening: str | None = None
    area_m2: str | None = None
    balkong: str | None = None
    avgift_kr_manad: str | None = None
    arsavgift_kr: str | None = None
    pris_kr: str | None = None
    pris_per_m2: str | None = None
    salj_datum: str | None = None
    raw: str | None = None


class ExtractionDiagnosticsOut(BaseModel):
    """Why a document produced what it produced — no document content.

    Carries a content hash, the per-guard predicate outcomes and size
    metrics: enough to tell an uncovered layout from a broken strategy
    without retaining a page of someone's property details (#5359).
    """
    outcome: str
    content_sha256: str
    byte_length: int
    page_count: int
    page1_text_length: int
    full_text_length: int
    guards_matched: list[str] = Field(default_factory=list)
    guards_evaluated: dict[str, bool] = Field(default_factory=dict)
    value_fields_filled: int
    value_fields_total: int


class ExtractionResultOut(BaseModel):
    """Per-PDF result.

    `document_type` is no longer surfaced; `source_class` and
    `property_shape` ride in `fields` and the frontend reads them from
    there to route each PDF into the right docx slots.

    `outcome` is stated rather than left to be inferred from counting
    empty values: the review step has to be able to say "nothing was
    recognised in this file" without re-deriving it (#5359).
    """
    filename: str
    fields: list[ExtractedFieldOut]
    comparable_sales: list[ComparableSale] = Field(default_factory=list)
    outcome: str = "extracted"
    diagnostics: ExtractionDiagnosticsOut | None = None


class ExtractResponse(BaseModel):
    documents: list[ExtractionResultOut]
    operator_defaults: "OperatorDefaults"


class OperatorDefaults(BaseModel):
    """Persisted appraiser-identity fields surfaced to the review step."""
    ort: str | None = None
    maklare_namn: str | None = None
    maklare_titel: str | None = None
    foretag: str | None = None
    likviditet: str = "normal"


class GenerateRequest(BaseModel):
    # Identifier row.
    objekt: str
    objekt_short: str
    adress: str
    kommun: str
    upplatelseform: str
    mode: str = "bostadsratt"  # "bostadsratt" | "frikopt"

    # Source-clause dates. Send the date string for sources that were
    # actually uploaded; omit / None for absent sources.
    datavardering_date: str | None = None
    fastighetsutdrag_date: str | None = None
    lagenhetsforteckning_date: str | None = None

    # Body content + appraisal.
    bilder_note: str | None = None
    likviditet: str = "normal"
    marknadsvarde_kr: str
    intervall_kr: str

    # Footer.
    ort: str = ""
    datum: str
    maklare_namn: str
    maklare_titel: str
    foretag: str


ExtractResponse.model_rebuild()


# ---------- processed-valuations store ----------


class ProcessedValuationCreate(BaseModel):
    """Body for POST /valuation-statement/processed.

    Caller sends the extract output (`extracted_values`) and the values
    actually committed for the docx (`final_values`); divergence sets
    `was_manually_edited`. `name` is auto-filled from
    `<created_date>_<fastighetsbeteckning or objekt_short>` when omitted.
    """

    name: str | None = None
    input_files: list[str] = Field(default_factory=list)
    extracted_values: dict = Field(default_factory=dict)
    final_values: dict = Field(default_factory=dict)
    created_by: str | None = None


class ProcessedValuationUpdate(BaseModel):
    """PATCH body — every field optional, only present fields are applied."""

    name: str | None = None
    extracted_values: dict | None = None
    final_values: dict | None = None


class ProcessedValuationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    input_files: list[str]
    extracted_values: dict
    final_values: dict
    was_manually_edited: bool
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime


class ProcessedValuationListOut(BaseModel):
    items: list[ProcessedValuationOut]
    total: int
    limit: int
    offset: int
