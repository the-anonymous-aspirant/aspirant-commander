"""Stub parser for Lantmäteriet's fastighetsutdrag.

The classifier and template both already understand fastighetsutdrag —
`fastighetsutdrag_date` rides in `GenerateRequest` and renders as the
"fastighetsutdrag per datum {date}" body clause. But no field is
auto-extractable yet (no representative PDF mapped), so this parser
just tags the upload with the right document_type and returns an empty
`fields` list. The operator types the date during the review step.

Until a real parser lands, the route still needs *a* callable here:
without it `extract_document()` raises ImportError on every upload
that classifies as fastighetsutdrag and the whole `/extract` request
500s — blocking the entire Värdeutlåtande flow whenever the operator
includes a Lantmäteriet PDF in the batch.
"""

from __future__ import annotations

from app.valuation_statement.classifier import DocumentType
from app.valuation_statement.extraction import ExtractionResult


def parse(pdf_bytes: bytes, filename: str) -> ExtractionResult:
    return ExtractionResult(
        document_type=DocumentType.FASTIGHETSUTDRAG,
        filename=filename,
    )
