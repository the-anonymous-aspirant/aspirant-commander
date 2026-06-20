from dataclasses import dataclass, field

from app.valuation_statement.classifier import DocumentType


@dataclass
class ExtractedField:
    """A single key/value pair surfaced to the review step.

    `confidence` is one of:
      * "confident"  — single unambiguous match in the source
      * "uncertain"  — heuristic / multiple candidates / regex fallback
      * "not_found"  — source scanned but no value found (review step paints red)
    """

    key: str
    value: str | None
    confidence: str
    source_filename: str
    source_page: int | None = None
    note: str | None = None


@dataclass
class ExtractionResult:
    document_type: DocumentType
    filename: str
    fields: list[ExtractedField] = field(default_factory=list)
    extras: dict = field(default_factory=dict)


def extract_document(
    pdf_bytes: bytes,
    document_type: DocumentType,
    filename: str,
) -> ExtractionResult:
    """Dispatch to the per-type parser; UNKNOWN returns an empty result."""
    if document_type == DocumentType.DATAVARDERING:
        from app.valuation_statement.parsers import datavardering

        return datavardering.parse(pdf_bytes, filename)
    if document_type == DocumentType.LGH_UTDRAG:
        from app.valuation_statement.parsers import lgh_utdrag

        return lgh_utdrag.parse(pdf_bytes, filename)
    if document_type == DocumentType.FASTIGHETSUTDRAG:
        from app.valuation_statement.parsers import fastighetsutdrag

        return fastighetsutdrag.parse(pdf_bytes, filename)
    return ExtractionResult(document_type=document_type, filename=filename)
