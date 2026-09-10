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

from app.valuation_statement._context import ParseContext, build_context
from app.valuation_statement.extraction import (
    OUTCOME_EXTRACTED,
    OUTCOME_NO_TEXT,
    OUTCOME_PARTIAL,
    OUTCOME_RECOGNISED_NO_FIELDS,
    OUTCOME_UNRECOGNISED,
    ExtractedField,
    ExtractionDiagnostics,
    ExtractionResult,
    extract_document,
)
from app.valuation_statement.field_extractor import (
    CONTENT_GUARDS,
    EXPECTED_SLOTS_BY_SHAPE,
    _build_diagnostics,
    _CORE_EXPECTED_SLOTS,
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


def _scanned_pdf() -> bytes:
    """One page, one raster image, zero text objects — a print-and-scan.

    Built rather than checked in: it needs no client document, and building it
    here is what makes the "no text" property true by construction instead of
    true by assertion about an opaque fixture.
    """
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    pix = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 1200, 1700), False)
    pix.clear_with(220)
    page.insert_image(fitz.Rect(0, 0, 595, 842), pixmap=pix)
    return doc.tobytes()


SCANNED_DOC = _scanned_pdf()


def test_a_document_with_no_text_is_not_reported_as_an_uncovered_layout():
    """A scan and an unsupported layout need opposite fixes.

    `unrecognised` reads "a layout the strategy library does not cover", and
    both the log record and the wizard's advice act on it — add a fingerprint,
    tell the user to check the report type. Neither helps a document that
    carries no text at all, where no fingerprint can ever fire and the remedy
    is a different export of the same file.
    """
    result = extract_document(SCANNED_DOC, "scanned.pdf")

    assert result.diagnostics is not None
    assert result.diagnostics.outcome == OUTCOME_NO_TEXT
    assert result.diagnostics.guards_matched == []
    assert result.diagnostics.value_fields_filled == 0
    assert result.diagnostics.page_count == 1


def test_the_scanned_fixture_really_is_a_page_with_no_text():
    """Positive control for the test above.

    A fixture that failed to build — an empty document, a zero-page PDF —
    would satisfy the `no_text` assertion for the wrong reason. This pins that
    there is a page, that it carries image bytes, and that neither projection
    reads a character out of it.
    """
    ctx = build_context(SCANNED_DOC)

    assert ctx.page_count == 1
    assert ctx.full_text.strip() == ""
    assert ctx.fitz_full_text.strip() == ""
    # It is a page with content, not an empty one: a raster image is what makes
    # this a scan rather than a blank sheet.
    assert len(SCANNED_DOC) > 5000


def test_a_text_bearing_unknown_layout_is_still_unrecognised():
    """The new arm must not swallow the old one.

    If `no_text` were keyed on anything looser than "no text at all" it would
    absorb the genuine coverage gaps this epic exists to find, and #5363 would
    stop being visible in the record.
    """
    result = extract_document(UNKNOWN_DOC, "FastighetPlus_Karlskrona.pdf")

    assert result.diagnostics.outcome == OUTCOME_UNRECOGNISED
    assert result.diagnostics.full_text_length > 0


def test_one_readable_projection_is_enough_to_rule_out_no_text():
    """pdfplumber alone must not decide it.

    The two projections disagree by design (HSB's CMap), so a document
    pdfplumber renders as empty may still be readable by PyMuPDF — and a guard
    reading that projection could still match. Reporting `no_text` off the
    pdfplumber view alone would mislabel exactly those documents.
    """
    readable_by_fitz_only = ParseContext(
        page1_text="",
        page1_words=(),
        page_texts=("",),
        fitz_full_text="Lagenhetsuppgifter",
    )
    all_missed = ExtractionResult(filename="x.pdf")

    diagnostics = _build_diagnostics(readable_by_fitz_only, b"%PDF-x", all_missed)

    assert diagnostics.outcome == OUTCOME_UNRECOGNISED


def test_a_covered_layout_that_fills_only_some_expected_slots_is_partial():
    """A recognised layout that fills part of what its shape expects is partial.

    `KNOWN_DOC` is a minimal Fastighetsrapport fixture: the guard matches and a
    value slot fills (`upplatelseform`), but the geometry-dependent `objekt`
    and `document_date` cells are not present, so those expected slots miss.
    Before #5662 that read as `extracted` — filling any one slot was success —
    and the operator retyping `objekt` was invisible. It is now its own outcome
    that names which expected slots missed, while the covered-layout guarantees
    (a guard matched, at least one value slot filled) still hold.
    """
    result = extract_document(KNOWN_DOC, "FastighetPlusR_Karlskrona.pdf")

    assert result.diagnostics is not None
    assert result.diagnostics.outcome == OUTCOME_PARTIAL
    assert "fastighetsrapport" in result.diagnostics.guards_matched
    assert result.diagnostics.value_fields_filled > 0
    assert "objekt" in result.diagnostics.missed_expected_slots


