"""Parser for HSB's "Lägenhetsuppgifter" extract (lägenhetsförteckning).

HSB's PDF embeds a font whose CMap drops `ff`/`ft`/`tt` ligatures and
duplicates labels four times. PyMuPDF surfaces the raw glyph stream
(values come through clean; labels come through 4× and with `;` / `9` / `'`
where ligatures should be). We dedup label lines and look up by
"strip-non-alnum-lowercase" stem so the ligature corruption doesn't matter.
"""

from __future__ import annotations

import re
from io import BytesIO

import fitz  # PyMuPDF

from app.valuation_statement.classifier import DocumentType
from app.valuation_statement.extraction import ExtractedField, ExtractionResult


# Map normalized label-stem → canonical key. Stems are computed by
# `_stem(label)`: lowercase + strip every non-alphanumeric character.
# Ligature damage drops out: 'Ska;teverkets lgh-nr' and 'Skatteverkets lgh-nr'
# both stem to 'skateverketslghnr'/'skatteverketslghnr' — so we list both.
_LABEL_STEMS: dict[str, str] = {
    "lghnr": "lgh_internal_hsb",
    # 'Ska;teverkets lgh-nr' (ligature-damaged) and clean form
    "skateverketslghnr": "lgh_skatteverket",
    "skatteverketslghnr": "lgh_skatteverket",
    "antalrum": "antal_rum",
    "lagenhetsyta": "boarea",
    "vaning": "vaning",
    "adress": "adress",
    "forvarvsdatum": "forvarvsdatum",
    "forening": "forening_namn",
    "fastighetsbeteckning": "fastighetsbeteckning",
    "organisationsnummer": "organisationsnummer",
    "registreradekonomiskplan": "registrerad_ekonomisk_plan",
    "andelidag": "andel",
}


def parse(pdf_bytes: bytes, filename: str) -> ExtractionResult:
    result = ExtractionResult(
        document_type=DocumentType.LGH_UTDRAG,
        filename=filename,
    )

    raw_pages = _read_pages(pdf_bytes)
    full_text = "\n".join(raw_pages)
    dedup_text = _dedup_repeated_lines(full_text)

    label_to_value = _pair_labels_with_values(dedup_text)

    # Multiple stems can map to the same canonical key (ligature-damaged
    # variants). Emit one field per key, preferring the first stem with
    # a non-None value.
    seen_keys: set[str] = set()
    for stem, key in _LABEL_STEMS.items():
        if key in seen_keys:
            continue
        value = label_to_value.get(stem)
        if value is None:
            # Try alternate stems for the same key before giving up.
            for alt_stem, alt_key in _LABEL_STEMS.items():
                if alt_key == key and alt_stem != stem and label_to_value.get(alt_stem):
                    value = label_to_value[alt_stem]
                    break
        result.fields.append(
            ExtractedField(
                key=key,
                value=_clean(value),
                confidence="confident" if value else "not_found",
                source_filename=filename,
                source_page=_page_of(stem, raw_pages),
            )
        )
        seen_keys.add(key)

    # The "Adress" row in LGH spans two lines: street, then postnr + locality.
    address = label_to_value.get("adress")
    postnr, postort = _split_address_followup(dedup_text, address)
    result.fields.append(
        ExtractedField(
            key="postnummer",
            value=postnr,
            confidence="confident" if postnr else "not_found",
            source_filename=filename,
        )
    )
    result.fields.append(
        ExtractedField(
            key="postort",
            value=postort,
            confidence="confident" if postort else "not_found",
            source_filename=filename,
            note="Original is upper-case (HÄGERSTEN); reformatted to TitleCase to match template.",
        )
    )

    # Document date sits in the page header ("Utskri'tsdatum: 2026-06-09").
    m = re.search(r"Utskri.{1,3}tsdatum.{0,5}(\d{4}-\d{2}-\d{2})", full_text)
    result.fields.append(
        ExtractedField(
            key="document_date",
            value=m.group(1) if m else None,
            confidence="confident" if m else "not_found",
            source_filename=filename,
            note="Date the lägenhetsförteckning extract was issued.",
        )
    )

    return result


# ---------- raw text + dedup ----------

def _read_pages(pdf_bytes: bytes) -> list[str]:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return [page.get_text() or "" for page in doc]


def _dedup_repeated_lines(text: str) -> str:
    """Collapse the 4× label duplication HSB ships.

    The duplication is always whole-line (`Lgh-nr\nLgh-nr\nLgh-nr\nLgh-nr`),
    so we only need consecutive-line dedup.
    """
    out: list[str] = []
    prev: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line == prev:
            continue
        out.append(line)
        prev = line
    return "\n".join(out)


# ---------- label → value pairing ----------

_STEM_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _stem(label: str) -> str:
    return _STEM_NON_ALNUM.sub("", label.lower().translate(_SE_NORMALIZE))


# Swedish letter normalisation for stems (keeps å/ä/ö comparable to a/a/o)
_SE_NORMALIZE = str.maketrans({"å": "a", "ä": "a", "ö": "o"})


def _pair_labels_with_values(text: str) -> dict[str, str]:
    """For each known label-stem, capture the next non-blank line as its value.

    Walks the deduped text top-to-bottom. When a line stems to a known
    label, the *immediately following* non-empty, non-label line is the
    value. Values are not consumed by later labels (one value per label).
    """
    known_stems = set(_LABEL_STEMS.keys())
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    result: dict[str, str] = {}
    i = 0
    while i < len(lines):
        stem = _stem(lines[i])
        if stem in known_stems and stem not in result:
            # Find next line that's not itself a known label.
            j = i + 1
            while j < len(lines) and _stem(lines[j]) in known_stems:
                j += 1
            if j < len(lines):
                result[stem] = lines[j]
        i += 1
    return result


def _split_address_followup(dedup_text: str, address_line: str | None) -> tuple[str | None, str | None]:
    if not address_line:
        return None, None
    lines = [ln.strip() for ln in dedup_text.splitlines() if ln.strip()]
    try:
        idx = lines.index(address_line)
    except ValueError:
        return None, None
    if idx + 1 >= len(lines):
        return None, None
    follow = lines[idx + 1]
    m = re.match(r"^(\d{5})\s+(.+)$", follow)
    if not m:
        return None, None
    return m.group(1), m.group(2).title()


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip()
    return v or None


def _page_of(stem: str, pages: list[str]) -> int | None:
    for i, raw in enumerate(pages, start=1):
        dedup = _dedup_repeated_lines(raw)
        for line in dedup.splitlines():
            if _stem(line) == stem:
                return i
    return None
