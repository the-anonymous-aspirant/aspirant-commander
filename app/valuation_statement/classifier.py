import re
from enum import Enum
from io import BytesIO


class DocumentType(str, Enum):
    DATAVARDERING = "datavardering"
    LGH_UTDRAG = "lgh_utdrag"
    FASTIGHETSUTDRAG = "fastighetsutdrag"
    UNKNOWN = "unknown"


# Fingerprints applied to the first page's plain text (case-insensitive,
# whitespace-collapsed). Order matters: first match wins.
_FINGERPRINTS: list[tuple[DocumentType, re.Pattern]] = [
    (
        DocumentType.DATAVARDERING,
        re.compile(r"v[äa]rdeutl[åa]tande.*bostadsr[äa]tt", re.IGNORECASE | re.DOTALL),
    ),
    (
        DocumentType.LGH_UTDRAG,
        re.compile(r"l[äa]genhetsuppgi.+ter|ska.+teverkets\s+lgh-nr", re.IGNORECASE),
    ),
    (
        DocumentType.FASTIGHETSUTDRAG,
        re.compile(r"fastighetsutdrag|lantm[äa]teriet|fastighetsregister", re.IGNORECASE),
    ),
]


def classify_pdf(pdf_bytes: bytes, filename: str | None = None) -> DocumentType:
    """Best-effort classification of a Swedish property-document PDF.

    Reads the first page only; if the page-1 fingerprint match is ambiguous
    the filename hint is used as a tiebreaker. Returns UNKNOWN when no
    fingerprint matches.
    """
    page1_text = _read_first_page_text(pdf_bytes)
    collapsed = re.sub(r"\s+", " ", page1_text)

    for doc_type, pattern in _FINGERPRINTS:
        if pattern.search(collapsed):
            return doc_type

    if filename:
        lower = filename.lower()
        if "datav" in lower:
            return DocumentType.DATAVARDERING
        if "lgh" in lower or "lägen" in lower:
            return DocumentType.LGH_UTDRAG
        if "fastighet" in lower:
            return DocumentType.FASTIGHETSUTDRAG

    return DocumentType.UNKNOWN


def _read_first_page_text(pdf_bytes: bytes) -> str:
    # PyMuPDF copes with the wider variety of CMaps we see in HSB/Lantmäteriet
    # printouts than pdfplumber does. We only need a few keywords, so the
    # per-document parser overhead is acceptable here.
    import fitz

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        if doc.page_count == 0:
            return ""
        return doc[0].get_text() or ""
