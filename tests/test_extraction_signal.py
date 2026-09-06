"""A total extraction miss must be loud (system_3 #5359).

Both 2026-09-06 uploads returned HTTP 200 with an empty field set. Nothing in
the response, the logs or the wizard distinguished that from a working upload;
it surfaced two days later when someone read `processed_valuations`. These
tests pin the three things that would have surfaced it, and the constraint that
the diagnostic record carries no document content — these are real client
valuation documents.
"""

from __future__ import annotations

import json
import logging

import fitz
import pytest

from app.valuation_statement._context import build_context
from app.valuation_statement.extraction import (
    OUTCOME_EXTRACTED,
    OUTCOME_RECOGNISED_NO_FIELDS,
    OUTCOME_UNRECOGNISED,
    ExtractedField,
    ExtractionResult,
    extract_document,
)
from app.valuation_statement.field_extractor import (
    CONTENT_GUARDS,
    _build_diagnostics,
    evaluate_content_guards,
)


def _pdf(lines: list[str]) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    y = 60
    for line in lines:
        page.insert_text((60, y), line, fontsize=11, fontname="helv")
        y += 18
    return doc.tobytes()


# A layout no fingerprint claims. The lines are real words so that a failure to
# read text at all is distinguishable from a failure to recognise the layout.
UNKNOWN_DOC = _pdf(
    [
        "Fastighetsrapport Plus",
        "Fastighetsbeteckning: KARLSKRONA INGLATORP 1:46",
        "Adress: Linvagen 3",
    ]
)

# The same document one letter longer — the tier the strategy library covers.
KNOWN_DOC = _pdf(
    [
        "Fastighetsrapport Plus R",
        "Fastighetsbeteckning: KARLSKRONA INGLATORP 1:46",
        "Adress: Linvagen 3",
    ]
)


def test_unrecognised_document_says_so_rather_than_returning_a_silent_blank():
    result = extract_document(UNKNOWN_DOC, "FastighetPlus_Karlskrona.pdf")

    assert result.diagnostics is not None
    assert result.diagnostics.outcome == OUTCOME_UNRECOGNISED
    assert result.diagnostics.guards_matched == []
    assert result.diagnostics.value_fields_filled == 0
    # Every value slot still lands as not_found — the outcome is the new signal,
    # not a change to how misses are represented.
    assert all(f.confidence == "not_found" for f in result.fields)


def test_the_text_was_readable_so_the_miss_is_about_recognition():
    """Positive control for the test above.

    An unrecognised document and a document whose text could not be read look
    identical in the outcome, and they need different fixes. This pins that the
    bytes above really do carry extractable text.
    """
    ctx = build_context(UNKNOWN_DOC)
    assert "Fastighetsrapport Plus" in ctx.page1_text
    assert len(ctx.page1_text) > 40


def test_a_covered_layout_still_extracts():
    result = extract_document(KNOWN_DOC, "FastighetPlusR_Karlskrona.pdf")

    assert result.diagnostics is not None
    assert result.diagnostics.outcome == OUTCOME_EXTRACTED
    assert "fastighetsrapport" in result.diagnostics.guards_matched
    assert result.diagnostics.value_fields_filled > 0


def test_recognised_but_empty_is_a_third_outcome_not_folded_into_the_other_two():
    """A matched guard with no values is a strategy bug, not a coverage gap.

    They need opposite fixes — add a layout, or repair a chain — so the record
    keeps them apart even though the user sees the same blank form.
    """
    ctx = build_context(KNOWN_DOC)
    all_missed = ExtractionResult(
        filename="x.pdf",
        fields=[
            ExtractedField(key="adress", value=None, confidence="not_found", source_filename="x.pdf"),
            ExtractedField(key="source_class", value="fastighetsutdrag", confidence="confident", source_filename="x.pdf"),
        ],
    )
    diagnostics = _build_diagnostics(ctx, KNOWN_DOC, all_missed)

    assert diagnostics.outcome == OUTCOME_RECOGNISED_NO_FIELDS
    # source_class filled but it is a semantic primitive: the operator still
    # types every field, so it does not count as "something was extracted".
    assert diagnostics.value_fields_filled == 0


def test_diagnostics_carry_no_document_content():
    result = extract_document(UNKNOWN_DOC, "FastighetPlus_Karlskrona.pdf")
    payload = json.dumps(result.diagnostics.__dict__, ensure_ascii=False)

    for secret in ("KARLSKRONA", "INGLATORP", "Linvagen", "Fastighetsbeteckning"):
        assert secret not in payload, f"{secret} leaked into the diagnostic record"
    # Positive control: those words ARE in the document, so the assertions above
    # are about the record rather than about the document being empty.
    assert "KARLSKRONA" in build_context(UNKNOWN_DOC).page1_text


def test_guard_registry_covers_every_fingerprint_the_slots_consult():
    """A guard missing from the registry is invisible to the signal.

    The registry is what the diagnostic reports; if a new layout's fingerprint
    is added to the strategies but not here, a run that DID recognise the
    document would be reported as `unrecognised`.
    """
    import app.valuation_statement.field_extractor as fx

    registered = {name for name, _ in CONTENT_GUARDS}
    defined = {
        name[len("_is_") :]
        for name in dir(fx)
        if name.startswith("_is_") and callable(getattr(fx, name))
    }
    # `datavardering_uc` is the union of the two it delegates to, both of which
    # are registered; reporting it as well would double-count.
    assert defined - registered == {"datavardering_uc"}


def test_a_total_miss_is_logged_with_the_guard_outcomes(caplog):
    from fastapi.testclient import TestClient

    from app.main import app

    with caplog.at_level(logging.WARNING, logger="app.valuation_statement.routes"):
        with TestClient(app) as client:
            response = client.post(
                "/valuation-statement/extract",
                files={"files": ("FastighetPlus_Karlskrona.pdf", UNKNOWN_DOC, "application/pdf")},
            )

    assert response.status_code == 200
    document = response.json()["documents"][0]
    assert document["outcome"] == OUTCOME_UNRECOGNISED
    assert document["diagnostics"]["guards_matched"] == []

    # Filter by logger, not just level: the app's own lifespan warning fires in
    # the same window, and asserting on records[0] would report "no log line"
    # when the truth is "a different line".
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and r.name == "app.valuation_statement.routes"
    ]
    assert warnings, "an extraction that recognised nothing left no log line"
    logged = warnings[0].getMessage()
    assert "unrecognised" in logged
    assert "guards_evaluated" in logged
    assert "KARLSKRONA" not in logged

    # The line is structured so it can be read back, not just eyeballed.
    payload = json.loads(logged.split(": ", 1)[1])
    assert payload["guards_evaluated"] == {name: False for name, _ in CONTENT_GUARDS}
    assert payload["content_sha256"]
