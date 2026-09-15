"""OCR fallback for image-only PDFs.

The valuation extractor reads two *text* projections of a PDF (pdfplumber and
PyMuPDF). A scanned or photographed document carries no text layer, so both
projections come back empty and every content guard misses: what was uploaded
is a picture of the valuation, not the valuation. `no_text`
(`extraction.OUTCOME_NO_TEXT`) names that outcome, but until now the pipeline
could do nothing about it — the operator retyped every field by hand, and the
product's only answer was to ask the client for a different file (system_3
#5907).

This module is that fallback: raster each page and run Tesseract over the
image, so the existing guards and slot strategies get a text projection to
match against. It is invoked ONLY from `build_context`, and only when both
native projections are empty, so a digital PDF never rasters and never imports
the OCR engine at call time — the normal path pays nothing.

Two deliberate limits, recorded here because both surface downstream:
  * The Swedish language model (`swe`) is required — these are Swedish
    real-estate documents. It is installed in the image
    (`Dockerfile-Commander`), not bundled here.
  * OCR reconstructs *text*, never the pdfplumber word-box grid
    (`ParseContext.page1_words`), so grid-walking slot strategies still miss
    on a scan; guard- and label-stem strategies recover. That is a property of
    OCR, not a bug, so a `not_found` grid slot on an `ocr_used` row is
    expected.

Nothing here egresses document bytes: Tesseract runs on-cell, so a client's
scanned valuation is never sent to a third-party OCR service.
"""

from __future__ import annotations

from io import BytesIO

# 300 DPI is the low end of what Tesseract reads reliably. A scan is already
# the slow path (it only reaches here because no text layer was found), so we
# render generously rather than risk an unreadable raster.
_OCR_DPI = 300
_OCR_LANG = "swe"


def ocr_pdf_pages(
    pdf_bytes: bytes, *, dpi: int = _OCR_DPI, lang: str = _OCR_LANG
) -> list[str]:
    """Raster every page and OCR it, returning one text string per page.

    PyMuPDF does the raster (already a dependency — no poppler/pdf2image),
    Pillow hands Tesseract an image, `pytesseract` runs the OCR. A page that
    OCRs to nothing yields an empty string, so the caller can tell "OCR ran and
    found nothing" (a list of empty strings) from "OCR was never attempted"
    (the caller degraded to `[]` on an environment failure).

    Raises on a genuinely broken environment — the Tesseract binary or the
    language data absent — so the caller decides whether that degrades to
    `no_text` rather than this module swallowing an infrastructure fault. It
    does NOT raise on an unreadable page; that is an empty string.
    """
    import fitz
    import pytesseract
    from PIL import Image

    texts: list[str] = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=dpi)
            image = Image.open(BytesIO(pix.tobytes("png")))
            try:
                texts.append(pytesseract.image_to_string(image, lang=lang) or "")
            finally:
                image.close()
    return texts
