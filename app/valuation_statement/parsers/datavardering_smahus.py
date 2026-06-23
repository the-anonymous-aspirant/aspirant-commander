"""Parser for UC Bostad's "Värdeutlåtande Småhus" Datavärdering PDF.

Same UC Bostad family as `datavardering_br`, but the Småhus layout has
six sub-columns in the Objektsinformation band (the property-detail
table) and two columns in the Värde block, instead of BR's flat
three-column layout. We therefore work directly from word (x, y)
positions on page 1: each word is bucketed into a (column_anchor,
row_y) cell, and per-slot extraction walks "row immediately below the
label at the same column anchor".

Sample reference: `UCB_BENGTSFORS_NARSIDAN_1-21_*.pdf`.
"""

from __future__ import annotations

import re
from io import BytesIO

import pdfplumber

from app.valuation_statement.classifier import DocumentType
from app.valuation_statement.extraction import ExtractedField, ExtractionResult


# Column anchors observed in the UCB Småhus sample.
# col 0 carries Fastighetsbeteckning, Adress, Taxeringsvärde, the Värde
# header; col 4 carries Byggnadstyp + the Värde block's Marknadsvärde
# column; col 5 carries Osäkerhet uppåt / nedåt. Cols 1-3 only appear
# in the dense property-detail band (Totalyta / Boyta / Byggnadsår
# and Tomtyta / Biyta / Värdeår).
_COL_ANCHORS = (41.5, 163.4, 220.1, 276.8, 333.4, 492.2)
_COL_TOL = 12  # pt — words within this of an anchor belong to that column
_ROW_TOL = 3   # pt — y-bucket size, mirrors BR


def parse(pdf_bytes: bytes, filename: str) -> ExtractionResult:
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        p1_words = pdf.pages[0].extract_words()
        footer_text = pdf.pages[0].extract_text() or ""

    rows = _build_rows(p1_words)

    result = ExtractionResult(
        document_type=DocumentType.DATAVARDERING_SMAHUS,
        filename=filename,
    )
    result.fields.extend(_extract_objektsinformation(rows, filename))
    result.fields.extend(_extract_value(rows, filename))
    result.fields.append(_extract_document_date(footer_text, filename))
    return result


# ---------- row/cell grid ----------


def _build_rows(words: list[dict]) -> list[tuple[float, dict[int, str]]]:
    """Group words into rows keyed by y-bucket.

    Each row is `(y, {column_index: joined_text})` where column_index is
    the index into `_COL_ANCHORS` of the nearest column, provided the
    word's x0 falls within `_COL_TOL` of the anchor. Words outside any
    anchor (e.g. the `.` divider glyphs and the centred banner) are
    skipped — they don't carry a slot value.
    """
    buckets: dict[float, dict[int, list[str]]] = {}
    for w in words:
        col = _nearest_column(w["x0"])
        if col is None:
            continue
        y = round(w["top"] / _ROW_TOL) * _ROW_TOL
        buckets.setdefault(y, {}).setdefault(col, []).append(w["text"])
    rows: list[tuple[float, dict[int, str]]] = []
    for y in sorted(buckets):
        cells = {col: " ".join(parts) for col, parts in buckets[y].items()}
        rows.append((y, cells))
    return rows


def _nearest_column(x: float) -> int | None:
    candidates = [
        (i, abs(x - anchor)) for i, anchor in enumerate(_COL_ANCHORS)
    ]
    col, dist = min(candidates, key=lambda c: c[1])
    return col if dist <= _COL_TOL else None


def _value_below(
    rows: list[tuple[float, dict[int, str]]],
    label_pattern: re.Pattern,
    col: int,
) -> str | None:
    """Find the cell in row N+1 at `col`, where row N has a cell at `col`
    that matches `label_pattern`. Returns None if no such pair exists.
    """
    for i, (_, cells) in enumerate(rows):
        text = cells.get(col)
        if text and label_pattern.search(text):
            if i + 1 < len(rows):
                return rows[i + 1][1].get(col)
            return None
    return None


# ---------- Objektsinformation block ----------


