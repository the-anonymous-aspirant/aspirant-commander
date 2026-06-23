"""Per-slot extractors for the Bostadsrätt Värdeutlåtande family.

Two known layouts both classify to `DocumentType.DATAVARDERING_BR`
and feed through this parser branch (operator correction on epic
#1060: filename ≠ content type, one parser per content type
regardless of issuer):

1. **UC Bostad data-feed report** (sample `Datavardering.pdf`).
   Three-column page-1 grid of label / value pairs at column
   anchors ~42 / ~304 / ~462; values sit on the row immediately
   below their label.

2. **Fastighetsbyrån prose appraisal** (sample `VardeutlatandeBR.pdf`,
   was previously the orphaned `VARDEUTLATANDE_NORTHMILL_BR`).
   `Värderingsobjekt` bullets ("Objekt:", "Adress:", "Kommun:",
   "Upplåtelseform:") and a "Marknadsvärdet bedöms till X kr ± Y kr"
   sentence. No column grid; regex against flat text.

The slot list below is fixed by the docx template (`TemplateFields`).
Each slot lists its strategies in priority order — UC tabular first
when both could apply, prose as the fallback for the prose-only
sample. A slot whose chain misses lands as `not_found` so the
operator types it during review; never a broken default.

Additions go IN the chain, not as new slots: a future issuer
inserting "Marknadsvärde" at a different column-x simply gets a new
strategy appended below the existing UC strategy, keeping
backwards-compatibility for every sample already seen.
"""

from __future__ import annotations

import re

from app.valuation_statement.classifier import DocumentType
from app.valuation_statement.extraction import ExtractionResult
from app.valuation_statement.parsers._context import ParseContext, build_context
from app.valuation_statement.parsers._strategy import (
    SlotExtractor,
    Strategy,
    run_slots,
)


# ---------- parse entrypoint ----------


def parse(pdf_bytes: bytes, filename: str) -> ExtractionResult:
    ctx = build_context(pdf_bytes)
    result = ExtractionResult(
        document_type=DocumentType.DATAVARDERING_BR,
        filename=filename,
    )
    result.fields.extend(run_slots(_SLOTS, ctx, filename))
    if _is_uc_tabular(ctx) and ctx.page_count > 1:
        result.extras["comparable_sales"] = _extract_comparable_sales_p2(pdf_bytes)
    return result


def _is_uc_tabular(ctx: ParseContext) -> bool:
    """UC tabular has the "Värdeutlåtande Bostadsrätt" header on page 1.

    Used to gate page-2 comparable-sales extraction — the prose
    layout has no such table.
    """
    return bool(re.search(r"V[äa]rdeutl[åa]tande\s+Bostadsr[äa]tt", ctx.page1_text, re.IGNORECASE))


def _is_prose(ctx: ParseContext) -> bool:
    return "Värderingsobjekt" in ctx.page1_text


# ---------- UC tabular grid cache (cells keyed by column/row) ----------


_UC_COL_ANCHORS = (42, 304, 462)
_UC_ROW_TOL = 3


def _uc_cells(ctx: ParseContext) -> dict[tuple[int, float], str]:
    """Group page-1 words into a {(column_index, row_y): joined_text} dict.

    column_index is 0/1/2 depending on which UC anchor (~42, ~304,
    ~462) the word's x0 is closest to. row_y is bucketed onto a
    multiple of `_UC_ROW_TOL` so neighbours collapse.
    """
    rows: dict[tuple[int, float], list[str]] = {}
    for w in ctx.page1_words:
        col = min(
            range(len(_UC_COL_ANCHORS)),
            key=lambda i: abs(w["x0"] - _UC_COL_ANCHORS[i]),
        )
        row_y = round(w["top"] / _UC_ROW_TOL) * _UC_ROW_TOL
        rows.setdefault((col, row_y), []).append(w["text"])
    return {key: " ".join(parts) for key, parts in rows.items()}


def _uc_column_lines(cells: dict[tuple[int, float], str], col: int) -> list[tuple[float, str]]:
    return sorted(
        ((y, text) for (c, y), text in cells.items() if c == col),
        key=lambda r: r[0],
    )