def test_a_document_that_fills_every_expected_slot_is_extracted_not_partial():
    """`extracted` now means complete: every expected slot for the shape filled.

    Built as a result rather than a synthetic PDF so the assertion is about the
    outcome rule, not about reproducing a real layout's geometry. A bostadsrätt
    that fills all four core slots (and nothing is expected beyond them) is a
    clean success with no missed slots and no warning.
    """
    ctx = build_context(KNOWN_DOC)
    complete = ExtractionResult(
        filename="x.pdf",
        fields=[
            ExtractedField(key="objekt", value="LGH 2 HSB Brf Furulund (7...)", confidence="confident", source_filename="x.pdf"),
            ExtractedField(key="objekt_short", value="LGH 2 HSB Brf Furulund", confidence="confident", source_filename="x.pdf"),
            ExtractedField(key="upplatelseform", value="Bostadsrätt", confidence="confident", source_filename="x.pdf"),
            ExtractedField(key="document_date", value="2026-09-08", confidence="confident", source_filename="x.pdf"),
            ExtractedField(key="property_shape", value="bostadsratt", confidence="confident", source_filename="x.pdf"),
        ],
    )

    diagnostics = _build_diagnostics(ctx, KNOWN_DOC, complete)

    assert diagnostics.outcome == OUTCOME_EXTRACTED
    assert diagnostics.missed_expected_slots == []
    assert diagnostics.value_fields_filled == 4


def test_a_recognised_document_that_misses_an_expected_slot_is_partial():
    """The partial branch keyed off the shape's expected set, not the raw total.

    A bostadsrätt that filled `adress`/`upplatelseform`/`document_date` but not
    `objekt`/`objekt_short` is partial and names exactly those two — the shape
    of the 2026-09-08 Furulund miss this task was filed on.
    """
    ctx = build_context(KNOWN_DOC)
    partial = ExtractionResult(
        filename="x.pdf",
        fields=[
            ExtractedField(key="adress", value="Furuvägen 2", confidence="confident", source_filename="x.pdf"),
            ExtractedField(key="upplatelseform", value="Bostadsrätt", confidence="confident", source_filename="x.pdf"),
            ExtractedField(key="document_date", value="2026-09-08", confidence="confident", source_filename="x.pdf"),
            ExtractedField(key="objekt", value=None, confidence="not_found", source_filename="x.pdf"),
            ExtractedField(key="objekt_short", value=None, confidence="not_found", source_filename="x.pdf"),
            ExtractedField(key="property_shape", value="bostadsratt", confidence="confident", source_filename="x.pdf"),
        ],
    )

    diagnostics = _build_diagnostics(ctx, KNOWN_DOC, partial)

    assert diagnostics.outcome == OUTCOME_PARTIAL
    assert diagnostics.missed_expected_slots == ["objekt", "objekt_short"]
    assert diagnostics.value_fields_filled == 3


def test_missing_only_a_document_type_optional_slot_is_not_partial():
    """A warning that fires on every document is the same as no warning.

    Registry documents (lägenhetsförteckning, fastighetsutdrag) legitimately
    carry no market valuation, so a missing `marknadsvarde_kr` / `intervall_kr`
    is not a partial miss. Only the shape's expected slots gate the outcome; the
    optional document-type slots do not, which is what keeps the signal from
    firing on the LGH_utdrag / FastighetPlusR class of golden documents.
    """
    ctx = build_context(KNOWN_DOC)
    core_only = ExtractionResult(
        filename="x.pdf",
        fields=[
            ExtractedField(key="objekt", value="Bengtsfors Närsidan 1:21", confidence="confident", source_filename="x.pdf"),
            ExtractedField(key="objekt_short", value="Bengtsfors Närsidan 1:21", confidence="confident", source_filename="x.pdf"),
            ExtractedField(key="upplatelseform", value="Friköpt", confidence="confident", source_filename="x.pdf"),
            ExtractedField(key="document_date", value="2026-09-08", confidence="confident", source_filename="x.pdf"),
            ExtractedField(key="marknadsvarde_kr", value=None, confidence="not_found", source_filename="x.pdf"),
            ExtractedField(key="intervall_kr", value=None, confidence="not_found", source_filename="x.pdf"),
            ExtractedField(key="property_shape", value="fastighet", confidence="confident", source_filename="x.pdf"),
        ],
    )

    diagnostics = _build_diagnostics(ctx, KNOWN_DOC, core_only)

    assert diagnostics.outcome == OUTCOME_EXTRACTED
    assert diagnostics.missed_expected_slots == []


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


def _partial_diagnostics(missed):
    """A content-free `partial` diagnostic record for the log-shape tests."""
    return ExtractionDiagnostics(
        outcome=OUTCOME_PARTIAL,
        content_sha256="a" * 64,
        byte_length=4096,
        page_count=1,
        page1_text_length=200,
        full_text_length=200,
        guards_matched=["lgh_utdrag"],
        guards_evaluated={"lgh_utdrag": True},
        value_fields_filled=6,
        value_fields_total=8,
        missed_expected_slots=list(missed),
    )


