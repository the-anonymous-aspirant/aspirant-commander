"""Content fingerprints must survive how the text projection renders (#5371).

Refactor #21 (`f9611d9`) moved the fingerprints from `classifier.py` into
`field_extractor.py` and reimplemented them. Every pre-refactor pattern
carried `re.IGNORECASE`; none of the five that landed did, and the two UC
Bostad guards additionally changed `\\s+` to `\\s*\\n\\s*`, so the banner's two
words had to fall on separate lines. Measured against the ten real samples,
an upper-cased rendering lost six recognitions and a same-line rendering lost
four — and when every guard goes False the document is wholly unrecognised,
which is HTTP 200 with an empty form: the #5359 symptom exactly.

Neither narrowing was pinned by a test, which is why a refactor could drop
both silently. These tests pin the tolerance so it cannot happen again.

`_is_datavardering_prose` is deliberately NOT in the tolerant set — see
`test_prose_guard_stays_case_sensitive`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.valuation_statement._context import ParseContext, build_context
from app.valuation_statement.field_extractor import (
    CONTENT_GUARDS,
    _is_datavardering_prose,
    evaluate_content_guards,
)
from tests.sample_resolver import load_aliases, resolve

GUARDS = dict(CONTENT_GUARDS)

# The four guards #5371 restored tolerance to. `datavardering_prose` is
# excluded on purpose and `test_prose_guard_stays_case_sensitive` says why.
TOLERANT = frozenset(
    {
        "datavardering_uc_br",
        "datavardering_uc_smahus",
        "fastighetsrapport",
        "lgh_utdrag",
    }
)

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "golden"
SAMPLE_DIR = Path(os.environ.get("VALUATION_SAMPLE_DIR") or "/tmp/vardeutlatande")
ALIASES = load_aliases(GOLDEN_DIR)


def _ctx(page1: str, fitz: str | None = None) -> ParseContext:
    """A context carrying only the two text projections the guards read."""
    return ParseContext(
        page1_text=page1,
        page1_words=(),
        page_texts=(page1,),
        fitz_full_text=fitz if fitz is not None else page1,
    )


# One minimal page-1 banner per layout, in the casing and line breaking the
# samples on the cell happen to use. Each is the text its own guard claims.
BANNERS: dict[str, str] = {
    "datavardering_uc_br": "Värdeutlåtande\nBostadsrätt\nUC Bostadsvärdering",
    "datavardering_uc_smahus": "Värdeutlåtande\nSmåhus\nUC Bostadsvärdering",
    "fastighetsrapport": "Fastighetsrapport\nPlus R\nLantmäteriet",
    "lgh_utdrag": "Lägenhetsuppgifter\nBostadsrättsförening Långpannan",
}

# How a text projection may legitimately differ from the sample we happened to
# capture. Neither changes which document the reader is holding.
def _upper(text: str) -> str:
    return text.upper()


def _lower(text: str) -> str:
    return text.lower()


def _one_line(text: str) -> str:
    return text.replace("\n", " ")


RENDERINGS = {
    "as-captured": lambda t: t,
    "upper-cased": _upper,
    "lower-cased": _lower,
    "banner on one line": _one_line,
}


@pytest.mark.parametrize("guard_name", sorted(BANNERS))
@pytest.mark.parametrize("rendering", sorted(RENDERINGS))
def test_guard_claims_its_own_banner_under_every_rendering(
    guard_name: str, rendering: str
):
    """A fingerprint identifies a document, not a typesetting of one."""
    text = RENDERINGS[rendering](BANNERS[guard_name])
    assert GUARDS[guard_name](_ctx(text)), (
        f"{guard_name} stopped recognising its own banner when the text "
        f"projection rendered it {rendering}: {text!r}"
    )


@pytest.mark.parametrize("guard_name", sorted(BANNERS))
@pytest.mark.parametrize("rendering", sorted(RENDERINGS))
def test_guard_claims_no_other_layouts_banner(guard_name: str, rendering: str):
    """The tolerance restored above must not let a guard claim a document
    that belongs to another layout — that trades a silent miss for a silent
    mis-extraction, which is worse in a document a valuer signs (#5359).
    """
    for other, banner in BANNERS.items():
        if other == guard_name:
            continue
        text = RENDERINGS[rendering](banner)
        assert not GUARDS[guard_name](_ctx(text)), (
            f"{guard_name} claimed the {other} banner rendered {rendering}"
        )


def test_prose_guard_stays_case_sensitive():
    """`_is_datavardering_prose` keys on the ALL-CAPS `VÄRDEUTLÅTANDE` title
    block. Its pre-refactor pattern was case-sensitive too, so unlike the
    other four this is not a regression — and it must stay that way: the word
    `Värdeutlåtande` appears in nearly every document this extractor sees, so
    matching it case-insensitively would let the prose guard claim the UC
    Bostad reports. This is the one guard the #5371 restoration leaves alone.
    """
    prose = "VÄRDEUTLÅTANDE\nVärderingsobjekt: Hök 1:1"
    assert _is_datavardering_prose(_ctx(prose))
    assert not _is_datavardering_prose(_ctx(prose.lower()))
    assert not _is_datavardering_prose(_ctx(BANNERS["datavardering_uc_br"]))


# ---------- the same two claims, against the real documents ----------


def _golden_stems() -> list[str]:
    return sorted(p.name.replace(".expected.json", "") for p in GOLDEN_DIR.glob("*.expected.json"))


@pytest.mark.parametrize("stem", _golden_stems())
def test_real_sample_stays_recognised_under_a_different_rendering(stem: str):
    """Whatever guard claims a real sample today must still claim it when the
    page-1 text arrives upper-cased or with the banner collapsed onto one
    line. Skips where the sample is absent, like the golden harness (#5369).
    """
    pdf_path = resolve(stem, SAMPLE_DIR, ALIASES)
    if pdf_path is None:
        pytest.skip(f"no sample PDF for {stem} under {SAMPLE_DIR}")

    ctx = build_context(pdf_path.read_bytes())
    claimed = [
        n
        for n, matched in evaluate_content_guards(ctx).items()
        if matched and n in TOLERANT
    ]
    if not claimed:
        pytest.skip(
            f"{stem} is claimed by no tolerance-restored guard; nothing to hold"
        )

    for label, damage in (
        ("upper-cased", _upper),
        ("banner on one line", _one_line),
    ):
        damaged = _ctx(damage(ctx.page1_text), damage(ctx.fitz_full_text))
        still = [n for n, m in evaluate_content_guards(damaged).items() if m]
        assert set(claimed) <= set(still), (
            f"{pdf_path.name}: {sorted(set(claimed) - set(still))} stopped "
            f"recognising the document once its text was {label}"
        )
