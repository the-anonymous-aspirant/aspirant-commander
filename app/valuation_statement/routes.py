import logging
import os
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response

from app.valuation_statement.api_schemas import (
    ComparableSale,
    ExtractedFieldOut,
    ExtractResponse,
    ExtractionResultOut,
    GenerateRequest,
    OperatorDefaults,
)
from app.valuation_statement.classifier import DocumentType, classify_pdf
from app.valuation_statement.extraction import extract_document
from app.valuation_statement.pdf_export import (
    LibreOfficeConversionFailed,
    LibreOfficeUnavailable,
    docx_to_pdf,
)
from app.valuation_statement.template import TemplateFields, populate


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/valuation-statement", tags=["valuation-statement"])


MAX_PDF_BYTES = 25 * 1024 * 1024  # 25 MB per file


@router.post("/extract", response_model=ExtractResponse)
async def extract_uploads(files: list[UploadFile] = File(...)):
    """Classify + parse one or more uploaded PDFs.

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

        doc_type = classify_pdf(pdf_bytes, upload.filename)
        parsed = extract_document(pdf_bytes, doc_type, upload.filename or "<unnamed>")
        results.append(
            ExtractionResultOut(
                document_type=parsed.document_type,
                filename=parsed.filename,
                fields=[
                    ExtractedFieldOut(**asdict(field)) for field in parsed.fields
                ],
                comparable_sales=[
                    ComparableSale(**row) for row in parsed.extras.get("comparable_sales", [])
                ],
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
