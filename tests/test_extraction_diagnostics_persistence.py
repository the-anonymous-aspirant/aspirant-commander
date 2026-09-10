"""The extraction diagnostic must outlive the container (system_3 #5663).

`ExtractionDiagnostics` (#5361) was built so a failed extraction says what it
saw. It reached `logger.warning` and stopped there, so its lifetime was the
commander container's: on 2026-09-10 the 2026-09-08 extraction records were
already unreadable because the container had restarted on 09-09, and the one
surface built to be read after the fact could not be. These tests pin that the
record is persisted, that it is persisted for every outcome and not only the
loud ones, that it expires on a stated window, and that its own failure never
costs the operator their extraction.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import fitz

from app.models import ExtractionDiagnostic
from app.valuation_statement import routes as vs_routes
from app.valuation_statement.extraction import (
    OUTCOME_EXTRACTED,
    OUTCOME_UNRECOGNISED,
    ExtractionDiagnostics,
)


def _pdf(lines: list[str]) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    y = 60
    for line in lines:
        page.insert_text((60, y), line, fontsize=11, fontname="helv")
        y += 18
    return doc.tobytes()


# The 2026-09-06 shape: a layout no fingerprint claims, which is the case the
# whole diagnostic exists for.
UNKNOWN_DOC = _pdf(
    [
        "Fastighetsrapport Plus",
        "Fastighetsbeteckning: KARLSKRONA INGLATORP 1:46",
        "Adress: Linvagen 3",
    ]
)


def _diagnostics(outcome: str, filled: int = 0, sha: str = "a" * 64):
    return ExtractionDiagnostics(
        outcome=outcome,
        content_sha256=sha,
        byte_length=1234,
        page_count=1,
        page1_text_length=90,
        full_text_length=90,
        guards_matched=["fastighet_plus_r"] if filled else [],
        guards_evaluated={"fastighet_plus_r": bool(filled)},
        value_fields_filled=filled,
        value_fields_total=8,
    )


def test_a_failed_extraction_leaves_a_row_not_only_a_log_line(client, db_session):
    """The record survives the process that emitted the warning."""
    response = client.post(
        "/valuation-statement/extract",
        files=[("files", ("FastighetPlus_Karlskrona.pdf", UNKNOWN_DOC, "application/pdf"))],
    )
    assert response.status_code == 200

    rows = db_session.query(ExtractionDiagnostic).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.outcome == OUTCOME_UNRECOGNISED
    assert row.filename == "FastighetPlus_Karlskrona.pdf"
    assert row.value_fields_filled == 0
    assert row.value_fields_total > 0
    assert len(row.content_sha256) == 64


def test_the_row_carries_no_document_text(client, db_session):
    """#5359 item 5: these are real client valuations, so nothing textual lands.

    `Linvagen` and the fastighetsbeteckning are in the PDF and in no column.
    """
    client.post(
        "/valuation-statement/extract",
        files=[("files", ("FastighetPlus_Karlskrona.pdf", UNKNOWN_DOC, "application/pdf"))],
    )

    row = db_session.query(ExtractionDiagnostic).one()
    stored = " ".join(
        str(v)
        for v in (
            row.outcome,
            row.content_sha256,
            row.guards_matched,
            row.guards_evaluated,
        )
    )
    assert "Linvagen" not in stored
    assert "INGLATORP" not in stored
    # The guard names themselves are the point of the column, so it is not
    # empty — an assertion that passed on an empty row would prove nothing.
    assert row.guards_evaluated != {}


def test_a_successful_extraction_is_recorded_too(db_session):
    """Only failures are logged; every outcome is stored.

    A partial extraction is silent today (#5662), and a successful row is what
    makes a failure legible by contrast — the `FastighetPlus_` vs
    `FastighetPlusR_` discriminator was found by comparing rows that worked
    against rows that did not.
    """
    vs_routes._persist_extraction_outcome(
        db_session, "FastighetPlusR_Karlskrona.pdf", _diagnostics(OUTCOME_EXTRACTED, filled=8)
    )

    row = db_session.query(ExtractionDiagnostic).one()
    assert row.outcome == OUTCOME_EXTRACTED
    assert row.value_fields_filled == 8


def test_rows_older_than_the_retention_window_are_swept_and_newer_ones_are_not(db_session):
    """Retention is enforced by something that runs, not by intent."""
    now = datetime.now(timezone.utc)
    stale = ExtractionDiagnostic(
        filename="stale.pdf",
        outcome=OUTCOME_UNRECOGNISED,
        content_sha256="b" * 64,
        byte_length=1,
        page_count=1,
        page1_text_length=0,
        full_text_length=0,
        guards_matched=[],
        guards_evaluated={},
        value_fields_filled=0,
        value_fields_total=8,
        created_at=now - timedelta(days=vs_routes.DIAGNOSTIC_RETENTION_DAYS + 1),
    )
    fresh = ExtractionDiagnostic(
        filename="fresh.pdf",
        outcome=OUTCOME_UNRECOGNISED,
        content_sha256="c" * 64,
        byte_length=1,
        page_count=1,
        page1_text_length=0,
        full_text_length=0,
        guards_matched=[],
        guards_evaluated={},
        value_fields_filled=0,
        value_fields_total=8,
        created_at=now - timedelta(days=vs_routes.DIAGNOSTIC_RETENTION_DAYS - 1),
    )
    db_session.add_all([stale, fresh])
    db_session.commit()

    vs_routes._persist_extraction_outcome(
        db_session, "new.pdf", _diagnostics(OUTCOME_UNRECOGNISED)
    )

    names = {r.filename for r in db_session.query(ExtractionDiagnostic).all()}
    assert names == {"fresh.pdf", "new.pdf"}, names


def test_a_persistence_failure_does_not_cost_the_operator_the_extraction(
    client, db_session, monkeypatch, caplog
):
    """The diagnostic is bookkeeping about the work, never the work itself."""

    def _boom(*_args, **_kwargs):
        raise RuntimeError("diagnostics table is gone")

    monkeypatch.setattr(db_session, "add", _boom)

    with caplog.at_level("ERROR"):
        response = client.post(
            "/valuation-statement/extract",
            files=[("files", ("FastighetPlus_Karlskrona.pdf", UNKNOWN_DOC, "application/pdf"))],
        )

    assert response.status_code == 200
    assert response.json()["documents"][0]["fields"]
    # Swallowed, but never silently: the failure is in the log with the file.
    assert "failed to persist extraction diagnostic" in caplog.text
    assert "FastighetPlus_Karlskrona.pdf" in caplog.text
