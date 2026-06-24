# Valuation Statement (Värdeutlåtande) tool.
#
# Extracts key/value pairs from a small set of Swedish property-document PDFs
# (datavärdering, lägenhetsförteckning, fastighetsutdrag) and populates the
# canonical Värdeutlåtande Word template for download.

from app.valuation_statement.extraction import (
    ExtractedField,
    ExtractionResult,
    extract_document,
)

__all__ = [
    "ExtractedField",
    "ExtractionResult",
    "extract_document",
]
