"""Parser for UC Bostad's "Värdeutlåtande Bostadsrätt" Datavärdering PDF.

The PDF is laid out in three columns (x ≈ 42 / 304 / 462) of paired
label/value rows. pdfplumber's flat `extract_text()` concatenates
adjacent words without spaces, so we work from `extract_words()` and
re-cluster by (column-x, row-y) instead.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from io import BytesIO

import pdfplumber

from app.valuation_statement.classifier import DocumentType
from app.valuation_statement.extraction import ExtractedField, ExtractionResult


COL_TOL = 30  # pt — words within 30pt of a column anchor are part of that column
ROW_TOL = 3   # pt — vertical clustering tolerance for line grouping


def parse(pdf_bytes: bytes, filename: str) -> ExtractionResult:
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        p1_words = pdf.pages[0].extract_words()
        comparables = _extract_comparable_sales(pdf.pages[1]) if len(pdf.pages) > 1 else []
        footer_text = pdf.pages[0].extract_text() or ""

    cells = _build_cell_grid(p1_words)

    result = ExtractionResult(
        document_type=DocumentType.DATAVARDERING,
        filename=filename,
    )

    result.fields.extend(_extract_object_info(cells, filename))
    result.fields.extend(_extract_value(cells, filename))
    result.fields.extend(_extract_forening(cells, filename))
    result.fields.append(_extract_document_date(footer_text, filename))

    result.extras["comparable_sales"] = comparables
    return result


# ---------- column/row clustering ----------

def _build_cell_grid(words: list[dict]) -> dict[tuple[int, float], str]:
    """Group page-1 words into a {(column_index, row_y): joined_text} dict.

    column_index is 0/1/2 depending on which of the three column anchors
    (~42, ~304, ~462) the word's x0 is closest to.
    """
    anchors = [42, 304, 462]
    rows: dict[tuple[int, float], list[str]] = {}
    for w in words:
        col = min(range(len(anchors)), key=lambda i: abs(w["x0"] - anchors[i]))
        # Bucket the y coord onto a multiple of ROW_TOL so neighbours collapse.
        row_y = round(w["top"] / ROW_TOL) * ROW_TOL
        rows.setdefault((col, row_y), []).append(w["text"])
    return {key: " ".join(parts) for key, parts in rows.items()}


def _column_lines(cells: dict[tuple[int, float], str], col: int) -> list[tuple[float, str]]:
    return sorted(
        ((y, text) for (c, y), text in cells.items() if c == col),
        key=lambda r: r[0],
    )


def _value_after(lines: list[tuple[float, str]], label_pattern: re.Pattern) -> str | None:
    """Return the text on the line immediately below the line matching label_pattern."""
    for idx, (_, text) in enumerate(lines):
        if label_pattern.search(text):
            if idx + 1 < len(lines):
                return lines[idx + 1][1].strip() or None
    return None


# ---------- object-info column (col 0 + 1) ----------

_ADDRESS_RE = re.compile(r"^(?P<street>[A-ZÅÄÖ][A-Za-zÅÄÖåäö]+?(?:gata|vägen|gränden|stigen|allén|plan|torget))(?P<num>\d+(?:[A-Z])?)(?:LGH(?P<lgh>\d+))?$")
_POSTORT_RE = re.compile(r"^(\d{5})(.+)$")


def _extract_object_info(cells, filename: str) -> list[ExtractedField]:
    col0 = _column_lines(cells, 0)
    fields: list[ExtractedField] = []

    # Address row sits right under "Adress" header.
    raw_address = _value_after(col0, re.compile(r"^Adress$"))
    street, lgh_nr = _split_address(raw_address)
    fields.append(
        ExtractedField(
            key="address_street",
            value=street,
            confidence="confident" if street else "not_found",
            source_filename=filename,
            source_page=1,
        )
    )
    fields.append(
        ExtractedField(
            key="lgh_internal",
            value=lgh_nr,
            confidence="confident" if lgh_nr else "uncertain",
            source_filename=filename,
            source_page=1,
            note="Föreningens internal lgh# (e.g. 1303); not Skatteverkets-nr.",
        )
    )

    # Post number + locality come on the next col-0 line.
    postnr, postort = _split_postort(_value_below(col0, raw_address))
    fields.append(
        ExtractedField(
            key="postnummer",
            value=postnr,
            confidence="confident" if postnr else "not_found",
            source_filename=filename,
            source_page=1,
        )
    )
    fields.append(
        ExtractedField(
            key="postort",
            value=postort,
            confidence="confident" if postort else "not_found",
            source_filename=filename,
            source_page=1,
            note="From data-valuation footer; tag-case (Hägersten, not HÄGERSTEN).",
        )
    )

    # Kommun is in col 1 under "Kommun" header.
    col1 = _column_lines(cells, 1)
    kommun = _value_after(col1, re.compile(r"^Kommun$"))
    fields.append(
        ExtractedField(
            key="kommun_datavardering",
            value=kommun,
            confidence="confident" if kommun else "not_found",
            source_filename=filename,
            source_page=1,
            note="Datavärdering reports the wider-area kommun (Stockholm); the LGH-extract's postort (Hägersten) is usually preferred.",
        )
    )

    return fields


# Known Swedish street-suffix words — split as their own token even when
# fused onto the preceding word.
_STREET_SUFFIXES = (
    "gata",
    "vagen",
    "vägen",
    "gränden",
    "stigen",
    "allén",
    "plan",
    "torget",
    "backen",
    "kullen",
)


def _split_address(raw: str | None) -> tuple[str | None, str | None]:
    if not raw:
        return None, None
    # 'HannaRydhsgata12LGH1303' → split CamelCase street, numeric house number, optional LGH.
    m = re.match(
        r"^(?P<street_camel>(?:[A-ZÅÄÖ][a-zåäö]+)+)(?P<house>\d+[A-Z]?)(?:LGH(?P<lgh>\d+))?$",
        raw,
    )
    if not m:
        return raw, None
    street = _camel_split(m.group("street_camel"))
    return f"{street} {m.group('house')}", m.group("lgh")


def _camel_split(camel: str) -> str:
    # 'HannaRydhsgata' → 'Hanna Rydhs gata'
    parts = re.findall(r"[A-ZÅÄÖ][a-zåäö]*", camel)
    # Detach known street suffixes that got fused onto the preceding word
    # (e.g. 'Rydhsgata' is a single CamelCase token but is really 'Rydhs gata').
    detached: list[str] = []
    for part in parts:
        suffix = _trailing_street_suffix(part)
        if suffix and len(part) > len(suffix):
            detached.append(part[: -len(suffix)])
            detached.append(suffix)
        else:
            detached.append(part)
    return " ".join(detached)


def _trailing_street_suffix(token: str) -> str | None:
    lower = token.lower()
    for suffix in _STREET_SUFFIXES:
        if lower.endswith(suffix) and lower != suffix:
            return suffix
    return None


def _split_postort(raw: str | None) -> tuple[str | None, str | None]:
    if not raw:
        return None, None
    m = _POSTORT_RE.match(raw)
    if not m:
        return None, raw
    postnr = m.group(1)
    raw_locality = m.group(2)
    return postnr, raw_locality.title()


def _value_below(lines: list[tuple[float, str]], reference_text: str | None) -> str | None:
    if reference_text is None:
        return None
    for idx, (_, text) in enumerate(lines):
        if text == reference_text and idx + 1 < len(lines):
            return lines[idx + 1][1].strip() or None
    return None


# ---------- value column (col 1 + 2) ----------

def _extract_value(cells, filename: str) -> list[ExtractedField]:
    col1 = _column_lines(cells, 1)
    col2 = _column_lines(cells, 2)
    fields: list[ExtractedField] = []

    marknadsvarde = _value_after(col1, re.compile(r"^Marknadsv[äa]rde$"))
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

    osakerhet_upp = _value_after(col2, re.compile(r"Os[äa]kerhetupp[åa]t"))
    osakerhet_ned = _value_after(col2, re.compile(r"Os[äa]kerhetned[åa]t"))
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


def _format_sek(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return raw
    # 2350000 → '2 350 000'
    return re.sub(r"(?<=\d)(?=(\d{3})+$)", " ", digits)


# ---------- förening (col 0) ----------

def _extract_forening(cells, filename: str) -> list[ExtractedField]:
    col0 = _column_lines(cells, 0)
    fields: list[ExtractedField] = []

    forening_raw = _value_after(col0, re.compile(r"^F[öo]reningsinformation$"))
    forening = _expand_concat(forening_raw) if forening_raw else None
    fields.append(
        ExtractedField(
            key="forening_namn",
            value=forening,
            confidence="confident" if forening else "not_found",
            source_filename=filename,
            source_page=1,
        )
    )

    orgnr_raw = _value_after(col0, re.compile(r"^Organisationsnummer$"))
    fields.append(
        ExtractedField(
            key="organisationsnummer",
            value=_compact_orgnr(orgnr_raw),
            confidence="confident" if orgnr_raw else "not_found",
            source_filename=filename,
            source_page=1,
            note="Compact form (no dash) as used in the template.",
        )
    )

    return fields


def _expand_concat(raw: str) -> str:
    # 'HSBBrfLångpannaniStockholm' → 'HSB Brf Långpannan i Stockholm'.
    s = raw
    # 1. Boundary between consecutive uppercase + a following CamelCase word
    #    (handles the 'HSBBrf' acronym + word case: insert space before
    #    the final uppercase that starts a new TitleCase token).
    s = re.sub(r"(?<=[A-ZÅÄÖ])(?=[A-ZÅÄÖ][a-zåäö])", " ", s)
    # 2. Boundary between lowercase + uppercase (Långpannan + i / Stockholm).
    s = re.sub(r"(?<=[a-zåäö])(?=[A-ZÅÄÖ])", " ", s)
    # 3. Lift the 'i' connector out of a word when it's between two TitleCase
    #    tokens (Långpannani → Långpannan i).
    s = re.sub(r"(?<=[a-zåäö])(i) (?=[A-ZÅÄÖ])", r" \1 ", s)
    return re.sub(r"\s+", " ", s).strip()


def _compact_orgnr(raw: str | None) -> str | None:
    if not raw:
        return None
    return re.sub(r"\D", "", raw)


# ---------- comparable-sales table (page 2) ----------

def _extract_comparable_sales(page) -> list[dict]:
    """Return a best-effort row list for the comparable-sales table.

    Each row: {forening, address, boyta, rum, hiss, balkong, manadsavg, afs, pris, kvm, kopedatum}.
    We're after the rows; visual fidelity is not critical for v1.
    """
    rows: list[dict] = []
    text = page.extract_text() or ""
    for line in text.splitlines():
        # Row pattern: '...Långpannan...' followed by a date YYYY-MM.
        m = re.search(r"(\d[\d ]*\d{3,4})\s+(\d+)\s+(\d{4}-\d{2})$", line.strip())
        if m:
            rows.append({"raw": line.strip()})
    return rows


# ---------- footer date ----------

def _extract_document_date(footer_text: str, filename: str) -> ExtractedField:
    m = re.search(r"(\d{4}-\d{2}-\d{2})\d{2}:\d{2}\s*$", footer_text.strip(), re.MULTILINE)
    if not m:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", footer_text)
    return ExtractedField(
        key="document_date",
        value=m.group(1) if m else None,
        confidence="confident" if m else "not_found",
        source_filename=filename,
        source_page=1,
        note="Date the datavärdering was issued (appears in template's Beskrivning sentence).",
    )
