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


class ExtractionResultOut(BaseModel):
    """Per-PDF result.

    `document_type` is no longer surfaced; `source_class` and
    `property_shape` ride in `fields` and the frontend reads them from
    there to route each PDF into the right docx slots.
    """
    filename: str
    fields: list[ExtractedFieldOut]
    comparable_sales: list[ComparableSale] = Field(default_factory=list)


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
