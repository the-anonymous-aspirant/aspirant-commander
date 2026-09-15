import json
import logging
import os
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ExtractionDiagnostic, ProcessedValuation
from app.valuation_statement.processed import require_caller_id

from app.valuation_statement.api_schemas import (
    ComparableSale,
    DecideResponse,
    DecideResult,
    ExtractedFieldOut,
    ExtractionDiagnosticsOut,
    ExtractResponse,
    ExtractionResultOut,
    GenerateRequest,
    OperatorDefaults,
)
from app.valuation_statement.extraction import (
    OUTCOME_EXTRACTED,
    OUTCOME_PARTIAL,
    decide_extraction,
    extract_document,
)
from app.valuation_statement.pdf_export import (
    LibreOfficeConversionFailed,
    LibreOfficeUnavailable,
    docx_to_pdf,
)
from app.valuation_statement.template import TemplateFields, populate


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/valuation-statement", tags=["valuation-statement"])


MAX_PDF_BYTES = 25 * 1024 * 1024  # 25 MB per file

# How long a persisted extraction diagnostic is kept. These rows are exhaust,
# not a record of value, so they expire rather than accumulate. 90 days is
# chosen against the observed upload rate, not a guess: `processed_valuations`
# on the live database held 12 valuations over the 10 days to 2026-09-10, i.e.
# a few documents a day, so a 90-day window is a few hundred content-free rows
# and cheap to keep. The window is what makes an incident diagnosable weeks
# later — #5359 itself spanned four days between the failure and the read.
DIAGNOSTIC_RETENTION_DAYS = 90


