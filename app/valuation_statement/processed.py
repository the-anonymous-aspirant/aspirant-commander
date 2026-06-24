"""REST endpoints for the processed-valuations store.

Backs the 'Tidigare värderingar' tab in the Värdeutlåtande tool: every
processing iteration the operator commits via /generate gets persisted
here so they can revisit, rename, edit-in-place, delete, re-download
the docx/pdf, or bulk-export to CSV.

Edit-in-place: PATCH updates the existing row; no history snapshots
(operator decision 2026-06-24).

Storage shape: docx/pdf artefacts are NOT stored as blobs — they are
regenerated on demand by calling /generate with `final_values`. This
keeps the table light and matches the existing 'Edit reopens Review'
flow which re-runs the generator anyway.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ProcessedValuation
from app.valuation_statement.api_schemas import (
    ProcessedValuationCreate,
    ProcessedValuationListOut,
    ProcessedValuationOut,
    ProcessedValuationUpdate,
)


router = APIRouter(
    prefix="/valuation-statement/processed",
    tags=["valuation-statement"],
)


def _auto_name(final_values: dict, extracted_values: dict) -> str:
    """Fallback name: <YYYY-MM-DD>_<fastighetsbeteckning or objekt_short>."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    candidate = (
        final_values.get("fastighetsbeteckning")
        or final_values.get("objekt_short")
        or final_values.get("objekt")
        or extracted_values.get("fastighetsbeteckning")
        or extracted_values.get("objekt_short")
        or extracted_values.get("objekt")
        or "valuation"
    )
    return f"{stamp}_{candidate}"


def _flatten_for_csv(row: ProcessedValuation) -> dict:
    """One CSV row per valuation: metadata cols + all final/extracted keys flattened.

    Nested dicts/lists are JSON-encoded so they survive the CSV round-trip.
    The export is intentionally wide rather than normalised — the operator
    asked for a single sheet they can open in Excel.
    """
    out = {
        "id": str(row.id),
        "name": row.name,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "was_manually_edited": row.was_manually_edited,
        "created_by": row.created_by or "",
        "input_files": json.dumps(row.input_files, ensure_ascii=False),
    }
    for k, v in (row.final_values or {}).items():
        out[f"final.{k}"] = (
            json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
        )
    for k, v in (row.extracted_values or {}).items():
        out[f"extracted.{k}"] = (
            json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
        )
    return out


@router.post("", response_model=ProcessedValuationOut, status_code=201)
def create_processed_valuation(
    body: ProcessedValuationCreate,
    db: Session = Depends(get_db),
):
    """Persist one processing iteration. Called by the client right after /generate succeeds."""
    name = body.name or _auto_name(body.final_values, body.extracted_values)
    row = ProcessedValuation(
        name=name,
        input_files=body.input_files,
        extracted_values=body.extracted_values,
        final_values=body.final_values,
        was_manually_edited=body.final_values != body.extracted_values,
        created_by=body.created_by,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("", response_model=ProcessedValuationListOut)
def list_processed_valuations(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """List iterations, newest first. Paginated; the tab UI defaults to 50/page."""
    total = db.query(ProcessedValuation).count()
    items = (
        db.query(ProcessedValuation)
        .order_by(ProcessedValuation.created_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return ProcessedValuationListOut(
        items=[ProcessedValuationOut.model_validate(it) for it in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/export.csv")
def export_processed_valuations_csv(db: Session = Depends(get_db)):
    """Bulk CSV of every iteration — full metadata + flattened values.

    The header row is the union of all keys across all rows so a sparse
    column (one row has 'final.balkong', another doesn't) still renders
    in every row's cell — empty when absent.
    """
    rows = (
        db.query(ProcessedValuation)
        .order_by(ProcessedValuation.created_at.desc())
        .all()
    )
    flattened = [_flatten_for_csv(r) for r in rows]

    base_cols = [
        "id",
        "name",
        "created_at",
        "updated_at",
        "was_manually_edited",
        "created_by",
        "input_files",
    ]
    extra_keys: set[str] = set()
    for row in flattened:
        extra_keys.update(k for k in row if k not in base_cols)
    fieldnames = base_cols + sorted(extra_keys)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in flattened:
        writer.writerow(row)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="processed_valuations_{stamp}.csv"'
            ),
        },
    )


@router.get("/{valuation_id}", response_model=ProcessedValuationOut)
def get_processed_valuation(valuation_id: UUID, db: Session = Depends(get_db)):
    row = db.get(ProcessedValuation, valuation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Processed valuation not found")
    return row


@router.patch("/{valuation_id}", response_model=ProcessedValuationOut)
def update_processed_valuation(
    valuation_id: UUID,
    body: ProcessedValuationUpdate,
    db: Session = Depends(get_db),
):
    """Edit-in-place: mutate the row; recompute was_manually_edited if values changed."""
    row = db.get(ProcessedValuation, valuation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Processed valuation not found")

    if body.name is not None:
        row.name = body.name
    if body.extracted_values is not None:
        row.extracted_values = body.extracted_values
    if body.final_values is not None:
        row.final_values = body.final_values

    row.was_manually_edited = row.final_values != row.extracted_values

    db.commit()
    db.refresh(row)
    return row


@router.delete("/{valuation_id}", status_code=204)
def delete_processed_valuation(valuation_id: UUID, db: Session = Depends(get_db)):
    row = db.get(ProcessedValuation, valuation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Processed valuation not found")
    db.delete(row)
    db.commit()
    return Response(status_code=204)