def _extract_objektsinformation(
    rows: list[tuple[float, dict[int, str]]],
    filename: str,
) -> list[ExtractedField]:
    fields: list[ExtractedField] = []

    # Fastighetsbeteckning: col 0, value row immediately under label.
    fb_raw = _value_below(rows, re.compile(r"^Fastighetsbeteckning$"), col=0)
    fb_value = _split_concat(fb_raw)
    fields.append(
        ExtractedField(
            key="fastighetsbeteckning",
            value=fb_value,
            confidence="confident" if fb_value else "not_found",
            source_filename=filename,
            source_page=1,
        )
    )

    # Adress: col 0; in the Närsidan sample no value is rendered, but
    # other Småhus samples populate it on the row directly below.
    adress_raw = _value_below(rows, re.compile(r"^Adress$"), col=0)
    fields.append(
        ExtractedField(
            key="adress",
            value=_split_concat(adress_raw),
            confidence="confident" if adress_raw else "not_found",
            source_filename=filename,
            source_page=1,
        )
    )

    # Kommun is not present on this Småhus sample's page 1; emit
    # not_found so the operator types it during review. Future samples
    # that *do* render a Kommun label will pick it up via the same
    # column-walk.
    kommun_raw = _find_value_anywhere(rows, re.compile(r"^Kommun$"))
    fields.append(
        ExtractedField(
            key="kommun",
            value=kommun_raw,
            confidence="confident" if kommun_raw else "not_found",
            source_filename=filename,
            source_page=1,
        )
    )

    # Byggnadstyp lives in col 4 with its value on the next row.
    byggnadstyp = _value_below(rows, re.compile(r"^Byggnadstyp$"), col=4)
    fields.append(
        ExtractedField(
            key="byggnadstyp",
            value=byggnadstyp,
            confidence="confident" if byggnadstyp else "not_found",
            source_filename=filename,
            source_page=1,
        )
    )

    # Taxeringsvärde: col 0 in the six-column property band. Raw cell
    # carries the "(Tax<YY>)" tax-year qualifier; keep it in the value
    # so the operator sees the qualifier alongside the amount.
    tax_raw = _value_below(rows, re.compile(r"^Taxeringsvärde$"), col=0)
    fields.append(
        ExtractedField(
            key="taxeringsvarde",
            value=_format_tax_amount(tax_raw),
            confidence="confident" if tax_raw else "not_found",
            source_filename=filename,
            source_page=1,
            note="Includes the `(Tax<YY>)` tax-year qualifier UC prints next to the amount.",
        )
    )

    # Tomtyta (m²): col 1 in the six-column band, second sub-row.
    tomtyta = _value_below(rows, re.compile(r"^Tomtyta$"), col=1)
    fields.append(
        ExtractedField(
            key="tomtyta_m2",
            value=_format_sek(tomtyta),
            confidence="confident" if tomtyta else "not_found",
            source_filename=filename,
            source_page=1,
        )
    )

    # Byggnadsår: col 3, first six-column row.
    byggnadsar = _value_below(rows, re.compile(r"^Byggnadsår$"), col=3)
    fields.append(
        ExtractedField(
            key="byggnadsar",
            value=byggnadsar,
            confidence="confident" if byggnadsar else "not_found",
            source_filename=filename,
            source_page=1,
        )
    )

    # Värdeår: col 3, second six-column row.
    vardear = _value_below(rows, re.compile(r"^Värdeår$"), col=3)
    fields.append(
        ExtractedField(
            key="vardear",
            value=vardear,
            confidence="confident" if vardear else "not_found",
            source_filename=filename,
            source_page=1,
        )
    )

    return fields


def _find_value_anywhere(
    rows: list[tuple[float, dict[int, str]]],
    label_pattern: re.Pattern,
) -> str | None:
    """Search every column for a label match and return the cell below it
    in the same column. Used for slots like Kommun where the column
    isn't fixed across Småhus variants.
    """
    for col in range(len(_COL_ANCHORS)):
        value = _value_below(rows, label_pattern, col=col)
        if value is not None:
            return value
    return None


# ---------- Värde block ----------


