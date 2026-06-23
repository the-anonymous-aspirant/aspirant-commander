"""Per-slot extractors for the Småhus Värdeutlåtande family.

Two known layouts both classify to `DocumentType.DATAVARDERING_SMAHUS`
and feed through this parser branch:

1. **UC Bostad data-feed report** (sample
   `Datavardering_smahus.pdf`). Six sub-column page-1 grid with
   anchors at ~42 / ~163 / ~220 / ~277 / ~333 / ~492; values sit on
   the row immediately below their label.

2. **Fastighetsbyrån prose appraisal** (sample
   `VardeutlatandeHok.pdf`). `Värderingsobjekt` bullets ("Objekt:",
   "Adress:", "Kommun:", "Upplåtelseform:") and a "Marknadsvärdet
   bedöms till X kr ± Y kr" sentence. Regex against flat text.

The slot list below is fixed by the docx template's Småhus column;
each slot lists its strategies in priority order. UC tabular wins
when both could apply; prose is the fallback. Slots whose chain
misses land as `not_found` so the operator types them during review
— never a broken default.
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
        document_type=DocumentType.DATAVARDERING_SMAHUS,
        filename=filename,
    )
    result.fields.extend(run_slots(_SLOTS, ctx, filename))
    return result


def _is_uc_tabular(ctx: ParseContext) -> bool:
    """UC tabular has the "Värdeutlåtande Småhus" header on page 1."""
    return bool(
        re.search(r"V[äa]rdeutl[åa]tande\s+Sm[åa]hus", ctx.page1_text, re.IGNORECASE)
    )


def _is_prose(ctx: ParseContext) -> bool:
    return "Värderingsobjekt" in ctx.page1_text


# ---------- UC tabular six-column grid ----------


_UC_COL_ANCHORS = (41.5, 163.4, 220.1, 276.8, 333.4, 492.2)
_UC_COL_TOL = 12  # pt — words within this of an anchor belong to that column
_UC_ROW_TOL = 3   # pt — y-bucket size


def _uc_rows(ctx: ParseContext) -> list[tuple[float, dict[int, str]]]:
    """Group page-1 words into rows keyed by y-bucket.

    Each row is `(y, {column_index: joined_text})` where column_index
    is the index into `_UC_COL_ANCHORS` of the nearest column,
    provided x0 falls within `_UC_COL_TOL` of the anchor. Words
    outside any anchor (centred banner, `.` divider glyphs) are
    skipped — they don't carry a slot value.
    """
    buckets: dict[float, dict[int, list[str]]] = {}
    for w in ctx.page1_words:
        col = _nearest_column(w["x0"])
        if col is None:
            continue
        y = round(w["top"] / _UC_ROW_TOL) * _UC_ROW_TOL
        buckets.setdefault(y, {}).setdefault(col, []).append(w["text"])
    rows: list[tuple[float, dict[int, str]]] = []
    for y in sorted(buckets):
        cells = {col: " ".join(parts) for col, parts in buckets[y].items()}
        rows.append((y, cells))
    return rows


def _nearest_column(x: float) -> int | None:
    candidates = [(i, abs(x - anchor)) for i, anchor in enumerate(_UC_COL_ANCHORS)]
    col, dist = min(candidates, key=lambda c: c[1])
    return col if dist <= _UC_COL_TOL else None


def _value_below(
    rows: list[tuple[float, dict[int, str]]],
    label_pattern: re.Pattern,
    col: int,
) -> str | None:
    """Return the cell at `col` in row N+1, where row N has a cell at
    `col` matching `label_pattern`.
    """
    for i, (_, cells) in enumerate(rows):
        text = cells.get(col)
        if text and label_pattern.search(text):
            if i + 1 < len(rows):
                return rows[i + 1][1].get(col)
            return None
    return None


def _value_below_any_column(
    rows: list[tuple[float, dict[int, str]]],
    label_pattern: re.Pattern,
) -> str | None:
    """Search every column for the label and return the cell below it.

    Used for slots where the column anchor varies between Småhus
    samples (e.g. Kommun appears in different sub-columns across
    UC issuers).
    """
    for col in range(len(_UC_COL_ANCHORS)):
        value = _value_below(rows, label_pattern, col=col)
        if value is not None:
            return value
    return None


# ---------- UC tabular strategies ----------


def _uc_fastighetsbeteckning(ctx: ParseContext) -> str | None:
    if not _is_uc_tabular(ctx):
        return None
    raw = _value_below(_uc_rows(ctx), re.compile(r"^Fastighetsbeteckning$"), col=0)
    return _split_concat(raw)


def _uc_adress(ctx: ParseContext) -> str | None:
    if not _is_uc_tabular(ctx):
        return None
    raw = _value_below(_uc_rows(ctx), re.compile(r"^Adress$"), col=0)
    return _split_concat(raw)


def _uc_kommun(ctx: ParseContext) -> str | None:
    if not _is_uc_tabular(ctx):
        return None
    return _value_below_any_column(_uc_rows(ctx), re.compile(r"^Kommun$"))


def _uc_byggnadstyp(ctx: ParseContext) -> str | None:
    if not _is_uc_tabular(ctx):
        return None
    return _value_below(_uc_rows(ctx), re.compile(r"^Byggnadstyp$"), col=4)


def _uc_taxeringsvarde(ctx: ParseContext) -> str | None:
    if not _is_uc_tabular(ctx):
        return None
    raw = _value_below(_uc_rows(ctx), re.compile(r"^Taxeringsvärde$"), col=0)
    return _format_tax_amount(raw)


def _uc_tomtyta(ctx: ParseContext) -> str | None:
    if not _is_uc_tabular(ctx):
        return None
    raw = _value_below(_uc_rows(ctx), re.compile(r"^Tomtyta$"), col=1)
    return _format_sek(raw)


def _uc_byggnadsar(ctx: ParseContext) -> str | None:
    if not _is_uc_tabular(ctx):
        return None
    return _value_below(_uc_rows(ctx), re.compile(r"^Byggnadsår$"), col=3)


def _uc_vardear(ctx: ParseContext) -> str | None:
    if not _is_uc_tabular(ctx):
        return None
    return _value_below(_uc_rows(ctx), re.compile(r"^Värdeår$"), col=3)


def _uc_marknadsvarde(ctx: ParseContext) -> str | None:
    if not _is_uc_tabular(ctx):
        return None
    raw = _value_below(_uc_rows(ctx), re.compile(r"^Marknadsvärde$"), col=4)
    return _format_sek(raw)


def _uc_osakerhet_upp(ctx: ParseContext) -> str | None:
    if not _is_uc_tabular(ctx):
        return None
    raw = _value_below(_uc_rows(ctx), re.compile(r"^Osäkerhet\s*uppåt$"), col=5)
    return _format_sek(raw)


def _uc_osakerhet_ned(ctx: ParseContext) -> str | None:
    if not _is_uc_tabular(ctx):
        return None
    raw = _value_below(_uc_rows(ctx), re.compile(r"^Osäkerhet\s*nedåt$"), col=5)
    return _format_sek(raw)


def _uc_document_date(ctx: ParseContext) -> str | None:
    if not _is_uc_tabular(ctx):
        return None
    footer = ctx.page1_text
    m = re.search(r"(\d{4}-\d{2}-\d{2})\d{2}:\d{2}", footer)
    if not m:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", footer)
    return m.group(1) if m else None


# ---------- formatting helpers ----------


def _format_sek(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return raw
    return re.sub(r"(?<=\d)(?=(\d{3})+$)", " ", digits)


_TAX_AMOUNT_RE = re.compile(r"^(\d+)(\(Tax\d+\))?$")


def _format_tax_amount(raw: str | None) -> str | None:
    """'765000(Tax24)' → '765 000 (Tax24)'."""
    if not raw:
        return None
    m = _TAX_AMOUNT_RE.match(raw)
    if not m:
        return raw
    digits, qualifier = m.group(1), m.group(2)
    formatted = re.sub(r"(?<=\d)(?=(\d{3})+$)", " ", digits)
    return f"{formatted} {qualifier}" if qualifier else formatted


def _split_concat(raw: str | None) -> str | None:
    """'BengtsforsNärsidan1:21' → 'Bengtsfors Närsidan 1:21'.

    'Saknades2026-06-14' → 'Saknades 2026-06-14'. Spaces already in
    the text are left intact.
    """
    if not raw:
        return None
    s = re.sub(r"(?<=[a-zåäö])(?=[A-ZÅÄÖ])", " ", raw)
    s = re.sub(r"(?<=[A-Za-zÅÄÖåäö])(?=\d)", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ---------- Fastighetsbyrån prose strategies ----------


def _bullet_value(text: str, label: str) -> str | None:
    pat = re.compile(
        rf"(?:^|\n)\s*(?:●\s*)?{re.escape(label)}\s*:\s*(?P<value>[^\n]+)"
    )
    m = pat.search(text)
    if not m:
        return None
    return m.group("value").strip() or None


_PROSE_VALUE_RE = re.compile(
    r"Marknadsv[äa]rdet\s+bed[öo]ms\s+till\s+(?P<amount>[\d\s]+?)\s*kr"
    r"(?:[^.]*?intervall\s+om\s*(?:\+/-|±)?\s*(?P<interval>[\d\s]+?)\s*kr)?",
    re.IGNORECASE,
)

_PROSE_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")


def _prose_fastighetsbeteckning(ctx: ParseContext) -> str | None:
    if not _is_prose(ctx):
        return None
    return _bullet_value(ctx.page1_text, "Objekt")


def _prose_adress(ctx: ParseContext) -> str | None:
    if not _is_prose(ctx):
        return None
    return _bullet_value(ctx.page1_text, "Adress")


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
        slot_key="fastighetsbeteckning",
        strategies=(
            Strategy("uc_tabular_col0_label_below", _uc_fastighetsbeteckning),
            Strategy("fb_prose_objekt_bullet", _prose_fastighetsbeteckning),
        ),
    ),
    SlotExtractor(
        slot_key="adress",
        strategies=(
            Strategy("uc_tabular_col0_adress_label", _uc_adress),
            Strategy("fb_prose_adress_bullet", _prose_adress),
        ),
    ),
    SlotExtractor(
        slot_key="kommun",
        strategies=(
            Strategy("uc_tabular_kommun_any_column", _uc_kommun),
            Strategy("fb_prose_kommun_bullet", _prose_kommun),
        ),
    ),
    SlotExtractor(
        slot_key="byggnadstyp",
        strategies=(
            Strategy("uc_tabular_col4_label_below", _uc_byggnadstyp),
        ),
    ),
    SlotExtractor(
        slot_key="taxeringsvarde",
        strategies=(
            Strategy("uc_tabular_col0_label_below", _uc_taxeringsvarde),
        ),
        note="Includes the `(Tax<YY>)` tax-year qualifier UC prints next to the amount.",
    ),
    SlotExtractor(
        slot_key="tomtyta_m2",
        strategies=(
            Strategy("uc_tabular_col1_label_below", _uc_tomtyta),
        ),
    ),
    SlotExtractor(
        slot_key="byggnadsar",
        strategies=(
            Strategy("uc_tabular_col3_label_below", _uc_byggnadsar),
        ),
    ),
    SlotExtractor(
        slot_key="vardear",
        strategies=(
            Strategy("uc_tabular_col3_label_below", _uc_vardear),
        ),
    ),
    SlotExtractor(
        slot_key="upplatelseform",
        strategies=(
            Strategy("fb_prose_upplatelseform_bullet", _prose_upplatelseform),
        ),
        note="Drives the template's bostadsrätt/friköpt mode toggle.",
    ),
    SlotExtractor(
        slot_key="marknadsvarde_suggested",
        strategies=(
            Strategy("uc_tabular_col4_marknadsvarde_label", _uc_marknadsvarde),
            Strategy("fb_prose_bedoms_till", _prose_marknadsvarde),
        ),
        note="Machine-suggested by the issuer. Appraiser typically overrides using the comparables table.",
    ),
    SlotExtractor(
        slot_key="osakerhet_uppat",
        strategies=(
            Strategy("uc_tabular_col5_osakerhet_upp_label", _uc_osakerhet_upp),
            Strategy("fb_prose_symmetric_interval", _prose_osakerhet),
        ),
        note="Prose layout reports a single ±X kr interval; uppåt = nedåt = X.",
    ),
    SlotExtractor(
        slot_key="osakerhet_nedat",
        strategies=(
            Strategy("uc_tabular_col5_osakerhet_ned_label", _uc_osakerhet_ned),
            Strategy("fb_prose_symmetric_interval", _prose_osakerhet),
        ),
        note="Prose layout reports a single ±X kr interval; uppåt = nedåt = X.",
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
