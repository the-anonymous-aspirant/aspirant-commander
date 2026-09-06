"""Resolution rules for golden sample PDFs, tested without any real PDF.

Every case here is a shape that was observed in the sample store on
the aspirant cell (#5369), so these are regressions against real
filenames rather than invented ones. The bytes are irrelevant to
resolution, so the fixtures are empty files with the right names.
"""

from __future__ import annotations

import json
import unicodedata

import pytest

from tests.sample_resolver import (
    AmbiguousSample,
    describe_attempt,
    fold,
    load_aliases,
    resolve,
    sample_pdfs,
)


# The two constants below render identically in every editor and differ
# only in normalisation. If a tool ever normalises this file they
# collapse into one string and `test_fold_makes_nfc_and_nfd_agree`
# fails on its first assertion — loudly, which is the point.
NFC_A_DIAERESIS = "ä"  # ä as one code point
NFD_A_DIAERESIS = "ä"  # a + combining diaeresis


def _touch(directory, *names):
    for name in names:
        (directory / name).write_bytes(b"%PDF-1.4\n")
    return directory


def test_fold_makes_nfc_and_nfd_agree():
    nfc = f"Datav{NFC_A_DIAERESIS}rdering.pdf"
    nfd = f"Datav{NFD_A_DIAERESIS}rdering.pdf"
    assert nfc != nfd  # the whole point: they are different strings
    assert unicodedata.normalize("NFC", nfd) == nfc
    assert fold(nfc) == fold(nfd) == "datavardering.pdf"


def test_fold_treats_underscore_hyphen_and_space_alike():
    assert fold("LGH_utdrag") == fold("LGH utdrag") == fold("LGH-utdrag")


def test_exact_filename_still_wins(tmp_path):
    _touch(tmp_path, "Min_bostad.pdf", "Min bostad.pdf")
    # An exactly-named file is taken without consulting anything else,
    # so a directory prepared the old way keeps working unchanged.
    assert resolve("Min_bostad", tmp_path, {}) == tmp_path / "Min_bostad.pdf"


def test_resolves_an_nfd_filename_the_stem_cannot_spell(tmp_path):
    stored = f"V{NFD_A_DIAERESIS}rdeutlåtandeBR.pdf"
    _touch(tmp_path, stored)
    assert resolve("VardeutlatandeBR", tmp_path, {}) == tmp_path / stored


def test_resolves_a_real_download_name_by_prefix(tmp_path):
    stored = "FastighetPlusR_Bengtsfors_NARSIDAN_1-21_20260618085233.pdf"
    _touch(tmp_path, stored)
    assert resolve("FastighetPlusR_Bengtsfors", tmp_path, {}) == tmp_path / stored


def test_resolves_a_case_shifted_download_name(tmp_path):
    stored = "UCB_BENGTSFORS_NARSIDAN_1-21_20260618085224.pdf"
    _touch(tmp_path, stored)
    assert resolve("UCB_Bengtsfors", tmp_path, {}) == tmp_path / stored


def test_underscore_stem_matches_a_spaced_filename(tmp_path):
    _touch(tmp_path, "LGH utdrag.pdf")
    assert resolve("LGH_utdrag", tmp_path, {}) == tmp_path / "LGH utdrag.pdf"


def test_alias_resolves_a_stem_that_shares_a_prefix_with_a_sibling(tmp_path):
    """`Datavardering_2` is the post-2026 6-column UC BR layout, stored as
    `Datavärdering (1).pdf`. Nothing in the stem points at that name, and a
    bare prefix match on `Datavardering` would collide with the sibling.
    """
    first = f"Datav{NFC_A_DIAERESIS}rdering.pdf"
    second = f"Datav{NFD_A_DIAERESIS}rdering (1).pdf"
    _touch(tmp_path, first, second)
    aliases = {"Datavardering_2": [f"Datav{NFC_A_DIAERESIS}rdering (1)"]}

    assert resolve("Datavardering", tmp_path, aliases) == tmp_path / first
    assert resolve("Datavardering_2", tmp_path, aliases) == tmp_path / second


def test_ambiguity_raises_rather_than_guessing(tmp_path):
    """Two candidates must not silently become one.

    Picking either would compare a real document against another
    document's golden and report a strategy regression that is not
    there — worse than not running.
    """
    _touch(
        tmp_path,
        "UCB_Katrineholm_JULITA_1-137_20260623110327.pdf",
        "UCB_Katrineholm_JULITA_1-137_20260624090000.pdf",
    )
    with pytest.raises(AmbiguousSample) as excinfo:
        resolve("UCB_Katrineholm", tmp_path, {})
    assert "matches 2 files" in str(excinfo.value)


def test_unmatched_stem_returns_none(tmp_path):
    _touch(tmp_path, "something_else.pdf")
    assert resolve("UCB_Bengtsfors", tmp_path, {}) is None


def test_missing_directory_resolves_to_none_without_raising(tmp_path):
    assert resolve("UCB_Bengtsfors", tmp_path / "absent", {}) is None
    assert sample_pdfs(tmp_path / "absent") == []


def test_sample_pdfs_ignores_non_pdfs(tmp_path):
    _touch(tmp_path, "UCB_Bengtsfors.pdf")
    (tmp_path / "Värdeutlåtande mall.docx").write_bytes(b"PK\x03\x04")
    assert [p.name for p in sample_pdfs(tmp_path)] == ["UCB_Bengtsfors.pdf"]


def test_skip_message_names_the_directory_and_what_was_tried(tmp_path):
    _touch(tmp_path, "unrelated.pdf")
    message = describe_attempt("UCB_Bengtsfors", tmp_path, {})
    assert str(tmp_path) in message
    assert "UCB_Bengtsfors.pdf" in message
    assert "unrelated.pdf" in message


def test_alias_file_ignores_comment_keys(tmp_path):
    (tmp_path / "sample_index.json").write_text(
        json.dumps({"_comment": ["ignore me"], "Datavardering_2": ["Datav (1)"]}),
        encoding="utf-8",
    )
    assert load_aliases(tmp_path) == {"Datavardering_2": ["Datav (1)"]}


def test_absent_alias_file_is_not_an_error(tmp_path):
    assert load_aliases(tmp_path) == {}
