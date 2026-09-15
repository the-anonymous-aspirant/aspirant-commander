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


# --- OCR word-box grid (#5932) -----------------------------------------------
#
# The module above hands back linearised *text*. The positional slot strategies
# (`_uc_word_below`, `_label_text_below_in_column`) do not read text — they walk
# a word-box GRID (`ParseContext.page1_words`), reading the cell BELOW a label by
# coordinate. OCR linearisation destroys that geometry, so on a scan the grid is
# empty and every positional strategy misses even when the label and value both
# survived the scan (#5909 recovered exactly one slot with a per-slot linear
# hack; #5932 is the general form).
#
# `ocr_pdf_words` rebuilds the grid from `image_to_data`, which — unlike
# `image_to_string` — carries a box per word. Two calibrations make it a
# drop-in for pdfplumber's grid, so the strategies run unchanged:
#
#   * COORDINATES IN POINTS. pdfplumber word boxes are in PDF points; a 300-DPI
#     raster's boxes are in pixels. Scaling by 72/dpi puts them back in points,
#     so the strategies' point tolerances (`_ROW_TOL`, `_COL_TOL`,
#     `_MAX_VALUE_BELOW`) apply with no change.
#   * CELLS, NOT WORDS, GLUED SPACELESS. pdfplumber renders a table cell as ONE
#     spaceless token (`BengtsforsNÄRSIDAN1:21`) because these PDFs carry no space
#     glyphs inside a cell; `image_to_data` splits the same cell on its visual
#     spaces. So we reassemble same-line words into one cell token glued WITHOUT
#     spaces, exactly matching pdfplumber — this matters because some strategies
#     match multi-word labels by their spaceless pdfplumber form (e.g.
#     `intervall_kr` looks for the label token `Osäkerhetuppåt`, which OCR reads
#     as two words `Osäkerhet uppåt`), and a spaced glue would break the label
#     match. `_canonical_via_fitz` restores the human-readable spacing of a value
#     from the text projection, as on the digital path (with an OCR arm — see its
#     docstring — because OCR linearises a whole table row onto one line, which
#     the digital line-equality lookup cannot match). The split point is a
#     horizontal gutter: measured on the real FR/UCB/Datavärdering scans, gaps
#     inside a cell are 2.4-4.1 pt while gaps between columns are 43-210 pt, so a
#     12 pt gutter separates the two with a 10x margin on both sides and cannot
#     glue one column's value onto the next.
_OCR_CELL_GUTTER_PT = 12.0


def ocr_pdf_words(
    pdf_bytes: bytes, *, dpi: int = _OCR_DPI, lang: str = _OCR_LANG
) -> tuple[dict, ...]:
    """Rebuild page 1's word-box grid from OCR, shaped like pdfplumber's.

    Returns a tuple of cell dicts with the `text`/`x0`/`x1`/`top`/`bottom` keys
    the positional strategies read, coordinates in PDF points and cells glued to
    match pdfplumber's tokenisation (see the module note above). Page 1 only, for
    parity with the digital `ParseContext.page1_words`.

    Raises on a broken OCR environment, exactly as `ocr_pdf_pages` does, so the
    caller (`build_context`) makes the one degrade-to-empty decision.
    """
    import fitz
    import pytesseract
    from PIL import Image

    scale = 72.0 / dpi
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        if doc.page_count == 0:
            return ()
        pix = doc[0].get_pixmap(dpi=dpi)
        image = Image.open(BytesIO(pix.tobytes("png")))
        try:
            data = pytesseract.image_to_data(
                image, lang=lang, output_type=pytesseract.Output.DICT
            )
        finally:
            image.close()

    # One entry per recognised word, in points, tagged with the Tesseract line
    # it belongs to (block/paragraph/line) so cells never span two rows.
    words: list[dict] = []
    for i in range(len(data["text"])):
        text = (data["text"][i] or "").strip()
        # conf is -1 for the layout rows (block/line placeholders) that carry no
        # text; skip those and any blank token.
        if not text or int(data["conf"][i]) < 0:
            continue
        left = data["left"][i] * scale
        top = data["top"][i] * scale
        words.append(
            {
                "text": text,
                "x0": left,
                "x1": left + data["width"][i] * scale,
                "top": top,
                "bottom": top + data["height"][i] * scale,
                "_line": (data["block_num"][i], data["par_num"][i], data["line_num"][i]),
            }
        )

    return tuple(_reassemble_cells(words))


