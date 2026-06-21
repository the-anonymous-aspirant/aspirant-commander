from pydantic import BaseModel, Field

from app.valuation_statement.classifier import DocumentType


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
    document_type: DocumentType
    filename: str
    fields: list[ExtractedFieldOut]
    comparable_sales: list[ComparableSale] = Field(default_factory=list)


class ExtractResponse(BaseModel):
    documents: list[ExtractionResultOut]
    operator_defaults: "OperatorDefaults"


class OperatorDefaults(BaseModel):
    """Persisted appraiser-identity fields surfaced to the review step."""
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