def _log_extraction_outcome(filename: str, diagnostics) -> None:
    """Say it out loud when a document yielded nothing — or only part.

    Both #5359 uploads returned 200 with an empty field set and nobody knew
    until the database was read two days later. An extraction that
    recognises nothing is not a normal outcome, so it leaves a WARNING
    naming the file, the hash, and which guards evaluated True — no
    document text, since these are real client valuations.

    A *partial* extraction is the same failure at smaller scale (#5662): a
    recognised document filled some slots and missed one it was expected to,
    the operator retypes the missed slot, and the run reads as success. It
    leaves its OWN warning — a distinct message string so the two are
    greppable apart — naming which expected slots missed (slot keys only, the
    same no-document-content rule as the total-miss line).
    """
    if diagnostics is None or diagnostics.outcome == OUTCOME_EXTRACTED:
        return
    if diagnostics.outcome == OUTCOME_PARTIAL:
        logger.warning(
            "valuation extraction filled only some expected slots: %s",
            json.dumps(
                {
                    "filename": filename,
                    "outcome": diagnostics.outcome,
                    "content_sha256": diagnostics.content_sha256,
                    "missed_expected_slots": diagnostics.missed_expected_slots,
                    "value_fields_filled": diagnostics.value_fields_filled,
                    "value_fields_total": diagnostics.value_fields_total,
                    "guards_matched": diagnostics.guards_matched,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        return
    logger.warning(
        "valuation extraction produced no fields: %s",
        json.dumps(
            {
                "filename": filename,
                "outcome": diagnostics.outcome,
                "content_sha256": diagnostics.content_sha256,
                "byte_length": diagnostics.byte_length,
                "page_count": diagnostics.page_count,
                "page1_text_length": diagnostics.page1_text_length,
                "full_text_length": diagnostics.full_text_length,
                "guards_matched": diagnostics.guards_matched,
                "guards_evaluated": diagnostics.guards_evaluated,
                "value_fields_total": diagnostics.value_fields_total,
                # `no_text` with ocr_used=True is "OCR was tried and recovered
                # nothing"; with ocr_used=False it was never attempted (#5907).
                "ocr_used": diagnostics.ocr_used,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )


def _persist_extraction_outcome(db: Session, filename: str, diagnostics) -> None:
    """Write the diagnostic somewhere a container restart cannot erase.

    The WARNING above is the live signal; this is the record. They are not
    interchangeable: on 2026-09-10 the 09-08 extraction records were already
    gone because the commander container had restarted on 09-09, so the one
    surface built to be read after the fact could not be (#5663).

    Every outcome is stored, not only the failures the warning fires on. A
    partial extraction — some slots filled, some missed — is silent today
    (#5662) and is exactly the case a later reader needs the row for; and a
    successful row is what makes a failure legible by contrast, which is how
    the `FastighetPlus_` / `FastighetPlusR_` discriminator was found at all.

    A diagnostic is bookkeeping about the operator's work, never the work
    itself. If this write fails the extraction still succeeded and the
    operator must still get their fields, so the failure is logged loudly
    and swallowed rather than turned into a 500.
    """
    if diagnostics is None:
        return
    try:
        db.add(
            ExtractionDiagnostic(
                filename=filename[:255],
                outcome=diagnostics.outcome,
                content_sha256=diagnostics.content_sha256,
                byte_length=diagnostics.byte_length,
                page_count=diagnostics.page_count,
                page1_text_length=diagnostics.page1_text_length,
                full_text_length=diagnostics.full_text_length,
                guards_matched=list(diagnostics.guards_matched),
                guards_evaluated=dict(diagnostics.guards_evaluated),
                value_fields_filled=diagnostics.value_fields_filled,
                value_fields_total=diagnostics.value_fields_total,
                missed_expected_slots=list(diagnostics.missed_expected_slots),
                ocr_used=diagnostics.ocr_used,
                no_text_subkind=diagnostics.no_text_subkind,
            )
        )
        cutoff = datetime.now(timezone.utc) - timedelta(days=DIAGNOSTIC_RETENTION_DAYS)
        db.query(ExtractionDiagnostic).filter(
            ExtractionDiagnostic.created_at < cutoff
        ).delete(synchronize_session=False)
        db.commit()
    except Exception as exc:  # pragma: no cover - exercised via monkeypatched failure
        db.rollback()
        logger.exception("failed to persist extraction diagnostic for %s: %s", filename, exc)


def optional_caller_id(
    x_aspirant_user_id: int | None = Header(default=None, alias="X-Aspirant-User-Id"),
) -> int | None:
    """The caller id if the proxy set it, else None — for endpoints where the
    per-user record is a convenience, not a boundary. `/extract` embeds the
    caller's own defaults when known and an empty block otherwise, so it stays
    callable without a caller (e.g. a direct or legacy call) rather than 401'ing;
    the identity read/write endpoints use the fail-closed `require_caller_id`."""
    return x_aspirant_user_id


@router.post("/extract", response_model=ExtractResponse)
async def extract_uploads(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    caller: int | None = Depends(optional_caller_id),
):
    """Parse one or more uploaded PDFs via the field-first extractor.

    Returns one ExtractionResultOut per uploaded file plus the persisted
    operator-defaults block (appraiser identity, default likviditet).
    """
    if not files:
        raise HTTPException(status_code=400, detail="At least one PDF must be uploaded.")

    results: list[ExtractionResultOut] = []
    for upload in files:
        pdf_bytes = await upload.read()
        if len(pdf_bytes) > MAX_PDF_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"{upload.filename}: exceeds {MAX_PDF_BYTES // (1024 * 1024)} MB limit.",
            )
        if not pdf_bytes.startswith(b"%PDF"):
            raise HTTPException(
                status_code=415,
                detail=f"{upload.filename}: file is not a PDF.",
            )

        parsed = extract_document(pdf_bytes, upload.filename or "<unnamed>")
        diagnostics = parsed.diagnostics
        _log_extraction_outcome(parsed.filename, diagnostics)
        _persist_extraction_outcome(db, parsed.filename, diagnostics)
        results.append(
            ExtractionResultOut(
                filename=parsed.filename,
                fields=[
                    ExtractedFieldOut(**asdict(field)) for field in parsed.fields
                ],
                comparable_sales=[
                    ComparableSale(**row) for row in parsed.extras.get("comparable_sales", [])
                ],
                outcome=diagnostics.outcome if diagnostics else OUTCOME_EXTRACTED,
                diagnostics=(
                    ExtractionDiagnosticsOut(**asdict(diagnostics)) if diagnostics else None
                ),
            )
        )

    return ExtractResponse(
        documents=results,
        operator_defaults=_load_operator_defaults(caller, db),
    )


@router.post("/decide", response_model=DecideResponse)
async def decide_uploads(files: list[UploadFile] = File(...)):
    """Fast pre-flight for the wizard's uploads (#5915).

    Per file: does it need OCR, which image-only sub-kind, how many pages, and
    an OCR-time estimate — everything `/extract` knows within ~1.4s but does not
    send until its ~24s blocking POST returns. The client calls this first so it
    can announce the image-scanning phase and its estimate instead of spinning
    silently. No OCR is run here; the digital-PDF case returns
    `ocr_required=false` and a zero estimate.
    """
    if not files:
        raise HTTPException(status_code=400, detail="At least one PDF must be uploaded.")

    results: list[DecideResult] = []
    for upload in files:
        pdf_bytes = await upload.read()
        if len(pdf_bytes) > MAX_PDF_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"{upload.filename}: exceeds {MAX_PDF_BYTES // (1024 * 1024)} MB limit.",
            )
        if not pdf_bytes.startswith(b"%PDF"):
            raise HTTPException(
                status_code=415,
                detail=f"{upload.filename}: file is not a PDF.",
            )
        decision = decide_extraction(pdf_bytes)
        results.append(
            DecideResult(
                filename=upload.filename or "<unnamed>",
                ocr_required=decision.ocr_required,
                no_text_subkind=decision.no_text_subkind,
                page_count=decision.page_count,
                estimated_ocr_seconds=decision.estimated_ocr_seconds,
            )
        )

    return DecideResponse(
        documents=results,
        any_ocr_required=any(r.ocr_required for r in results),
        estimated_seconds=sum(r.estimated_ocr_seconds for r in results),
    )


@router.post("/generate")
def generate_filled_docx(
    body: GenerateRequest,
    format: str = Query("docx", pattern="^(docx|pdf)$"),
):
    """Render the Värdeutlåtande template with the reviewed values.

    `?format=docx` (default) returns the populated Word document.
    `?format=pdf` runs the docx through LibreOffice headless and returns
    the resulting PDF; if LibreOffice isn't installed the endpoint
    surfaces a 503 so the caller can fall back to the docx flow.

    Refuses when the signing appraiser name is blank (#5943): a värdeutlåtande
    with no mäklarnamn is an unsigned document, and silently emitting one is the
    worst available behaviour — a user whose identity has not been set (a brand-
    new account, or one not yet seeded) is told to set it rather than handed a
    blank-signed statement.
    """
    if not (body.maklare_namn or "").strip():
        raise HTTPException(
            status_code=422,
            detail=(
                "Appraiser name (mäklarnamn) is required to sign the "
                "värdeutlåtande. Set your appraiser identity before generating."
            ),
        )
    fields = TemplateFields(
        objekt=body.objekt,
        objekt_short=body.objekt_short,
        adress=body.adress,
        kommun=body.kommun,
        upplatelseform=body.upplatelseform,
        datavardering_date=body.datavardering_date,
        fastighetsutdrag_date=body.fastighetsutdrag_date,
        lagenhetsforteckning_date=body.lagenhetsforteckning_date,
        bilder_note=body.bilder_note,
        likviditet=body.likviditet,
        marknadsvarde_kr=body.marknadsvarde_kr,
        intervall_kr=body.intervall_kr,
        ort=body.ort,
        datum=body.datum,
        maklare_namn=body.maklare_namn,
        maklare_titel=body.maklare_titel,
        foretag=body.foretag,
        mode="frikopt" if body.mode == "frikopt" else "bostadsratt",
    )
    docx_bytes = populate(fields)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")

    if format == "pdf":
        try:
            pdf_bytes = docx_to_pdf(docx_bytes)
        except LibreOfficeUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except LibreOfficeConversionFailed as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        filename = f"vardeutlatande_{stamp}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    filename = f"vardeutlatande_{stamp}.docx"
    return Response(
        content=docx_bytes,
        media_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------- operator defaults ----------

_DEFAULTS_DIR_ENV = "VALUATION_OPERATOR_DEFAULTS_DIR"


# Appraiser identity is PER-USER (#5924): `maklare_namn` / `maklare_titel` /
# `foretag` / `ort` belong to the appraiser whose documents carry them, not to
# a shared service config. So the first-time-load default is EMPTY — an empty
# form is safer than one pre-filled with a specific real person's name, which
# would sign a second appraiser's valuations with the first appraiser's identity
# (and a source-code default cannot represent two users). `likviditet` keeps its
# neutral starting value. Continuity for the existing appraiser is a one-time
# per-user seed written as cell data, never a source-code default.
_EMPTY_DEFAULTS = OperatorDefaults()


def _operator_defaults_dir() -> "Path":
    from pathlib import Path

    return Path(os.environ.get(_DEFAULTS_DIR_ENV, "/data/commander/operator_defaults"))


def _operator_defaults_path(user_id: int) -> "Path":
    """The per-user identity file. One file per appraiser, keyed by the
    server-verified caller id, so no user's write can touch another's."""
    return _operator_defaults_dir() / f"{user_id}.json"


# The appraiser-identity fields carried verbatim in a värdeutlåtande's
# `final_values`. A user's own history is the authoritative source for their
# effective identity when their per-user file has not been written yet (#5943).
_IDENTITY_KEYS = ("ort", "maklare_namn", "maklare_titel", "foretag", "likviditet")


def _seed_defaults_from_history(
    user_id: int, db: "Session"
) -> OperatorDefaults | None:
    """Derive a user's identity from their most recent signed valuation.

    #5924 moved identity to a per-user file and (correctly) shipped no source-code
    default, but nothing carried the pre-#5924 value into the new home, so an
    existing appraiser's identity came back empty and every generated document
    would have been signed with a blank mäklarnamn (#5943). Their own
    `processed_valuations.final_values` carries the identity verbatim, so the
    latest row bearing a non-empty name is the authoritative seed — a read of the
    user's own record, never a guess or another user's identity.

    Returns None when the user has no history bearing a name (a brand-new user),
    so the caller falls back to the EMPTY default rather than inventing one.
    """
    row = (
        db.query(ProcessedValuation.final_values)
        .filter(
            ProcessedValuation.owner_user_id == user_id,
            ProcessedValuation.final_values["maklare_namn"].astext.isnot(None),
            ProcessedValuation.final_values["maklare_namn"].astext != "",
        )
        .order_by(ProcessedValuation.created_at.desc())
        .first()
    )
    if row is None:
        return None
    final_values = row[0] or {}
    name = (final_values.get("maklare_namn") or "").strip()
    if not name:
        return None
    return OperatorDefaults(
        ort=final_values.get("ort"),
        maklare_namn=name,
        maklare_titel=final_values.get("maklare_titel"),
        foretag=final_values.get("foretag"),
        likviditet=final_values.get("likviditet") or "normal",
    )


def _load_operator_defaults(
    user_id: int | None, db: "Session | None" = None
) -> OperatorDefaults:
    """Read one appraiser's own persisted identity defaults.

    Keyed by the caller id (the aspirant-server proxy sets `X-Aspirant-User-Id`
    from the verified session and strips any client value, #3096). When the user
    has never saved — or is unknown — returns the EMPTY default, never another
    user's identity and never a hardcoded name.

    When the per-user file is absent and a db session is available, seed it once
    from the user's own valuation history (#5943): the seed is written to the
    file so it becomes the user's editable record and never re-derives, and it
    only ever fires when no file exists, so an explicit save (or a hand-restored
    identity) is never overwritten. A user with no history seeds nothing and
    reads empty.
    """
    import json

    if user_id is None:
        return _EMPTY_DEFAULTS.model_copy()
    path = _operator_defaults_path(user_id)
    if not path.exists():
        if db is not None:
            seeded = _seed_defaults_from_history(user_id, db)
            if seeded is not None:
                _write_operator_defaults(user_id, seeded)
                return seeded
        return _EMPTY_DEFAULTS.model_copy()
    try:
        return OperatorDefaults(**json.loads(path.read_text()))
    except Exception as exc:
        logger.warning("Failed to load operator defaults at %s: %s", path, exc)
        return _EMPTY_DEFAULTS.model_copy()


def _write_operator_defaults(user_id: int, defaults: OperatorDefaults) -> None:
    """Persist one appraiser's identity to their per-user file."""
    path = _operator_defaults_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(defaults.model_dump_json(indent=2))


@router.get("/operator-defaults", response_model=OperatorDefaults)
def get_operator_defaults(
    caller: int = Depends(require_caller_id), db: Session = Depends(get_db)
):
    """Read the caller's own persisted appraiser-identity defaults.

    Mirrors the `operator_defaults` block embedded in `/extract`'s response
    so the frontend (or a manual-entry caller) can hydrate the form without
    first uploading a PDF. Scoped to the caller — never a shared record. On a
    first load with no file, seeds once from the caller's own history (#5943).
    """
    return _load_operator_defaults(caller, db)


@router.put("/operator-defaults", response_model=OperatorDefaults)
def save_operator_defaults(
    body: OperatorDefaults, caller: int = Depends(require_caller_id)
):
    """Persist the caller's OWN appraiser identity (#5924).

    The write is scoped to the caller's id (from the forge-proof proxy header),
    so a Member can only ever change their own identity — there is no shared
    record to corrupt, which is why this is Member-writable without re-opening
    the #3182 integrity finding that gated the old single global record.
    """
    _write_operator_defaults(caller, body)
    return body