def _reassemble_cells(words: list[dict]) -> list[dict]:
    """Glue same-line words into pdfplumber-shaped cell tokens.

    Within each Tesseract line, walk words left to right and start a new cell
    whenever the gap from the previous word exceeds `_OCR_CELL_GUTTER_PT`. A cell
    token carries its words concatenated without spaces (pdfplumber's shape, so
    the strategies' label matching and `_canonical_via_fitz` restoration apply
    unchanged), anchored at the leftmost `x0` and spanning to the rightmost `x1`.
    """
    from itertools import groupby

    words_sorted = sorted(words, key=lambda w: (w["_line"], w["x0"]))
    cells: list[dict] = []
    for _line, group in groupby(words_sorted, key=lambda w: w["_line"]):
        run: list[dict] = []
        prev_x1: float | None = None
        for w in group:
            if prev_x1 is not None and w["x0"] - prev_x1 > _OCR_CELL_GUTTER_PT:
                cells.append(_merge_cell(run))
                run = []
            run.append(w)
            prev_x1 = w["x1"]
        if run:
            cells.append(_merge_cell(run))
    return cells


def _merge_cell(run: list[dict]) -> dict:
    return {
        "text": "".join(w["text"] for w in run),
        "x0": min(w["x0"] for w in run),
        "x1": max(w["x1"] for w in run),
        "top": min(w["top"] for w in run),
        "bottom": max(w["bottom"] for w in run),
    }


# --- no_text sub-kind classification (#5910) ---------------------------------

SUBKIND_REPRINTED_VECTOR = "reprinted_vector"
SUBKIND_RASTER_SCAN = "raster_scan"
SUBKIND_UNKNOWN = "unknown"

# Calibrated on the real files: a UC report re-printed through "Print To PDF"
# carries fonts=0 with hundreds-to-thousands of vector drawings per page and
# only tiny logo images; a true scan carries one page-filling raster with ~no
# drawings. Both populations sit far from these thresholds.
_SUBKIND_MIN_DRAWINGS = 50
_SUBKIND_FULL_PAGE_IMAGE_FRAC = 0.5


def classify_no_text_subkind(pdf_bytes: bytes) -> str:
    """Tell a re-printed vector PDF apart from a true raster scan.

    Both reach `no_text` (no text layer), but they want opposite advice: a UC
    report re-printed through "Print To PDF" has every glyph as a vector outline
    (``fonts == 0``, many drawings per page, only tiny logo images), and the
    right answer is "upload the original file" — it extracts cleanly with the
    existing guards. A photograph or scanner output is one page-filling raster
    with almost no drawings, and OCR (or re-export) is the only route.

    Returns ``reprinted_vector``, ``raster_scan``, or ``unknown`` when neither
    signature is clear. The client treats ``unknown`` (and an absent field) as
    "keep the current copy", so a misclassification is never worse than today
    (#5910).
    """
    import fitz

    total_fonts = 0
    total_drawings = 0
    has_full_page_image = False
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page in doc:
            total_fonts += len(page.get_fonts())
            total_drawings += len(page.get_drawings())
            page_area = abs(page.rect.width * page.rect.height) or 1.0
            for img in page.get_images(full=True):
                for rect in page.get_image_rects(img[0]):
                    if (
                        abs(rect.width * rect.height) / page_area
                        >= _SUBKIND_FULL_PAGE_IMAGE_FRAC
                    ):
                        has_full_page_image = True
                        break

    if has_full_page_image and total_drawings < _SUBKIND_MIN_DRAWINGS:
        return SUBKIND_RASTER_SCAN
    if (
        total_fonts == 0
        and total_drawings >= _SUBKIND_MIN_DRAWINGS
        and not has_full_page_image
    ):
        return SUBKIND_REPRINTED_VECTOR
    return SUBKIND_UNKNOWN