def _extract_value(
    rows: list[tuple[float, dict[int, str]]],
    filename: str,
) -> list[ExtractedField]:
    fields: list[ExtractedField] = []

    # Marknadsvärde: col 4, label "Marknadsvärde" (NOT
    # "Marknadsvärde,kr/kvm" nor "Marknadsvärde/Taxeringsvärde", which
    # both share the same column-0/4 anchor in surrounding rows).
    marknadsvarde = _value_below(rows, re.compile(r"^Marknadsvärde$"), col=4)
    fields.append(
        ExtractedField(
            key="marknadsvarde_suggested",
            value=_format_sek(marknadsvarde),
            confidence="confident" if marknadsvarde else "not_found",
            source_filename=filename,
            source_page=1,
            note="Machine-suggested by UC Bostad. Appraiser typically overrides using the comparables table.",
        )
    )

    # Osäkerhet uppåt / nedåt: col 5. pdfplumber concatenates the words
    # ("Osäkerhetuppåt") so the regex tolerates both forms.
    osakerhet_upp = _value_below(rows, re.compile(r"^Osäkerhet\s*uppåt$"), col=5)
    osakerhet_ned = _value_below(rows, re.compile(r"^Osäkerhet\s*nedåt$"), col=5)
    fields.append(
        ExtractedField(
            key="osakerhet_uppat",
            value=_format_sek(osakerhet_upp),
            confidence="confident" if osakerhet_upp else "not_found",
            source_filename=filename,
            source_page=1,
        )
    )
    fields.append(
        ExtractedField(
            key="osakerhet_nedat",
            value=_format_sek(osakerhet_ned),
            confidence="confident" if osakerhet_ned else "not_found",
            source_filename=filename,
            source_page=1,
        )
    )

    return fields


# ---------- formatting helpers ----------


def _format_sek(raw: str | None) -> str | None:
    """Normalise UC's whitespace-stripped digit groups to space-separated
    thousands (e.g. '1075000' → '1 075 000').
    """
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return raw
    return re.sub(r"(?<=\d)(?=(\d{3})+$)", " ", digits)


_TAX_AMOUNT_RE = re.compile(r"^(\d+)(\(Tax\d+\))?$")


def _format_tax_amount(raw: str | None) -> str | None:
    """Format the Taxeringsvärde cell. '765000(Tax24)' → '765 000 (Tax24)'."""
    if not raw:
        return None
    m = _TAX_AMOUNT_RE.match(raw)
    if not m:
        return raw
    digits, qualifier = m.group(1), m.group(2)
    formatted = re.sub(r"(?<=\d)(?=(\d{3})+$)", " ", digits)
    return f"{formatted} {qualifier}" if qualifier else formatted


def _split_concat(raw: str | None) -> str | None:
    """Split UC's concatenated tokens back into spaced words.

    'BengtsforsNärsidan1:21' → 'Bengtsfors Närsidan 1:21'
    'Saknades2026-06-14' → 'Saknades 2026-06-14'
    Existing-space text is left intact.
    """
    if not raw:
        return None
    # Insert a space at every lower→Upper boundary.
    s = re.sub(r"(?<=[a-zåäö])(?=[A-ZÅÄÖ])", " ", raw)
    # Insert a space at every letter→digit boundary.
    s = re.sub(r"(?<=[A-Za-zÅÄÖåäö])(?=\d)", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ---------- footer date ----------


def _extract_document_date(footer_text: str, filename: str) -> ExtractedField:
    """The footer renders '2026-06-18 08:52' as '2026-06-1808:52' once
    pdfplumber collapses spans — match the date + immediate time stamp
    pattern, with a bare-date fallback.
    """
    m = re.search(r"(\d{4}-\d{2}-\d{2})\d{2}:\d{2}", footer_text)
    if not m:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", footer_text)
    return ExtractedField(
        key="document_date",
        value=m.group(1) if m else None,
        confidence="confident" if m else "not_found",
        source_filename=filename,
        source_page=1,
        note="Date the datavärdering was issued (used in the template's Beskrivning sentence).",
    )
