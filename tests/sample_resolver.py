"""Locate the real sample PDF that backs each golden fixture.

Split out of `test_golden_pipeline.py` so the resolution rules can be
tested on their own, without any real (client-confidential) PDF.

Why this exists rather than `SAMPLE_DIR / f"{stem}.pdf"`:

  * The samples arrive with their download names — a Lantmäteriet
    report lands as `FastighetPlusR_Bengtsfors_NARSIDAN_1-21_2026…pdf`,
    not as `FastighetPlusR_Bengtsfors.pdf`. Requiring a rename means
    the harness only ever runs for whoever did the renaming.
  * Swedish filenames are not consistently normalised. In the store on
    the aspirant cell, `Datavärdering.pdf` is NFC (`ä` = U+00E4) while
    `Datavärdering (1).pdf` and `VärdeutlåtandeBR.pdf` are NFD (`a` +
    U+0308). The two forms are visually identical and byte-different,
    so a hand-typed name silently fails to open — which reads exactly
    like an absent file.

Both of those failures are silent: the fixture skips, and a skip in a
suite of 160 is invisible. They are why every golden fixture skipped
unnoticed until #5369.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path


# Golden stems whose sample cannot be derived from the stem itself.
# Keyed by golden stem, valued by the folded prefix(es) to try.
ALIASES_FILE = "sample_index.json"


def fold(name: str) -> str:
    """Reduce a filename to a comparison key.

    Strips diacritics (so NFC and NFD forms agree), lowercases, and
    treats `_`, `-` and runs of whitespace as the same separator (so
    the golden stem `LGH_utdrag` matches the stored `LGH utdrag.pdf`).
    """
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[\s_-]+", " ", stripped).strip().lower()


def load_aliases(golden_dir: Path) -> dict[str, list[str]]:
    """Read the optional stem -> download-name-prefix index."""
    path = golden_dir / ALIASES_FILE
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def sample_pdfs(sample_dir: Path) -> list[Path]:
    """Every PDF in the sample directory, deterministically ordered."""
    if not sample_dir.is_dir():
        return []
    return sorted(p for p in sample_dir.iterdir() if p.suffix.lower() == ".pdf")


class AmbiguousSample(LookupError):
    """More than one PDF answers to a golden stem — refuse to guess."""


def resolve(stem: str, sample_dir: Path, aliases: dict[str, list[str]]) -> Path | None:
    """Return the PDF backing `stem`, or None if the directory holds none.

    Resolution is deliberately ordered from most to least literal, and
    an ambiguous match raises rather than picking one: choosing the
    wrong sample would compare a real document against the wrong
    golden and report a strategy regression that is not there.
    """
    exact = sample_dir / f"{stem}.pdf"
    if exact.exists():
        return exact

    pdfs = sample_pdfs(sample_dir)
    wanted = fold(f"{stem}.pdf")

    same_name = [p for p in pdfs if fold(p.name) == wanted]
    if len(same_name) == 1:
        return same_name[0]
    if len(same_name) > 1:
        raise AmbiguousSample(
            f"{stem}: {len(same_name)} files share this name once accents are "
            f"folded: {[p.name for p in same_name]}"
        )

    for pattern in aliases.get(stem, [stem]):
        folded = fold(pattern)
        hits = [p for p in pdfs if fold(p.name).startswith(folded)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise AmbiguousSample(
                f"{stem}: prefix {pattern!r} matches {len(hits)} files: "
                f"{[p.name for p in hits]}. Add a longer prefix for this stem "
                f"to tests/fixtures/golden/{ALIASES_FILE}."
            )

    return None


def describe_attempt(stem: str, sample_dir: Path, aliases: dict[str, list[str]]) -> str:
    """The skip message: what was looked for, and where."""
    patterns = aliases.get(stem, [stem])
    present = [p.name for p in sample_pdfs(sample_dir)]
    return (
        f"No sample PDF for golden {stem!r} in {sample_dir}. "
        f"Tried: exact {stem}.pdf, accent-folded {stem}.pdf, and prefix(es) "
        f"{patterns}. Directory holds {len(present)} PDF(s): {present[:12]}"
    )