def _uc_value_after(
    lines: list[tuple[float, str]], label_pattern: re.Pattern
) -> str | None:
    """Return the text on the line immediately below the label match."""
    for idx, (_, text) in enumerate(lines):
        if label_pattern.search(text):
            if idx + 1 < len(lines):
                return lines[idx + 1][1].strip() or None
    return None


def _uc_value_below_text(
    lines: list[tuple[float, str]], reference_text: str | None
) -> str | None:
    """Return the next line below `reference_text` in the same column."""
    if reference_text is None:
        return None
    for idx, (_, text) in enumerate(lines):
        if text == reference_text and idx + 1 < len(lines):
            return lines[idx + 1][1].strip() or None
    return None


# ---------- UC tabular strategies (one helper per slot) ----------


def _uc_address_raw(ctx: ParseContext) -> str | None:
    if not _is_uc_tabular(ctx):
        return None
    col0 = _uc_column_lines(_uc_cells(ctx), 0)
    return _uc_value_after(col0, re.compile(r"^Adress$"))


def _uc_address_street(ctx: ParseContext) -> str | None:
    street, _ = _split_uc_address(_uc_address_raw(ctx))
    return street


def _uc_lgh_internal(ctx: ParseContext) -> str | None:
    _, lgh = _split_uc_address(_uc_address_raw(ctx))
    return lgh


def _uc_postnummer(ctx: ParseContext) -> str | None:
    if not _is_uc_tabular(ctx):
        return None
    col0 = _uc_column_lines(_uc_cells(ctx), 0)
    raw = _uc_address_raw(ctx)
    postnr, _ = _split_postort(_uc_value_below_text(col0, raw))
    return postnr


def _uc_postort(ctx: ParseContext) -> str | None:
    if not _is_uc_tabular(ctx):
        return None
    col0 = _uc_column_lines(_uc_cells(ctx), 0)
    raw = _uc_address_raw(ctx)
    _, postort = _split_postort(_uc_value_below_text(col0, raw))
    return postort


def _uc_kommun(ctx: ParseContext) -> str | None:
    if not _is_uc_tabular(ctx):
        return None
    col1 = _uc_column_lines(_uc_cells(ctx), 1)
    return _uc_value_after(col1, re.compile(r"^Kommun$"))


def _uc_marknadsvarde(ctx: ParseContext) -> str | None:
    if not _is_uc_tabular(ctx):
        return None
    col1 = _uc_column_lines(_uc_cells(ctx), 1)
    raw = _uc_value_after(col1, re.compile(r"^Marknadsv[äa]rde$"))
    return _format_sek(raw)


def _uc_osakerhet_upp(ctx: ParseContext) -> str | None:
    if not _is_uc_tabular(ctx):
        return None
    col2 = _uc_column_lines(_uc_cells(ctx), 2)
    raw = _uc_value_after(col2, re.compile(r"Os[äa]kerhetupp[åa]t"))
    return _format_sek(raw)


def _uc_osakerhet_ned(ctx: ParseContext) -> str | None:
    if not _is_uc_tabular(ctx):
        return None
    col2 = _uc_column_lines(_uc_cells(ctx), 2)
    raw = _uc_value_after(col2, re.compile(r"Os[äa]kerhetned[åa]t"))
    return _format_sek(raw)


def _uc_forening_namn(ctx: ParseContext) -> str | None:
    if not _is_uc_tabular(ctx):
        return None
    col0 = _uc_column_lines(_uc_cells(ctx), 0)
    raw = _uc_value_after(col0, re.compile(r"^F[öo]reningsinformation$"))
    return _expand_concat(raw) if raw else None


def _uc_organisationsnummer(ctx: ParseContext) -> str | None:
    if not _is_uc_tabular(ctx):
        return None
    col0 = _uc_column_lines(_uc_cells(ctx), 0)
    raw = _uc_value_after(col0, re.compile(r"^Organisationsnummer$"))
    return _compact_digits(raw)


def _uc_document_date(ctx: ParseContext) -> str | None:
    if not _is_uc_tabular(ctx):
        return None
    footer = ctx.page1_text
    m = re.search(r"(\d{4}-\d{2}-\d{2})\d{2}:\d{2}\s*$", footer.strip(), re.MULTILINE)
    if not m:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", footer)
    return m.group(1) if m else None


