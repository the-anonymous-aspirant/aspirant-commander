# Valuation Statement (Värdeutlåtande) tool.
#
# Extracts key/value pairs from a small set of Swedish property-document PDFs
# (datavärdering, lägenhetsförteckning, fastighetsutdrag) and populates the
# canonical Värdeutlåtande Word template for download.

from app.valuation_statement.classifier import (
    DocumentType,
    classify_pdf,
)
from app.valuation_statement.extraction import (
    ExtractedField,
    ExtractionResult,
    extract_document,
)

__all__ = [
    "DocumentType",
    "ExtractedField",
    "ExtractionResult",
    "classify_pdf",
    "extract_document",
]
