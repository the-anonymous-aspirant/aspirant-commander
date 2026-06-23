"""Content-based classifier for Swedish property-document PDFs.

Returns the document family by walking the CATEGORIES table top-to-bottom
and returning the first category whose page-1 fingerprints all match.
The classifier reads PDF content only — filename, file size, and other
out-of-band signals are ignored. See docs/VALUATION_CLASSIFIER_AUDIT.md
for the source fingerprints.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class DocumentType(str, Enum):
    DATAVARDERING_BR = "datavardering_br"
    DATAVARDERING_SMAHUS = "datavardering_smahus"
    VARDEUTLATANDE_NORTHMILL_BR = "vardeutlatande_northmill_br"
    VARDEUTLATANDE_NORTHMILL_SMAHUS = "vardeutlatande_northmill_smahus"
    FASTIGHETSUTDRAG = "fastighetsutdrag"
    LGH_UTDRAG = "lgh_utdrag"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Category:
    document_type: DocumentType
    name: str
    fingerprints: tuple[re.Pattern, ...]


# Northmill variants share the all-caps `VÄRDEUTLÅTANDE` banner with a
# bank-name line, so they are checked before the UC `Värdeutlåtande` /
# `Bostadsrätt|Småhus` banner variants — otherwise the UC fingerprints
# would also match a Northmill PDF on the literal word `Värdeutlåtande`.
CATEGORIES: tuple[Category, ...] = (
    Category(
        document_type=DocumentType.VARDEUTLATANDE_NORTHMILL_BR,
        name="Northmill Värdeutlåtande — Bostadsrätt",
        fingerprints=(
            re.compile(r"VÄRDEUTLÅTANDE"),
            re.compile(r"Northmill\s+Bank"),
            re.compile(r"Uppl[åa]telseform\s*:\s*Bostadsr[äa]tt", re.IGNORECASE),
        ),
    ),
    Category(
        document_type=DocumentType.VARDEUTLATANDE_NORTHMILL_SMAHUS,
        name="Northmill Värdeutlåtande — Friköpt",
        fingerprints=(
            re.compile(r"VÄRDEUTLÅTANDE"),
            re.compile(r"Northmill\s+Bank"),
            re.compile(r"Uppl[åa]telseform\s*:\s*Frik[öo]pt", re.IGNORECASE),
        ),
    ),
    Category(
        document_type=DocumentType.DATAVARDERING_BR,
        name="UC Bostad Värdeutlåtande — Bostadsrätt",
        fingerprints=(
            re.compile(r"V[äa]rdeutl[åa]tande\s+Bostadsr[äa]tt", re.IGNORECASE),
        ),
    ),
    Category(
        document_type=DocumentType.DATAVARDERING_SMAHUS,
        name="UC Bostad Värdeutlåtande — Småhus",
        fingerprints=(
            re.compile(r"V[äa]rdeutl[åa]tande\s+Sm[åa]hus", re.IGNORECASE),
        ),
    ),
    Category(
        document_type=DocumentType.FASTIGHETSUTDRAG,
        name="Lantmäteriet Fastighetsrapport Plus R",
        fingerprints=(
            re.compile(r"Fastighetsrapport\s+Plus\s+R", re.IGNORECASE),
        ),
    ),
    Category(
        document_type=DocumentType.LGH_UTDRAG,
        name="Bostadsrättsförening lägenhetsförteckning",
        fingerprints=(
            re.compile(r"L[äa]genhetsuppgi.{1,3}ter", re.IGNORECASE),
            re.compile(r"Bostadsr[äa].{1,3}tsf[öo]rening", re.IGNORECASE),
        ),
    ),
)


def classify_text(page1_text: str) -> tuple[DocumentType, list[str]]:
    """Classify a Värdeutlåtande document from its first-page text.

    Returns the matched `DocumentType` and the list of fingerprint
    patterns that matched (as raw regex strings) — the matched-pattern
    list is what the CLI debugger shows the operator when a new sample
    lands. Returns `(UNKNOWN, [])` when no category matches.
    """
    collapsed = re.sub(r"[ \t]+", " ", page1_text)
    for category in CATEGORIES:
        matched = [p.pattern for p in category.fingerprints if p.search(collapsed)]
        if len(matched) == len(category.fingerprints):
            return category.document_type, matched
    return DocumentType.UNKNOWN, []


def classify_pdf(pdf_bytes: bytes) -> DocumentType:
    """Classify the PDF by reading its first-page text.

    The classifier is content-only — callers must not pass a filename or
    other out-of-band hint. The deterministic kernel is `classify_text`;
    this wrapper exists so callers can hand it raw bytes.
    """
    page1_text = _read_first_page_text(pdf_bytes)
    document_type, _matched = classify_text(page1_text)
    return document_type


def _read_first_page_text(pdf_bytes: bytes) -> str:
    # PyMuPDF copes with the wider variety of CMaps we see in HSB and
    # Lantmäteriet printouts than pdfplumber does. We only need a few
    # keywords, so the per-document parser overhead is acceptable here.
    import fitz

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        if doc.page_count == 0:
            return ""
        return doc[0].get_text() or ""
