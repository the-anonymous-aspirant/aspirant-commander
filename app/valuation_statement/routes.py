import json
import logging
import os
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ExtractionDiagnostic

from app.valuation_statement.api_schemas import (
    ComparableSale,
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


@router.post("/extract", response_model=ExtractResponse)
async def extract_uploads(
    files: list[UploadFile] = File(...), db: Session = Depends(get_db)
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
        operator_defaults=_load_operator_defaults(),
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
    """
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

_DEFAULTS_ENV = "VALUATION_OPERATOR_DEFAULTS_PATH"


# First-time-load values taken from the operator's ground-truth examples
# (Värdeutlåtande{BR,Hok}.pdf). Surfaced when no persisted defaults file
# exists yet so the operator doesn't have to retype the same identity on
# every fresh deploy. Overwritten the moment they tick 'Spara'.
_EXAMPLE_DEFAULTS = OperatorDefaults(
    ort="Nynäshamn",
    maklare_namn="Jenny Wiklund",
    maklare_titel="Registrerad fastighetsmäklare",
    foretag="Fastighetsbyrån",
    likviditet="normal",
)


def _load_operator_defaults() -> OperatorDefaults:
    """Read persisted appraiser-identity defaults from a JSON file.

    The path is settable via VALUATION_OPERATOR_DEFAULTS_PATH (defaults to
    /data/commander/valuation_defaults.json). When no file exists yet,
    falls back to the ground-truth example identity so first-time-load
    isn't a blank form; once the operator saves, the file is authoritative.
    """
    import json
    from pathlib import Path

    path = Path(os.environ.get(_DEFAULTS_ENV, "/data/commander/valuation_defaults.json"))
    if not path.exists():
        return _EXAMPLE_DEFAULTS.model_copy()
    try:
        data = json.loads(path.read_text())
        return OperatorDefaults(**data)
    except Exception as exc:
        logger.warning("Failed to load operator defaults at %s: %s", path, exc)
        return _EXAMPLE_DEFAULTS.model_copy()


@router.get("/operator-defaults", response_model=OperatorDefaults)
def get_operator_defaults():
    """Read the persisted appraiser-identity defaults.

    Mirrors the `operator_defaults` block embedded in `/extract`'s response
    so the frontend (or a manual-entry caller) can hydrate the form without
    first uploading a PDF.
    """
    return _load_operator_defaults()


@router.put("/operator-defaults", response_model=OperatorDefaults)
def save_operator_defaults(body: OperatorDefaults):
    """Persist the appraiser-identity defaults seen on the review step."""
    import json
    from pathlib import Path

    path = Path(os.environ.get(_DEFAULTS_ENV, "/data/commander/valuation_defaults.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.model_dump_json(indent=2))
    return body