# UC tabular's banner reads `Värdeutlåtande` / `Bostadsrätt` on
# consecutive lines (line 0 / line 1 of page-1 text). Every BR
# Datavärdering from UC Bostad carries this — the doc isn't a UC BR
# without it — so banner→`Bostadsrätt` is categorical, not a sample-
# specific match.
_UC_BANNER_BR = re.compile(
    r"V[äa]rdeutl[åa]tande\s*\n\s*Bostadsr[äa]tt",
    re.IGNORECASE,
)


def _uc_upplatelseform(ctx: ParseContext) -> str | None:
    if not _is_uc_tabular(ctx):
        return None
    if _UC_BANNER_BR.search(ctx.page1_text):
        return "Bostadsrätt"
    return None


# UC-tabular address split: 'HannaRydhsgata12LGH1303' → ('Hanna Rydhs gata 12', '1303')
_STREET_SUFFIXES = (
    "gata", "vagen", "vägen", "gränden", "stigen", "allén",
    "plan", "torget", "backen", "kullen",
)
_POSTORT_RE = re.compile(r"^(\d{5})(.+)$")


def _split_uc_address(raw: str | None) -> tuple[str | None, str | None]:
    if not raw:
        return None, None
    m = re.match(
        r"^(?P<street_camel>(?:[A-ZÅÄÖ][a-zåäö]+)+)(?P<house>\d+[A-Z]?)(?:LGH(?P<lgh>\d+))?$",
        raw,
    )
    if not m:
        return raw, None
    street = _camel_split(m.group("street_camel"))
    return f"{street} {m.group('house')}", m.group("lgh")


def _camel_split(camel: str) -> str:
    parts = re.findall(r"[A-ZÅÄÖ][a-zåäö]*", camel)
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
    return m.group(1), m.group(2).title()


def _expand_concat(raw: str) -> str:
    s = raw
    s = re.sub(r"(?<=[A-ZÅÄÖ])(?=[A-ZÅÄÖ][a-zåäö])", " ", s)
    s = re.sub(r"(?<=[a-zåäö])(?=[A-ZÅÄÖ])", " ", s)
    s = re.sub(r"(?<=[a-zåäö])(i) (?=[A-ZÅÄÖ])", r" \1 ", s)
    return re.sub(r"\s+", " ", s).strip()


def _compact_digits(raw: str | None) -> str | None:
    if not raw:
        return None
    return re.sub(r"\D", "", raw)


