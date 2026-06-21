"""Populate the Värdeutlåtande .docx template.

Two-pass strategy:
  1. Per-paragraph substitution: collapse multi-run text, apply placeholder
     replacements, write the substituted text back into the paragraph's
     first run (preserving its formatting).
  2. Source-clause assembly: rewrite the "...inhämtats från tillgängliga
     underlag som ... beaktats i värderingen" sentence to include only
     the source documents the operator actually uploaded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib import resources
from io import BytesIO
from typing import Literal

from docx import Document


# Two property-type modes, derived from the upload mix or operator override.
PropertyMode = Literal["bostadsratt", "frikopt"]


@dataclass
class TemplateFields:
    # Top-level identifiers. `objekt` is the full identifier with org# parens
    # used in the Objekt row; `objekt_short` is the same identifier without
    # parenthetical org# used in the running-text "Vid värdering av..." line.
    # For Friköpt mode the two are identical (no parenthetical).
    objekt: str                 # "LGH 1303 HSB Brf Långpannan i Stockholm (7696097448)"
                                # or "Vaggeryd Hok 2:139"
    objekt_short: str           # "LGH 1303 HSB Brf Långpannan i Stockholm"
                                # or "Vaggeryd Hok 2:139"
    adress: str                 # "Hanna Rydhs gata 12"
    kommun: str                 # "Hägersten" / "Hok"
    upplatelseform: str         # "Bostadsrätt" / "Friköpt" / "Tomträtt"

    # Description-sentence dates; populate the ones whose source was uploaded
    # and leave the rest None — the sentence-cleanup pass will drop them.
    datavardering_date: str | None
    fastighetsutdrag_date: str | None
    lagenhetsforteckning_date: str | None

    # Free-text appraiser note ("[Fyll på ifall bilder...]"). None → drop the marker.
    bilder_note: str | None

    # Value-bedömning section.
    likviditet: str             # "god" / "normal" / "låg"
    marknadsvarde_kr: str       # "3 050 000"
    intervall_kr: str           # "50 000"

    # Identity footer.
    ort: str                    # "Stockholm"
    datum: str                  # "18/6/2026"
    maklare_namn: str           # "Jenny Wiklund"
    maklare_titel: str          # "Registrerad fastighetsmäklare"
    foretag: str                # "Fastighetsbyrån"

    # Mode is derived from upplatelseform but kept explicit so callers can
    # override the LGH-vs-fastighet identifier branch.
    mode: PropertyMode = "bostadsratt"


def populate(fields: TemplateFields) -> bytes:
    """Open the bundled mall.docx, apply substitutions, return docx bytes."""
    template_path = resources.files("app.valuation_statement.templates").joinpath(
        "vardeutlatande_mall.docx"
    )
    with template_path.open("rb") as f:
        doc = Document(f)

    replacements = _build_replacements(fields)
    for para in doc.paragraphs:
        if not para.text:
            continue
        new_text = _apply_replacements(para.text, replacements)
        new_text = _assemble_source_clauses(new_text, fields)
        new_text = _finalize_belopp(new_text, fields.marknadsvarde_kr, fields.intervall_kr)
        new_text = _strip_metacomments(new_text)
        new_text = _apply_bilder_note(new_text, fields.bilder_note)
        if new_text != para.text:
            _rewrite_paragraph(para, new_text)

    out = BytesIO()
    doc.save(out)
    return out.getvalue()


# ---------- placeholder substitution ----------

def _build_replacements(f: TemplateFields) -> list[tuple[str, str]]:
    """Ordered replacement list — longer/more-specific patterns first."""
    repls: list[tuple[str, str]] = []

    if f.mode == "bostadsratt":
        # Both top-of-row placeholders fold into the BR-style identifier in
        # the second slot; the first slot becomes empty and is trimmed.
        repls.append(("[Lägenhetsnummer, BRF namn och orgnr]", f.objekt))
        repls.append(("[Kommun Fastighet 1:1]", ""))
    else:
        repls.append(("[Kommun Fastighet 1:1]", f.objekt))
        repls.append(("[Lägenhetsnummer, BRF namn och orgnr]", ""))

    repls.append(("[Gatuadress, postort]", f.adress))
    repls.append(("[Friköpt / Tomträtt / Bostadsrätt]", f.upplatelseform))
    # The bracketed [Kommun] (with capital K) appears in two places; both fill
    # with the same value.
    repls.append(("[Kommun]", f.kommun))
    repls.append(("[objekt]", f.objekt_short))
    repls.append(("[god/normal/låg]", f.likviditet))
    # [belopp] appears twice in one paragraph (marknadsvärde, then intervall).
    # We replace both with a unique sentinel here so the substitution loop is
    # commutative, then `_finalize_belopp` walks the result and replaces the
    # two sentinels in order.
    repls.append(("[belopp]", "␞BELOPP␞"))
    # Ort is optional in the filled output. If empty, drop the "[Ort]," prefix
    # entirely so the line reads "18/6/2026" instead of ",18/6/2026". We handle
    # this with a multi-step substitution: the connector ',' between Ort and
    # Datum is part of the [Ort], pattern so we replace ",[Datum]" or "[Ort],[Datum]"
    # as a single unit.
    datum_display = _swedish_display_date(f.datum)
    if f.ort:
        repls.append(("[Ort],[Datum]", f"{f.ort}, {datum_display}"))
    else:
        repls.append(("[Ort],[Datum]", datum_display))
    repls.append(("[Ort]", f.ort))
    repls.append(("[Datum]", datum_display))
    repls.append(("[Mäklarens namn]", f.maklare_namn))
    repls.append(("[Titel/Funktion]", f.maklare_titel))
    repls.append(("[Företagets namn]", f.foretag))

    return repls


def _apply_replacements(text: str, replacements: list[tuple[str, str]]) -> str:
    for needle, sub in replacements:
        text = text.replace(needle, sub)
    return text


_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def _swedish_display_date(value: str) -> str:
    """Render an ISO YYYY-MM-DD date as Swedish DD/M/YYYY for the footer.

    The web client switched the [Datum] input to <input type='date'>, which
    submits ISO-format strings. The Värdeutlåtande footer convention is the
    short Swedish form (e.g. "18/6/2026"). Any non-ISO input — empty strings,
    operator-typed text, legacy clients — passes through unchanged.
    """
    if not value:
        return value
    m = _ISO_DATE_RE.match(value.strip())
    if not m:
        return value
    year, month, day = m.groups()
    return f"{int(day)}/{int(month)}/{year}"


def _finalize_belopp(text: str, marknadsvarde: str, intervall: str) -> str:
    """Replace the [belopp] sentinels with the marknadsvärde then intervall."""
    sentinel = "␞BELOPP␞"
    text = text.replace(sentinel, marknadsvarde, 1)
    text = text.replace(sentinel, intervall, 1)
    return text


# ---------- source-clause assembly ----------

_SOURCE_PHRASE_RE = re.compile(
    r"inhämtats från tillgängliga underlag som (.+?) beaktats i värderingen",
    re.DOTALL,
)

_SOURCE_CLAUSES = {
    "datavardering_date": "datavärdering per datum {date}",
    "fastighetsutdrag_date": "fastighetsutdrag per datum {date}",
    "lagenhetsforteckning_date": "lägenhetsförteckning per datum {date}",
}


def _assemble_source_clauses(text: str, fields: TemplateFields) -> str:
    """Rewrite the 'inhämtats från tillgängliga underlag som ...' sentence to
    list only the sources actually uploaded, with correct connector grammar.
    """
    match = _SOURCE_PHRASE_RE.search(text)
    if not match:
        return text

    available: list[str] = []
    for attr, tmpl in _SOURCE_CLAUSES.items():
        date = getattr(fields, attr)
        if date:
            available.append(tmpl.format(date=date))

    if not available:
        # No source — drop the whole "underlag som ..." phrase.
        rebuilt = "inhämtats från tillgängliga underlag beaktats i värderingen"
    elif len(available) == 1:
        rebuilt = f"inhämtats från tillgängliga underlag som {available[0]} beaktats i värderingen"
    else:
        head = ", ".join(available[:-1])
        rebuilt = (
            f"inhämtats från tillgängliga underlag som {head} och {available[-1]} "
            f"beaktats i värderingen"
        )

    return text[: match.start()] + rebuilt + text[match.end() :]


# ---------- meta-comment cleanup ----------

_METACOMMENTS = (
    "[Enbart bostadsrätt]",
)


def _strip_metacomments(text: str) -> str:
    for marker in _METACOMMENTS:
        text = text.replace(marker, "")
    # Collapse the runs of whitespace left behind, but preserve newlines.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" +\n", "\n", text)
    # Trim leading whitespace immediately after a colon (e.g. "Objekt:  X").
    text = re.sub(r":\s+", ": ", text)
    # Drop the BR-mode "Vid låg likviditet..." reminder sentence — it's an
    # author-facing hint, never present in the filled outputs.
    text = re.sub(
        r"\s*Vid låg likviditet behöver vi en förklaring, t\.ex\. sålda objekt senaste 3/12 månader\.\s*",
        " ",
        text,
    )
    return text


_BILDER_RE = re.compile(
    r"\s*\[Fyll på ifall bilder har inhämtats och bedömning ifall det föreligger renoveringsbehov eller annat\]\.?",
)


def _apply_bilder_note(text: str, note: str | None) -> str:
    """Replace the picture-note placeholder. None → drop the marker entirely."""
    if note:
        return _BILDER_RE.sub(f" {note}", text)
    return _BILDER_RE.sub("", text)


# ---------- paragraph rewrite ----------

def _rewrite_paragraph(para, new_text: str) -> None:
    """Replace the paragraph text while preserving the lead run's formatting."""
    if not para.runs:
        return
    first = para.runs[0]
    for run in para.runs[1:]:
        run.text = ""
    first.text = new_text