def test_a_partial_extraction_leaves_its_own_greppable_warning(caplog):
    """A partial miss is loud, and greppable APART from the zero-field line.

    The two failures need different reading — a total miss is a coverage or
    recognition gap, a partial miss is a slot the operator retyped on an
    otherwise working document — so the message strings differ and the partial
    line names which expected slots missed.
    """
    from app.valuation_statement.routes import _log_extraction_outcome

    with caplog.at_level(logging.WARNING, logger="app.valuation_statement.routes"):
        _log_extraction_outcome(
            "Furulund.pdf", _partial_diagnostics(["objekt", "objekt_short"])
        )

    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and r.name == "app.valuation_statement.routes"
    ]
    assert len(warnings) == 1, "a partial extraction left no log line"
    logged = warnings[0].getMessage()
    # Distinct message string: greppable apart from the total-miss warning.
    assert "filled only some expected slots" in logged
    assert "produced no fields" not in logged

    payload = json.loads(logged.split(": ", 1)[1])
    assert payload["outcome"] == OUTCOME_PARTIAL
    assert payload["missed_expected_slots"] == ["objekt", "objekt_short"]
    assert payload["value_fields_filled"] == 6
    assert payload["value_fields_total"] == 8


def test_a_complete_extraction_leaves_no_warning(caplog):
    """The other half of the signal: a document that filled everything expected
    is silent — otherwise the warning is noise on every upload."""
    from app.valuation_statement.routes import _log_extraction_outcome

    diagnostics = _partial_diagnostics([])
    object.__setattr__(diagnostics, "outcome", OUTCOME_EXTRACTED)

    with caplog.at_level(logging.WARNING, logger="app.valuation_statement.routes"):
        _log_extraction_outcome("complete.pdf", diagnostics)

    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and r.name == "app.valuation_statement.routes"
    ]
    assert warnings == []


def test_the_partial_warning_carries_no_document_content(caplog):
    """Slot KEYS are schema identifiers, but the record must still hold no text.

    The partial line names the missed slots by key; it must not carry a value
    the operator would have typed. This pins that only keys/counts/hash reach
    the log — the same no-content rule as the total-miss line (#5359 item 5).
    """
    from app.valuation_statement.routes import _log_extraction_outcome

    with caplog.at_level(logging.WARNING, logger="app.valuation_statement.routes"):
        _log_extraction_outcome(
            "Furulund.pdf", _partial_diagnostics(["objekt", "marknadsvarde_kr"])
        )

    logged = caplog.records[-1].getMessage()
    payload = json.loads(logged.split(": ", 1)[1])
    # Only these keys — no value/text field can ride along.
    assert set(payload) == {
        "filename",
        "outcome",
        "content_sha256",
        "missed_expected_slots",
        "value_fields_filled",
        "value_fields_total",
        "guards_matched",
    }


def test_the_expected_slot_set_is_filled_by_every_golden_fixture():
    """The expected set must be a subset of what real handled documents fill.

    This is the guard on the design decision itself: if a slot is ever added to
    EXPECTED_SLOTS_BY_SHAPE that some legitimately-handled document does not
    carry, the partial warning starts firing on that document and becomes noise.
    Pinning it against the golden corpus makes that regression a red test rather
    than a silent flood of warnings in production.
    """
    import glob
    import os

    golden = glob.glob(
        os.path.join(os.path.dirname(__file__), "fixtures", "golden", "*.expected.json")
    )
    assert golden, "no golden fixtures found — the corpus check would be vacuous"

    checked = 0
    for path in golden:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        fields = data.get("fields", data)
        if not isinstance(fields, dict) or "property_shape" not in fields:
            continue
        shape = fields["property_shape"]
        if isinstance(shape, dict):
            shape = shape.get("value")
        expected = EXPECTED_SLOTS_BY_SHAPE.get(shape, frozenset())
        for slot in expected:
            value = fields.get(slot)
            if isinstance(value, dict):
                value = value.get("value")
            assert value, (
                f"{os.path.basename(path)} ({shape}): expected slot {slot!r} is "
                f"not filled in the golden output — it must not be in the "
                f"expected set, or the partial warning will fire on it"
            )
        checked += 1
    assert checked, "no shape-bearing golden fixture exercised the expected set"


def test_an_unknown_shape_falls_back_to_the_core_expected_set():
    """A document whose shape the classifier missed still gets the core check.

    The fallback is the core set every golden fixture fills regardless of shape,
    so a run that filled a value slot yet missed `objekt` is still caught even
    when `property_shape` did not classify.
    """
    ctx = build_context(KNOWN_DOC)
    result = ExtractionResult(
        filename="x.pdf",
        fields=[
            ExtractedField(key="adress", value="Furuvägen 2", confidence="confident", source_filename="x.pdf"),
            ExtractedField(key="objekt", value=None, confidence="not_found", source_filename="x.pdf"),
        ],
    )

    diagnostics = _build_diagnostics(ctx, KNOWN_DOC, result)

    assert diagnostics.outcome == OUTCOME_PARTIAL
    assert set(diagnostics.missed_expected_slots) == set(_CORE_EXPECTED_SLOTS) - {"adress"}