def _format_sek(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return raw
    return re.sub(r"(?<=\d)(?=(\d{3})+$)", " ", digits)


# ---------- Fastighetsbyrån prose strategies ----------


def _bullet_value(text: str, label: str) -> str | None:
    """Match a `● Label: value` bullet (or unbulleted `Label: value`)."""
    pat = re.compile(
        rf"(?:^|\n)\s*(?:●\s*)?{re.escape(label)}\s*:\s*(?P<value>[^\n]+)"
    )
    m = pat.search(text)
    if not m:
        return None
    return m.group("value").strip() or None


# "Marknadsvärdet bedöms till 3 050 000 kr, med ett intervall om ± 50 000 kr"
_PROSE_VALUE_RE = re.compile(
    r"Marknadsv[äa]rdet\s+bed[öo]ms\s+till\s+(?P<amount>[\d\s]+?)\s*kr"
    r"(?:[^.]*?intervall\s+om\s*(?:\+/-|±)?\s*(?P<interval>[\d\s]+?)\s*kr)?",
    re.IGNORECASE,
)

# "18/6/2026" Swedish dd/m/yyyy stamp the appraiser writes above "Utfärdat av"
_PROSE_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")

# Decompose a prose `Objekt:` line for Bostadsrätt:
# "LGH 1303 HSB Brf Långpannan i Stockholm (7696097448)" →
#   lgh_internal='1303', forening_namn='HSB Brf Långpannan i Stockholm',
#   organisationsnummer='7696097448'.
_OBJEKT_BR_RE = re.compile(
    r"^\s*LGH\s+(?P<lgh>\d+)\s+(?P<forening>.+?)\s*\((?P<orgnr>\d[\d\s\-]*)\)\s*$"
)


def _prose_objekt_match(ctx: ParseContext) -> re.Match | None:
    if not _is_prose(ctx):
        return None
    objekt = _bullet_value(ctx.page1_text, "Objekt")
    if not objekt:
        return None
    return _OBJEKT_BR_RE.match(objekt)


def _prose_forening_namn(ctx: ParseContext) -> str | None:
    m = _prose_objekt_match(ctx)
    return m.group("forening").strip() if m else None


def _prose_organisationsnummer(ctx: ParseContext) -> str | None:
    m = _prose_objekt_match(ctx)
    return _compact_digits(m.group("orgnr")) if m else None


def _prose_lgh_internal(ctx: ParseContext) -> str | None:
    m = _prose_objekt_match(ctx)
    return m.group("lgh") if m else None


def _prose_address_street(ctx: ParseContext) -> str | None:
    if not _is_prose(ctx):
        return None
    return _bullet_value(ctx.page1_text, "Adress")


def _prose_postort(ctx: ParseContext) -> str | None:
    """Prose bullet `Kommun:` carries the locality (e.g. "Hägersten").

    For the docx template's `kommun` slot. The prose layout doesn't
    distinguish kommun-vs-postort the way the UC tabular does — we
    surface the single Kommun bullet under `postort` so the frontend
    review hydrates the same slot.
    """
    if not _is_prose(ctx):
        return None
    return _bullet_value(ctx.page1_text, "Kommun")


def _prose_kommun(ctx: ParseContext) -> str | None:
    if not _is_prose(ctx):
        return None
    return _bullet_value(ctx.page1_text, "Kommun")


def _prose_upplatelseform(ctx: ParseContext) -> str | None:
    if not _is_prose(ctx):
        return None
    return _bullet_value(ctx.page1_text, "Upplåtelseform")


def _prose_marknadsvarde(ctx: ParseContext) -> str | None:
    if not _is_prose(ctx):
        return None
    m = _PROSE_VALUE_RE.search(ctx.page1_text)
    if not m:
        return None
    return _format_sek(m.group("amount"))


def _prose_osakerhet(ctx: ParseContext) -> str | None:
    """Prose templates report a single symmetric interval — uppåt = nedåt = X."""
    if not _is_prose(ctx):
        return None
    m = _PROSE_VALUE_RE.search(ctx.page1_text)
    if not m or not m.group("interval"):
        return None
    return _format_sek(m.group("interval"))


def _prose_document_date(ctx: ParseContext) -> str | None:
    if not _is_prose(ctx):
        return None
    m = _PROSE_DATE_RE.search(ctx.page1_text)
    if not m:
        return None
    day, month, year = m.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


# ---------- slot inventory ----------


_SLOTS: tuple[SlotExtractor, ...] = (
    SlotExtractor(
        slot_key="forening_namn",
        strategies=(
            Strategy("uc_tabular_foreningsinformation", _uc_forening_namn),
            Strategy("fb_prose_objekt_line", _prose_forening_namn),
        ),
    ),
    SlotExtractor(
        slot_key="organisationsnummer",
        strategies=(
            Strategy("uc_tabular_organisationsnummer", _uc_organisationsnummer),
            Strategy("fb_prose_objekt_parens", _prose_organisationsnummer),
        ),
        note="Compact form (no dash) as used in the template.",
    ),
    SlotExtractor(
        slot_key="lgh_internal",
        strategies=(
            Strategy("uc_tabular_address_suffix", _uc_lgh_internal),
            Strategy("fb_prose_objekt_prefix", _prose_lgh_internal),
        ),
        note="Föreningens internal lgh# (e.g. 1303); not Skatteverkets-nr.",
    ),
    SlotExtractor(
        slot_key="address_street",
        strategies=(
            Strategy("uc_tabular_adress_label", _uc_address_street),
            Strategy("fb_prose_adress_bullet", _prose_address_street),
        ),
    ),
    SlotExtractor(
        slot_key="postnummer",
        strategies=(
            Strategy("uc_tabular_below_address", _uc_postnummer),
        ),
    ),
    SlotExtractor(
        slot_key="postort",
        strategies=(
            Strategy("uc_tabular_below_address_locality", _uc_postort),
            Strategy("fb_prose_kommun_bullet", _prose_postort),
        ),
        note="From data-valuation footer; tag-case (Hägersten, not HÄGERSTEN).",
    ),
    SlotExtractor(
        slot_key="kommun_datavardering",
        strategies=(
            Strategy("uc_tabular_kommun_label", _uc_kommun),
            Strategy("fb_prose_kommun_bullet", _prose_kommun),
        ),
        note="Datavärdering reports the wider-area kommun (Stockholm); LGH-extract's postort (Hägersten) usually preferred.",
    ),
    SlotExtractor(
        slot_key="upplatelseform",
        strategies=(
            Strategy("uc_tabular_banner_bostadsratt", _uc_upplatelseform),
            Strategy("fb_prose_upplatelseform_bullet", _prose_upplatelseform),
        ),
        note="UC banner ('Värdeutlåtande / Bostadsrätt') always implies Bostadsrätt; prose bullet otherwise.",
    ),
    SlotExtractor(
        slot_key="marknadsvarde_suggested",
        strategies=(
            Strategy("uc_tabular_marknadsvarde_label", _uc_marknadsvarde),
            Strategy("fb_prose_bedoms_till", _prose_marknadsvarde),
        ),
        note="Machine-suggested by the issuer. Appraiser typically overrides using the comparables table.",
    ),
    SlotExtractor(
        slot_key="osakerhet_uppat",
        strategies=(
            Strategy("uc_tabular_osakerhet_upp_label", _uc_osakerhet_upp),
            Strategy("fb_prose_symmetric_interval", _prose_osakerhet),
        ),
    ),
    SlotExtractor(
        slot_key="osakerhet_nedat",
        strategies=(
            Strategy("uc_tabular_osakerhet_ned_label", _uc_osakerhet_ned),
            Strategy("fb_prose_symmetric_interval", _prose_osakerhet),
        ),
    ),
    SlotExtractor(
        slot_key="document_date",
        strategies=(
            Strategy("uc_tabular_footer", _uc_document_date),
            Strategy("fb_prose_dd_m_yyyy_stamp", _prose_document_date),
        ),
        note="Date the datavärdering was issued (appears in template's Beskrivning sentence).",
    ),
)


# ---------- comparable-sales table (page 2, UC-only) ----------


_COMPARABLE_DATE_RE = re.compile(r"\s+(\d{4}-\d{2})$")
_BALKONG_TOKENS = {"ja", "nej"}


def _extract_comparable_sales_p2(pdf_bytes: bytes) -> list[dict]:
    """Best-effort row list for the UC Bostad page-2 comparables table.

    UC's PDF doesn't draw column borders, so pdfplumber's
    `extract_table()` can't infer them — we parse each text line by
    anchoring on the trailing YYYY-MM and walking right-to-left.
    Each row carries the structured columns plus the original `raw`
    line so the frontend has a fallback when a row fails to parse.
    """
    from io import BytesIO

    import pdfplumber

    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        if len(pdf.pages) < 2:
            return []
        text = pdf.pages[1].extract_text() or ""
    rows: list[dict] = []
    for line in text.splitlines():
        parsed = _parse_comparable_row(line)
        if parsed is not None:
            rows.append(parsed)
    return rows


def _parse_comparable_row(line: str) -> dict | None:
    stripped = line.strip()
    date_match = _COMPARABLE_DATE_RE.search(stripped)
    if not date_match:
        return None
    salj_datum = date_match.group(1)
    head = stripped[: date_match.start()].strip()
    parts = head.split()
    if len(parts) < 6:
        return {"raw": stripped, "salj_datum": salj_datum}

    pris_per_m2 = parts[-1]
    pris_kr = parts[-2]
    arsavgift_kr = parts[-3]
    avgift_kr_manad = parts[-4]
    rest = parts[:-4]

    balkong: str | None = None
    if rest and rest[-1].lower() in _BALKONG_TOKENS:
        balkong = rest[-1]
        rest = rest[:-1]
    if not rest:
        return {"raw": stripped, "salj_datum": salj_datum}

    area_m2 = rest[-1]
    forening_tokens = rest[:-1]
    forening: str | None = None
    if forening_tokens:
        forening = _expand_concat(" ".join(forening_tokens))

    return {
        "forening": forening,
        "area_m2": area_m2,
        "balkong": balkong,
        "avgift_kr_manad": avgift_kr_manad,
        "arsavgift_kr": arsavgift_kr,
        "pris_kr": pris_kr,
        "pris_per_m2": pris_per_m2,
        "salj_datum": salj_datum,
        "raw": stripped,
    }
