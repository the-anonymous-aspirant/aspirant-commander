"""Field-first extractor: one strategy chain per docx slot, no DocumentType dispatch.

Operator directive #1113 (2026-06-24): the previous shape — classify into one
of `DATAVARDERING_BR / DATAVARDERING_SMAHUS / FASTIGHETSUTDRAG / LGH_UTDRAG`
then dispatch to a per-type parser — over-modelled the problem. The final
docx template fills a fixed list of fields and each field has one or more
strategies that can fill it; whichever strategy fires first on a given PDF
wins.

The two semantic primitives left are `property_shape ∈ {bostadsratt,
fastighet}` and `source_class ∈ {datavardering, lagenhetsforteckning,
fastighetsutdrag}` — both emitted AS slots from their own chains, never as
gates that pre-route extraction. Every per-field strategy carries its own
content-fingerprint guard so adding a new layout means appending one
strategy per affected slot, not branching a new parser.

The strategy chains below cover all 10 sample PDFs in the operator's test
corpus (UC Bostad tabular BR + Småhus, Fastighetsbyrån prose BR + Småhus,
HSB lägenhetsförteckning, Lantmäteriet Fastighetsrapport Plus R). Slots
whose chain misses land as `not_found` so the operator types them during
review — never a broken default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO
from typing import Callable

from app.valuation_statement._context import ParseContext, build_context
from app.valuation_statement.extraction import ExtractedField, ExtractionResult


# ---------- strategy / slot dataclasses ----------


@dataclass(frozen=True)
class Strategy:
    """One way to fill a slot from a parsed PDF.

    `extract(ctx)` returns the slot value, or None if the strategy's
    content-fingerprint guard rejects the PDF or the underlying lookup
    misses. `note` is the compact human-readable description surfaced
    in the Om verktyget transparency JSON (per #1113); it MUST match
    the chain order the slot below.
    """

    name: str
    note: str
    extract: Callable[["ParseContext"], str | None]


@dataclass(frozen=True)
class Slot:
    """One docx-template slot with its priority-ordered strategies."""

    key: str
    description: str
    strategies: tuple[Strategy, ...]

    def run(self, ctx: "ParseContext", filename: str) -> ExtractedField:
        for strategy in self.strategies:
            value = strategy.extract(ctx)
            if value is not None:
                return ExtractedField(
                    key=self.key,
                    value=value,
                    confidence="confident",
                    source_filename=filename,
                    source_page=1,
                    note=f"strategy: {strategy.name}",
                )
        return ExtractedField(
            key=self.key,
            value=None,
            confidence="not_found",
            source_filename=filename,
            source_page=None,
            note=None,
        )


# ---------- content-fingerprint guards ----------


def _is_datavardering_prose(ctx: ParseContext) -> bool:
    """Fastighetsbyrån prose appraisal: VÄRDEUTLÅTANDE banner + Värderingsobjekt."""
    return "VÄRDEUTLÅTANDE" in ctx.page1_text and "Värderingsobjekt" in ctx.page1_text


def _is_datavardering_uc_br(ctx: ParseContext) -> bool:
    """UC Bostad data-feed report for a Bostadsrätt.

    Banner reads `Värdeutlåtande / Bostadsrätt` on consecutive lines.
    """
    return bool(
        re.search(r"V[äa]rdeutl[åa]tande\s*\n\s*Bostadsr[äa]tt", ctx.page1_text)
    )


def _is_datavardering_uc_smahus(ctx: ParseContext) -> bool:
    """UC Bostad data-feed report for a Småhus (Friköpt single-family house)."""
    return bool(
        re.search(r"V[äa]rdeutl[åa]tande\s*\n\s*Sm[åa]hus", ctx.page1_text)
    )


def _is_datavardering_uc(ctx: ParseContext) -> bool:
    return _is_datavardering_uc_br(ctx) or _is_datavardering_uc_smahus(ctx)


def _is_lgh_utdrag(ctx: ParseContext) -> bool:
    """HSB lägenhetsförteckning extract.

    HSB's CMap surfaces under PyMuPDF as ligature-damaged text
    (`Lägenhetsuppgi:ter`, `Bostadsrä,tsförening`); the wildcard
    tolerates the 1–3 char damage. pdfplumber renders the same input
    with each letter quadrupled (`LLLLäääägggg...`), so we MUST read
    from `fitz_full_text` here, not `page1_text`.
    """
    return bool(
        re.search(r"L[äa]genhetsuppgi.{1,3}ter", ctx.fitz_full_text)
        and re.search(r"Bostadsr[äa].{1,3}tsf[öo]rening", ctx.fitz_full_text)
    )


def _is_fastighetsrapport(ctx: ParseContext) -> bool:
    """Lantmäteriet Fastighetsrapport Plus R."""
    return bool(re.search(r"Fastighetsrapport\s+Plus\s+R", ctx.page1_text))


# ---------- generic helpers ----------


def _bullet_value(text: str, label: str) -> str | None:
    """Match `Label: value` (optionally bulleted with `●`) on a single line."""
    pat = re.compile(
        rf"(?:^|\n)\s*(?:●\s*)?{re.escape(label)}\s*:\s*(?P<value>[^\n]+)"
    )
    m = pat.search(text)
    if not m:
        return None
    return m.group("value").strip() or None


def _format_sek(raw: str | None) -> str | None:
    """Group an integer string into Swedish space-as-thousands form."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return raw
    return re.sub(r"(?<=\d)(?=(\d{3})+$)", " ", digits)


def _compact_digits(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = re.sub(r"\D", "", raw)
    return cleaned or None


_TAX_AMOUNT_RE = re.compile(r"^(\d+)(\(Tax\d+\))?$")


def _format_tax_amount(raw: str | None) -> str | None:
    """`765000(Tax24)` → `765 000 (Tax24)`."""
    if not raw:
        return None
    m = _TAX_AMOUNT_RE.match(raw)
    if not m:
        return raw
    digits, qualifier = m.group(1), m.group(2)
    formatted = re.sub(r"(?<=\d)(?=(\d{3})+$)", " ", digits)
    return f"{formatted} {qualifier}" if qualifier else formatted


# ---------- column-grid word extraction (UC tabular + Fastighetsrapport) ----------
#
# UC Bostad's PDFs (BR + Småhus + the post-2026 BR variant) and Lantmäteriet's
# Fastighetsrapport all use the same label-then-value-on-row-below layout:
# words at column X carry the label, words at column X on the next y-bucket
# below carry the value. The number of columns differs (3-col old BR, 4-col
# new BR Datavärdering, 6-col Småhus, 3-col Fastighetsrapport), but the
# per-cell algorithm is identical.
#
# Row distance is bounded — without the cap, an EMPTY cell (e.g. the Adress
# row in the Bengtsfors fastighet sample) would silently inherit the label
# value of the NEXT section that happens to sit in the same column further
# down the page.

_ROW_TOL = 3.0          # pt: words within this `top` are the same row
_COL_TOL = 3.0          # pt: words within this `x0` are the same column
_MAX_VALUE_BELOW = 18.0  # pt: max y-gap between a label and its value row


def _uc_word_below(
    ctx: ParseContext,
    label_text: str,
    formatter: Callable[[str], str | None] | None = None,
) -> str | None:
    """Find the row below a label word in a UC-tabular Värdeutlåtande."""
    if not _is_datavardering_uc(ctx):
        return None
    raw = _label_text_below_in_column(ctx, label_text)
    if raw is None:
        return None
    return formatter(raw) if formatter else raw


def _label_text_below_in_column(ctx: ParseContext, label_text: str) -> str | None:
    """Find the label word in pdfplumber's word grid, return the row below.

    No PDF-class guard — used by both UC tabular and Fastighetsrapport
    strategies, each of which guards externally.
    """
    label = next((w for w in ctx.page1_words if w["text"] == label_text), None)
    if label is None:
        return None
    return _row_below_in_column(ctx.page1_words, label)


def _row_below_in_column(words: tuple[dict, ...], anchor: dict) -> str | None:
    """Return the cell text on the row immediately below `anchor`.

    Filters to words whose x0 is within `_COL_TOL` of the anchor's x0,
    whose top sits in the next sustained y-bucket below, AND whose
    y-distance is within `_MAX_VALUE_BELOW`. The cap prevents an empty
    cell from inheriting a label further down the page.
    """
    same_col = [
        w for w in words
        if abs(w["x0"] - anchor["x0"]) <= _COL_TOL and w["top"] > anchor["top"] + _ROW_TOL
    ]
    if not same_col:
        return None
    same_col.sort(key=lambda w: w["top"])
    target_top = same_col[0]["top"]
    if target_top - anchor["top"] > _MAX_VALUE_BELOW:
        return None
    row_words = [
        w for w in same_col if abs(w["top"] - target_top) <= _ROW_TOL
    ]
    row_words.sort(key=lambda w: w["x0"])
    text = " ".join(w["text"] for w in row_words).strip()
    return text or None


def _canonical_via_fitz(ctx: ParseContext, raw: str) -> str:
    """Look up the canonical spaced form of `raw` in `fitz_full_text`.

    pdfplumber concatenates adjacent words inside one column cell into a
    single token (`HannaRydhsgata12LGH1303`), but PyMuPDF preserves the
    inter-word spaces (`Hanna Rydhs gata 12 LGH 1303`). Stripping spaces
    from both and matching gives us the human-readable form without
    having to hand-tune a CamelCase splitter for every Swedish compound
    street name.
    """
    target = re.sub(r"\s+", "", raw)
    if not target:
        return raw
    for line in ctx.fitz_full_text.splitlines():
        candidate = line.strip()
        if re.sub(r"\s+", "", candidate) == target:
            return candidate
    return raw


# ---------- objekt assembly helpers ----------

# `Hanna Rydhs gata 12 LGH 1303` → ('Hanna Rydhs gata 12', '1303')
_ADDRESS_WITH_LGH_RE = re.compile(r"^(?P<street>.+?)\s+LGH\s+(?P<lgh>\d+)\s*$")


def _split_address_and_lgh(raw: str) -> tuple[str, str | None]:
    m = _ADDRESS_WITH_LGH_RE.match(raw)
    if not m:
        return raw, None
    return m.group("street").strip(), m.group("lgh")


def _expand_postort(raw: str) -> tuple[str | None, str | None]:
    """`12950Hägersten` or `129 50 Hägersten` → ('129 50', 'Hägersten')."""
    s = re.sub(r"\s+", " ", raw.strip())
    m = re.match(r"^(\d{3}\s?\d{2})\s*(.+)$", s)
    if not m:
        return None, raw
    postnr = m.group(1)
    locality = m.group(2).strip()
    return postnr, locality.title() if locality.isupper() else locality


# ---------- per-slot strategy implementations ----------

# ----- source_class -----


def _source_class_datavardering(ctx: ParseContext) -> str | None:
    if _is_datavardering_uc(ctx) or _is_datavardering_prose(ctx):
        return "datavardering"
    return None


def _source_class_lagenhetsforteckning(ctx: ParseContext) -> str | None:
    return "lagenhetsforteckning" if _is_lgh_utdrag(ctx) else None


def _source_class_fastighetsutdrag(ctx: ParseContext) -> str | None:
    return "fastighetsutdrag" if _is_fastighetsrapport(ctx) else None


# ----- property_shape -----


def _shape_from_prose_upplatelseform(ctx: ParseContext) -> str | None:
    if not _is_datavardering_prose(ctx):
        return None
    raw = _bullet_value(ctx.page1_text, "Upplåtelseform")
    if not raw:
        return None
    if re.search(r"Bostadsr[äa]tt", raw, re.IGNORECASE):
        return "bostadsratt"
    return "fastighet"


def _shape_from_uc_banner(ctx: ParseContext) -> str | None:
    if _is_datavardering_uc_br(ctx):
        return "bostadsratt"
    if _is_datavardering_uc_smahus(ctx):
        return "fastighet"
    return None


def _shape_from_lgh(ctx: ParseContext) -> str | None:
    return "bostadsratt" if _is_lgh_utdrag(ctx) else None


def _shape_from_fastighetsrapport(ctx: ParseContext) -> str | None:
    return "fastighet" if _is_fastighetsrapport(ctx) else None


# ----- objekt + objekt_short -----


def _objekt_prose_bullet(ctx: ParseContext) -> str | None:
    if not _is_datavardering_prose(ctx):
        return None
    return _bullet_value(ctx.page1_text, "Objekt")


def _objekt_uc_br_assembled(ctx: ParseContext) -> str | None:
    """Assemble `LGH N FÖRENING (orgnr)` from UC tabular cells.

    The BR layout exposes three pieces — Adress (carrying the LGH
    suffix), Föreningsinformation (the brf name), Organisationsnummer
    — that the docx template's `objekt` row stitches together.
    """
    if not _is_datavardering_uc_br(ctx):
        return None
    adress_raw = _uc_word_below(ctx, "Adress")
    forening_raw = _uc_word_below(ctx, "Föreningsinformation")
    orgnr_raw = _uc_word_below(ctx, "Organisationsnummer")
    if not (adress_raw and forening_raw and orgnr_raw):
        return None
    adress_spaced = _canonical_via_fitz(ctx, adress_raw)
    _, lgh = _split_address_and_lgh(adress_spaced)
    if not lgh:
        return None
    forening = _canonical_via_fitz(ctx, forening_raw)
    orgnr = _compact_digits(orgnr_raw)
    if not orgnr:
        return None
    return f"LGH {lgh} {forening} ({orgnr})"


def _objekt_uc_smahus_fastighetsbeteckning(ctx: ParseContext) -> str | None:
    if not _is_datavardering_uc_smahus(ctx):
        return None
    raw = _uc_word_below(ctx, "Fastighetsbeteckning")
    if not raw:
        return None
    return _canonical_via_fitz(ctx, raw)


def _objekt_fastighetsrapport_beteckning(ctx: ParseContext) -> str | None:
    """Read the `Beteckning` cell from page 1 of a Fastighetsrapport.

    The layout is a 3-cell header row: `Beteckning / Senaste ändring
    allmänna delen / Totalareal`. The cell below `Beteckning` carries
    the property identifier, e.g. `Bengtsfors NÄRSIDAN 1:21`. UPPERCASE
    fastighet block (and its hyphenated parts) is titlecased to match
    the docx template convention.
    """
    if not _is_fastighetsrapport(ctx):
        return None
    raw = _label_text_below_in_column(ctx, "Beteckning")
    if not raw:
        return None
    spaced = _canonical_via_fitz(ctx, raw)
    parts = []
    for tok in spaced.split():
        parts.append(_titlecase_upper_alpha(tok))
    return " ".join(parts)


def _titlecase_upper_alpha(token: str) -> str:
    """Titlecase a token, splitting on hyphens so `JULITA-ÄNGTORP` → `Julita-Ängtorp`."""
    if "-" in token:
        return "-".join(_titlecase_upper_alpha(part) for part in token.split("-"))
    if token.isalpha() and token.isupper():
        return token.title()
    return token


def _objekt_lgh_assembled(ctx: ParseContext) -> str | None:
    """Assemble `LGH N FÖRENING (orgnr)` from an HSB lägenhetsförteckning."""
    if not _is_lgh_utdrag(ctx):
        return None
    cells = _lgh_label_to_value(ctx)
    lgh = cells.get("skatteverketslghnr") or cells.get("skateverketslghnr")
    forening = cells.get("forening")
    orgnr = _compact_digits(cells.get("organisationsnummer"))
    if not (lgh and forening and orgnr):
        return None
    forening_clean = _fix_lgh_ligatures(forening)
    return f"LGH {lgh} {forening_clean} ({orgnr})"


def _objekt_short_strip_orgnr(ctx: ParseContext) -> str | None:
    """Re-run the `objekt` chain inline and strip a trailing `(\\d+)` parens."""
    for strategy in _OBJEKT_SLOT.strategies:
        full = strategy.extract(ctx)
        if full is None:
            continue
        return re.sub(r"\s*\(\d[\d\-\s]*\)\s*$", "", full).strip()
    return None


# ----- adress -----


def _adress_prose_bullet(ctx: ParseContext) -> str | None:
    if not _is_datavardering_prose(ctx):
        return None
    return _bullet_value(ctx.page1_text, "Adress")


def _adress_uc_below_label(ctx: ParseContext) -> str | None:
    raw = _uc_word_below(ctx, "Adress")
    if not raw:
        return None
    spaced = _canonical_via_fitz(ctx, raw)
    street, _ = _split_address_and_lgh(spaced)
    return street


def _adress_fastighetsrapport(ctx: ParseContext) -> str | None:
    if not _is_fastighetsrapport(ctx):
        return None
    m = re.search(r"\nAdress\n([^\n]+)\n", ctx.fitz_full_text)
    if not m:
        return None
    line = m.group(1).strip()
    street_match = re.match(r"^(.+?),\s*\d{3}\s?\d{2}\b", line)
    return (street_match.group(1) if street_match else line).strip() or None


def _adress_lgh(ctx: ParseContext) -> str | None:
    if not _is_lgh_utdrag(ctx):
        return None
    cells = _lgh_label_to_value(ctx)
    raw = cells.get("adress")
    return _fix_lgh_ligatures(raw) if raw else None


# ----- kommun -----


def _kommun_prose_bullet(ctx: ParseContext) -> str | None:
    if not _is_datavardering_prose(ctx):
        return None
    return _bullet_value(ctx.page1_text, "Kommun")


def _kommun_uc_below_label(ctx: ParseContext) -> str | None:
    return _uc_word_below(ctx, "Kommun")


def _kommun_uc_postort_from_addr(ctx: ParseContext) -> str | None:
    """Fall back to the postort line below `Adress` when no `Kommun` cell.

    UC Småhus + the newer BR Datavärdering put a `<postnr> <ort>` line
    on the row two-below `Adress`. Only fires when the address row
    immediately below `Adress` is non-empty (otherwise we'd walk past
    the empty cell into the next section's labels).
    """
    if not _is_datavardering_uc(ctx):
        return None
    if _uc_word_below(ctx, "Adress") is None:
        return None
    words = ctx.page1_words
    label = next((w for w in words if w["text"] == "Adress"), None)
    if label is None:
        return None
    same_col = sorted(
        [w for w in words if abs(w["x0"] - label["x0"]) <= _COL_TOL and w["top"] > label["top"] + _ROW_TOL],
        key=lambda w: w["top"],
    )
    seen_buckets: list[float] = []
    for w in same_col:
        if not seen_buckets or abs(w["top"] - seen_buckets[-1]) > _ROW_TOL:
            seen_buckets.append(w["top"])
        if len(seen_buckets) == 2:
            break
    if len(seen_buckets) < 2 or seen_buckets[1] - label["top"] > _MAX_VALUE_BELOW * 2:
        return None
    second_top = seen_buckets[1]
    row = [w for w in same_col if abs(w["top"] - second_top) <= _ROW_TOL]
    row.sort(key=lambda w: w["x0"])
    if not row:
        return None
    raw = _canonical_via_fitz(ctx, " ".join(w["text"] for w in row).strip())
    _, locality = _expand_postort(raw)
    return locality


def _kommun_fastighetsrapport(ctx: ParseContext) -> str | None:
    if not _is_fastighetsrapport(ctx):
        return None
    m = re.search(r"\nAdress\n([^\n]+)\n", ctx.fitz_full_text)
    if not m:
        return None
    line = m.group(1).strip()
    locality_match = re.search(r"\d{3}\s?\d{2}\s+(.+?)\s*$", line)
    return locality_match.group(1) if locality_match else None


def _kommun_lgh(ctx: ParseContext) -> str | None:
    if not _is_lgh_utdrag(ctx):
        return None
    cells = _lgh_label_to_value(ctx)
    adress = cells.get("adress")
    if not adress:
        return None
    follow = _lgh_line_after(ctx, _fix_lgh_ligatures(adress))
    if not follow:
        return None
    _, locality = _expand_postort(follow)
    return locality


# ----- upplatelseform -----


def _upplatelseform_prose_bullet(ctx: ParseContext) -> str | None:
    if not _is_datavardering_prose(ctx):
        return None
    return _bullet_value(ctx.page1_text, "Upplåtelseform")


def _upplatelseform_uc_br_banner(ctx: ParseContext) -> str | None:
    return "Bostadsrätt" if _is_datavardering_uc_br(ctx) else None


def _upplatelseform_uc_smahus_implies_frikopt(ctx: ParseContext) -> str | None:
    return "Friköpt" if _is_datavardering_uc_smahus(ctx) else None


def _upplatelseform_fastighetsrapport_implies_frikopt(ctx: ParseContext) -> str | None:
    return "Friköpt" if _is_fastighetsrapport(ctx) else None


def _upplatelseform_lgh_implies_br(ctx: ParseContext) -> str | None:
    return "Bostadsrätt" if _is_lgh_utdrag(ctx) else None


# ----- marknadsvarde_kr -----


def _marknadsvarde_uc_below_label(ctx: ParseContext) -> str | None:
    raw = _uc_word_below(ctx, "Marknadsvärde")
    return _format_sek(raw)


_PROSE_VALUE_RE = re.compile(
    r"Marknadsv[äa]rdet\s+bed[öo]ms\s+till\s+(?P<amount>[\d\s]+?)\s*kr"
    r"(?:[^.]*?intervall\s+om\s*(?:\+/-|±)?\s*(?P<interval>[\d\s]+?)\s*kr)?",
    re.IGNORECASE,
)


def _marknadsvarde_prose_bedoms_till(ctx: ParseContext) -> str | None:
    if not _is_datavardering_prose(ctx):
        return None
    m = _PROSE_VALUE_RE.search(ctx.page1_text)
    return _format_sek(m.group("amount")) if m else None


# ----- intervall_kr -----


def _intervall_uc_uppat(ctx: ParseContext) -> str | None:
    raw = _uc_word_below(ctx, "Osäkerhetuppåt")
    return _format_sek(raw)


def _intervall_prose_symmetric(ctx: ParseContext) -> str | None:
    if not _is_datavardering_prose(ctx):
        return None
    m = _PROSE_VALUE_RE.search(ctx.page1_text)
    if not m or not m.group("interval"):
        return None
    return _format_sek(m.group("interval"))


# ----- document_date -----


_FOOTER_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\s*\d{2}:\d{2}")
_LOOSE_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_PROSE_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")


def _document_date_uc_footer(ctx: ParseContext) -> str | None:
    if not _is_datavardering_uc(ctx):
        return None
    m = _FOOTER_DATE_RE.search(ctx.page1_text)
    if m:
        return m.group(1)
    m = _LOOSE_DATE_RE.search(ctx.page1_text)
    return m.group(1) if m else None


def _document_date_prose_dd_m_yyyy(ctx: ParseContext) -> str | None:
    if not _is_datavardering_prose(ctx):
        return None
    m = _PROSE_DATE_RE.search(ctx.page1_text)
    if not m:
        return None
    day, month, year = m.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def _document_date_lgh_utskriftsdatum(ctx: ParseContext) -> str | None:
    if not _is_lgh_utdrag(ctx):
        return None
    m = re.search(r"Utskri.{1,3}tsdatum.{0,5}(\d{4}-\d{2}-\d{2})", ctx.fitz_full_text)
    return m.group(1) if m else None


def _document_date_fastighetsrapport(ctx: ParseContext) -> str | None:
    if not _is_fastighetsrapport(ctx):
        return None
    raw = _label_text_below_in_column(ctx, "Aktualitetsdatum")
    if raw and re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw
    m = re.search(r"Aktualitetsdatum\s+inskrivning\s*\n(\d{4}-\d{2}-\d{2})", ctx.fitz_full_text)
    return m.group(1) if m else None


def _document_date_fastighetsrapport_footer(ctx: ParseContext) -> str | None:
    if not _is_fastighetsrapport(ctx):
        return None
    m = _FOOTER_DATE_RE.search(ctx.fitz_full_text)
    return m.group(1) if m else None


# ---------- LGH-utdrag shared cell parsing ----------
#
# HSB ships labels duplicated 4× per line and may drop ligatures. We dedup
# consecutive identical lines, then for each known label-stem capture the
# next non-label line as its value.

_LGH_STEMS: dict[str, str] = {
    "lghnr": "lghnr",
    "skateverketslghnr": "skatteverketslghnr",
    "skatteverketslghnr": "skatteverketslghnr",
    "antalrum": "antalrum",
    "lagenhetsyta": "lagenhetsyta",
    "vaning": "vaning",
    "adress": "adress",
    "forvarvsdatum": "forvarvsdatum",
    "forening": "forening",
    "fastighetsbeteckning": "fastighetsbeteckning",
    "organisationsnummer": "organisationsnummer",
    "registreradekonomiskplan": "registreradekonomiskplan",
    "andelidag": "andelidag",
}

_STEM_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_SE_NORMALIZE = str.maketrans({"å": "a", "ä": "a", "ö": "o"})


def _stem(label: str) -> str:
    return _STEM_NON_ALNUM.sub("", label.lower().translate(_SE_NORMALIZE))


def _dedup_lines(text: str) -> str:
    out: list[str] = []
    prev: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line == prev:
            continue
        out.append(line)
        prev = line
    return "\n".join(out)


def _lgh_label_to_value(ctx: ParseContext) -> dict[str, str]:
    dedup = _dedup_lines(ctx.fitz_full_text)
    lines = [ln.strip() for ln in dedup.splitlines() if ln.strip()]
    known = set(_LGH_STEMS.keys())
    result: dict[str, str] = {}
    i = 0
    while i < len(lines):
        stem = _stem(lines[i])
        if stem in known and _LGH_STEMS[stem] not in result:
            j = i + 1
            while j < len(lines) and _stem(lines[j]) in known:
                j += 1
            if j < len(lines):
                result[_LGH_STEMS[stem]] = lines[j]
        i += 1
    return result


def _lgh_line_after(ctx: ParseContext, target_line: str) -> str | None:
    dedup = _dedup_lines(ctx.fitz_full_text)
    lines = [ln.strip() for ln in dedup.splitlines() if ln.strip()]
    target_dirty = next(
        (ln for ln in lines if _fix_lgh_ligatures(ln) == target_line),
        None,
    )
    if target_dirty is None:
        return None
    try:
        idx = lines.index(target_dirty)
    except ValueError:
        return None
    return lines[idx + 1] if idx + 1 < len(lines) else None


_LIGATURE_FIXES = (
    (re.compile(r"Bostadsrä,tsförening"), "Bostadsrättsförening"),
    (re.compile(r"Lägenhetsuppgi:ter"), "Lägenhetsuppgifter"),
    (re.compile(r"Utskri'tsdatum"), "Utskriftsdatum"),
    (re.compile(r"Ska,teverkets"), "Skatteverkets"),
    (re.compile(r"Lägenhetsuppgi9ter"), "Lägenhetsuppgifter"),
    (re.compile(r"Bostadsrä;tsförening"), "Bostadsrättsförening"),
    (re.compile(r"BostadsräFtsförening"), "Bostadsrättsförening"),
    (re.compile(r"Kontaktuppgi:ter"), "Kontaktuppgifter"),
)


def _fix_lgh_ligatures(text: str | None) -> str | None:
    """Restore the `ff`/`ft`/`tt` ligatures HSB's CMap eats.

    Each ligature-damaged token surfaces with a non-letter glyph (`;`,
    `:`, `9`, `,`, `'`, `F`) where the ligature should be. The fixes
    here cover the labels and the förening-name token we surface
    through to the docx output.
    """
    if text is None:
        return None
    out = text
    for pat, sub in _LIGATURE_FIXES:
        out = pat.sub(sub, out)
    return out


# ---------- slot inventory ----------
#
# The order here drives the Om verktyget table; keep the docx-template
# slots first (objekt → intervall_kr), with the derived/source-class
# fields trailing as routing hints.


_SOURCE_CLASS_SLOT = Slot(
    key="source_class",
    description=(
        "Avgör vilken datumrad i mallen den här PDF:en fyller "
        "(datavärdering, lägenhetsförteckning eller fastighetsutdrag)."
    ),
    strategies=(
        Strategy(
            "datavardering_fingerprint",
            "Fastighetsbyrån prosa-utlåtande (VÄRDEUTLÅTANDE / Värderingsobjekt) eller UC-tabellrapport",
            _source_class_datavardering,
        ),
        Strategy(
            "lagenhetsforteckning_fingerprint",
            "HSB lägenhetsförteckning (Lägenhetsuppgifter + Bostadsrättsförening i texten)",
            _source_class_lagenhetsforteckning,
        ),
        Strategy(
            "fastighetsutdrag_fingerprint",
            "Lantmäteriet Fastighetsrapport (rubrik 'Fastighetsrapport Plus R')",
            _source_class_fastighetsutdrag,
        ),
    ),
)


_PROPERTY_SHAPE_SLOT = Slot(
    key="property_shape",
    description=(
        "Avgör om mallen ska renderas i bostadsrätts- eller "
        "fastighetsläge."
    ),
    strategies=(
        Strategy(
            "prose_upplatelseform_classify",
            "Fastighetsbyrån prosa: 'Upplåtelseform:'-raden avgör bostadsrätt eller fastighet",
            _shape_from_prose_upplatelseform,
        ),
        Strategy(
            "uc_banner_classify",
            "UC-rapportens rubrik: 'Bostadsrätt' eller 'Småhus' avgör",
            _shape_from_uc_banner,
        ),
        Strategy(
            "lgh_implies_bostadsratt",
            "HSB lägenhetsförteckning ⇒ bostadsrätt",
            _shape_from_lgh,
        ),
        Strategy(
            "fastighetsrapport_implies_fastighet",
            "Lantmäteriets fastighetsrapport ⇒ fastighet",
            _shape_from_fastighetsrapport,
        ),
    ),
)


_OBJEKT_SLOT = Slot(
    key="objekt",
    description="Fastighetsbeteckning eller lägenhetsbeteckning som den skrivs på mallens 'Objekt'-rad.",
    strategies=(
        Strategy(
            "prose_objekt_bullet",
            "Fastighetsbyrån prosa: 'Objekt:'-raden, ordagrant",
            _objekt_prose_bullet,
        ),
        Strategy(
            "uc_br_assemble_from_cells",
            "UC bostadsrätt: 'LGH <nr> <förening> (<orgnr>)' satt ihop av Adress, Föreningsinformation och Organisationsnummer",
            _objekt_uc_br_assembled,
        ),
        Strategy(
            "uc_smahus_fastighetsbeteckning",
            "UC småhus: cellen under 'Fastighetsbeteckning'",
            _objekt_uc_smahus_fastighetsbeteckning,
        ),
        Strategy(
            "fastighetsrapport_beteckning",
            "Lantmäteriets fastighetsrapport: raden under 'Beteckning'",
            _objekt_fastighetsrapport_beteckning,
        ),
        Strategy(
            "lgh_assemble_from_cells",
            "HSB lägenhetsförteckning: 'LGH <skatteverket-nr> <förening> (<orgnr>)' satt ihop av lägenhets- och föreningsuppgifter",
            _objekt_lgh_assembled,
        ),
    ),
)


_OBJEKT_SHORT_SLOT = Slot(
    key="objekt_short",
    description="Beteckning utan organisationsnummer i parentes — används i löptext.",
    strategies=(
        Strategy(
            "derived_strip_orgnr_parens",
            "Samma som 'Objekt' men utan organisationsnummer i parentes",
            _objekt_short_strip_orgnr,
        ),
    ),
)


_ADRESS_SLOT = Slot(
    key="adress",
    description="Gata och husnummer för mallens 'Adress'-rad.",
    strategies=(
        Strategy(
            "prose_adress_bullet",
            "Fastighetsbyrån prosa: 'Adress:'-raden",
            _adress_prose_bullet,
        ),
        Strategy(
            "uc_label_adress_below",
            "UC-tabell: cellen under rubriken 'Adress'",
            _adress_uc_below_label,
        ),
        Strategy(
            "fastighetsrapport_adress_line",
            "Lantmäteriets fastighetsrapport: gatuadressen på raden under 'Adress' (före postnr)",
            _adress_fastighetsrapport,
        ),
        Strategy(
            "lgh_adress_label_value",
            "HSB lägenhetsförteckning: värdet på 'Adress'-raden",
            _adress_lgh,
        ),
    ),
)


_KOMMUN_SLOT = Slot(
    key="kommun",
    description="Postort för mallens 'Kommun'-rad.",
    strategies=(
        Strategy(
            "prose_kommun_bullet",
            "Fastighetsbyrån prosa: 'Kommun:'-raden",
            _kommun_prose_bullet,
        ),
        Strategy(
            "uc_label_kommun_below",
            "UC-tabell: cellen under rubriken 'Kommun'",
            _kommun_uc_below_label,
        ),
        Strategy(
            "uc_postort_below_address",
            "UC-tabell: postorten två rader under 'Adress' (om Kommun-cellen saknas)",
            _kommun_uc_postort_from_addr,
        ),
        Strategy(
            "fastighetsrapport_locality_from_addr",
            "Lantmäteriets fastighetsrapport: postorten efter postnr på raden under 'Adress'",
            _kommun_fastighetsrapport,
        ),
        Strategy(
            "lgh_postort_from_address_followup",
            "HSB lägenhetsförteckning: postorten på raden efter 'Adress'",
            _kommun_lgh,
        ),
    ),
)


_UPPLATELSEFORM_SLOT = Slot(
    key="upplatelseform",
    description="Bostadsrätt, Friköpt eller Tomträtt — styr om mallen renderas i BR- eller fastighetsläge.",
    strategies=(
        Strategy(
            "prose_upplatelseform_bullet",
            "Fastighetsbyrån prosa: värdet på 'Upplåtelseform:'-raden",
            _upplatelseform_prose_bullet,
        ),
        Strategy(
            "uc_br_banner_bostadsratt",
            "UC-rapportens rubrik 'Värdeutlåtande / Bostadsrätt' ⇒ Bostadsrätt",
            _upplatelseform_uc_br_banner,
        ),
        Strategy(
            "uc_smahus_banner_frikopt",
            "UC-rapportens rubrik 'Värdeutlåtande / Småhus' ⇒ Friköpt",
            _upplatelseform_uc_smahus_implies_frikopt,
        ),
        Strategy(
            "fastighetsrapport_frikopt",
            "Lantmäteriets fastighetsrapport ⇒ Friköpt (lagfartsägd fastighet)",
            _upplatelseform_fastighetsrapport_implies_frikopt,
        ),
        Strategy(
            "lgh_bostadsratt",
            "HSB lägenhetsförteckning ⇒ Bostadsrätt",
            _upplatelseform_lgh_implies_br,
        ),
    ),
)


_MARKNADSVARDE_SLOT = Slot(
    key="marknadsvarde_kr",
    description="Bedömt marknadsvärde i kronor (förslag till granskning).",
    strategies=(
        Strategy(
            "uc_label_marknadsvarde_below",
            "UC-tabell: cellen under rubriken 'Marknadsvärde'",
            _marknadsvarde_uc_below_label,
        ),
        Strategy(
            "prose_bedoms_till_x_kr",
            "Fastighetsbyrån prosa: beloppet i meningen 'Marknadsvärdet bedöms till X kr'",
            _marknadsvarde_prose_bedoms_till,
        ),
    ),
)


_INTERVALL_SLOT = Slot(
    key="intervall_kr",
    description="Osäkerhetsintervall i kronor — det belopp som mallens 'intervall om ± X kr' visar.",
    strategies=(
        Strategy(
            "uc_label_osakerhet_uppat_below",
            "UC-tabell: cellen under rubriken 'Osäkerhet uppåt'",
            _intervall_uc_uppat,
        ),
        Strategy(
            "prose_symmetric_interval",
            "Fastighetsbyrån prosa: beloppet i meningen 'intervall om ± X kr' (symmetriskt uppåt/nedåt)",
            _intervall_prose_symmetric,
        ),
    ),
)


_DOCUMENT_DATE_SLOT = Slot(
    key="document_date",
    description=(
        "Datumet då PDF:en utfärdades — fyller motsvarande datumrad i "
        "mallen beroende på underlagstyp."
    ),
    strategies=(
        Strategy(
            "uc_footer_iso_timestamp",
            "UC-rapport: datumet i tidsstämpeln (YYYY-MM-DD HH:MM) i sidfoten på sida 1",
            _document_date_uc_footer,
        ),
        Strategy(
            "prose_dd_m_yyyy_stamp",
            "Fastighetsbyrån prosa: datumstämpeln ovanför 'Utfärdat av'",
            _document_date_prose_dd_m_yyyy,
        ),
        Strategy(
            "lgh_utskriftsdatum_header",
            "HSB lägenhetsförteckning: 'Utskriftsdatum: YYYY-MM-DD' i sidhuvudet",
            _document_date_lgh_utskriftsdatum,
        ),
        Strategy(
            "fastighetsrapport_aktualitetsdatum",
            "Lantmäteriets fastighetsrapport: raden under 'Aktualitetsdatum inskrivning'",
            _document_date_fastighetsrapport,
        ),
        Strategy(
            "fastighetsrapport_footer_timestamp",
            "Lantmäteriets fastighetsrapport: tidsstämpeln (YYYY-MM-DD HH:MM) i sidfoten på sida 1",
            _document_date_fastighetsrapport_footer,
        ),
    ),
)


SLOTS: tuple[Slot, ...] = (
    _OBJEKT_SLOT,
    _OBJEKT_SHORT_SLOT,
    _ADRESS_SLOT,
    _KOMMUN_SLOT,
    _UPPLATELSEFORM_SLOT,
    _MARKNADSVARDE_SLOT,
    _INTERVALL_SLOT,
    _DOCUMENT_DATE_SLOT,
    _SOURCE_CLASS_SLOT,
    _PROPERTY_SHAPE_SLOT,
)


# ---------- entrypoint ----------


def extract_fields(pdf_bytes: bytes, filename: str) -> ExtractionResult:
    ctx = build_context(pdf_bytes)
    result = ExtractionResult(filename=filename)
    for slot in SLOTS:
        result.fields.append(slot.run(ctx, filename))
    if _needs_comparable_sales(ctx) and ctx.page_count > 1:
        result.extras["comparable_sales"] = _extract_comparable_sales_p2(pdf_bytes)
    return result


def _needs_comparable_sales(ctx: ParseContext) -> bool:
    """Only UC Bostad BR exposes a `Sålda bostadsrätter i området` table."""
    return _is_datavardering_uc_br(ctx)


# ---------- comparable-sales table (page 2, UC BR only) ----------


_COMPARABLE_DATE_RE = re.compile(r"\s+(\d{4}-\d{2})$")
_BALKONG_TOKENS = {"ja", "nej"}


def _extract_comparable_sales_p2(pdf_bytes: bytes) -> list[dict]:
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
    forening = " ".join(forening_tokens) if forening_tokens else None

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
