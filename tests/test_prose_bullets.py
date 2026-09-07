"""Prose-appraisal bullet extraction: a blank bullet must not steal the row below.

Fastighetsbyrån prose appraisals list the Värderingsobjekt fields as a bullet
column:

    ● Objekt: Karlskrona Inglatorp 1:46
    ● Adress:
    ● Kommun:
    ● Upplåtelseform: Friköpt

When the valuer leaves a bullet blank (`● Adress:` with nothing after the
colon), the value for that slot is genuinely absent and must land as
`not_found` so the operator types it. The regression this guards against
(#5363): `_bullet_value`'s post-colon whitespace was `\\s*`, which matches a
newline, so an empty bullet captured the *next* bullet's label line — filling
`adress` with `● Kommun:` and `kommun` with `● Upplåtelseform: Friköpt` in a
document a valuer signs.

No PDF needed: the strategies read `ctx.page1_text`, so a hand-built
ParseContext exercises them directly.
"""

from __future__ import annotations

from app.valuation_statement._context import ParseContext
from app.valuation_statement.field_extractor import (
    _adress_prose_bullet,
    _bullet_value,
    _kommun_prose_bullet,
    _objekt_prose_bullet,
)


def _ctx(page1: str) -> ParseContext:
    return ParseContext(
        page1_text=page1,
        page1_words=(),
        page_texts=(page1,),
        fitz_full_text=page1,
    )


# The Karlskrona sample's page-1 banner + Värderingsobjekt block, with Adress
# and Kommun blank. `VÄRDEUTLÅTANDE` + `Värderingsobjekt` is what
# `_is_datavardering_prose` keys on, so the prose strategies fire.
_BLANK_BULLETS = _ctx(
    "VÄRDEUTLÅTANDE\n"
    "Värderingsobjekt\n"
    "● Objekt: Karlskrona Inglatorp 1:46\n"
    "● Adress:\n"
    "● Kommun:\n"
    "● Upplåtelseform: Friköpt\n"
)

# A prose object whose Adress/Kommun bullets DO carry values (the shape the
# existing VardeutlatandeBR / VardeutlatandeHok goldens have): the positive
# control that proves the fix did not simply stop the strategies working.
_FILLED_BULLETS = _ctx(
    "VÄRDEUTLÅTANDE\n"
    "Värderingsobjekt\n"
    "● Objekt: Vaggeryd Hok 2:139\n"
    "● Adress: Lillholmsvägen 12\n"
    "● Kommun: Hok\n"
    "● Upplåtelseform: Friköpt\n"
)


class TestBlankBulletDoesNotStealNextLine:
    def test_blank_adress_is_not_found(self):
        assert _adress_prose_bullet(_BLANK_BULLETS) is None

    def test_blank_kommun_is_not_found(self):
        assert _kommun_prose_bullet(_BLANK_BULLETS) is None

    def test_objekt_still_reads_its_own_line(self):
        assert _objekt_prose_bullet(_BLANK_BULLETS) == "Karlskrona Inglatorp 1:46"

    def test_bullet_value_blank_returns_none_directly(self):
        # The helper, exercised on its own: a label with an empty value line
        # must return None rather than the following line's text.
        assert _bullet_value(_BLANK_BULLETS.page1_text, "Adress") is None
        assert _bullet_value(_BLANK_BULLETS.page1_text, "Kommun") is None


class TestFilledBulletsStillExtract:
    def test_adress_reads_its_value(self):
        assert _adress_prose_bullet(_FILLED_BULLETS) == "Lillholmsvägen 12"

    def test_kommun_reads_its_value(self):
        assert _kommun_prose_bullet(_FILLED_BULLETS) == "Hok"

    def test_objekt_reads_its_value(self):
        assert _objekt_prose_bullet(_FILLED_BULLETS) == "Vaggeryd Hok 2:139"
