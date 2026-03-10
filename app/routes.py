import uuid
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import COMMANDER_VERSION, SERVICE_NAME
from app.database import get_db
from app.models import CommanderNote, CommanderTask
from app.poller import poll_transcriptions
from app.schemas import (
    HealthResponse,
    NoteListResponse,
    NoteResponse,
    NoteUpdateRequest,
    ProcessResponse,
    TaskListResponse,
    TaskResponse,
    TaskUpdateRequest,
    VocabularyResponse,
)

# Flag toggled by the lifespan to reflect polling state
polling_active: bool = False

logger = logging.getLogger(__name__)
router = APIRouter()


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


@router.get("/health", response_model=HealthResponse)
def health_check(db: Session = Depends(get_db)):
    db_connected = False
    try:
        db.execute(text("SELECT 1"))
        db_connected = True
    except Exception:
        pass

    all_ok = db_connected
    return HealthResponse(
        status="ok" if all_ok else "degraded",
        service=SERVICE_NAME,
        version=COMMANDER_VERSION,
        database=db_connected,
        polling=polling_active,
    )


@router.get("/tasks", response_model=TaskListResponse)
def list_tasks(
    status: str | None = Query(None),
    priority: str | None = Query(None),
    label: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(CommanderTask)

    if status:
        query = query.filter(CommanderTask.status == status)
    if priority:
        query = query.filter(CommanderTask.priority == priority)
    if label:
        query = query.filter(CommanderTask.label == label)

    total = query.count()
    items = (
        query.order_by(CommanderTask.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return TaskListResponse(
        items=[TaskResponse.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(task_id: uuid.UUID, db: Session = Depends(get_db)):
    task = db.query(CommanderTask).filter(CommanderTask.id == task_id).first()
    if task is None:
        return _error(404, "not_found", "Task not found")
    return TaskResponse.model_validate(task)


@router.patch("/tasks/{task_id}", response_model=TaskResponse)
def update_task(
    task_id: uuid.UUID,
    body: TaskUpdateRequest,
    db: Session = Depends(get_db),
):
    task = db.query(CommanderTask).filter(CommanderTask.id == task_id).first()
    if task is None:
        return _error(404, "not_found", "Task not found")

    now = datetime.now(timezone.utc)
    update_data = body.model_dump(exclude_unset=True)

    if "status" in update_data:
        new_status = update_data["status"]
        old_status = task.status
        if new_status == "closed" and old_status != "closed":
            task.closed_at = now
        elif new_status != "closed" and old_status == "closed":
            task.closed_at = None

    for field_name, value in update_data.items():
        setattr(task, field_name, value)

    task.updated_at = now
    db.commit()
    db.refresh(task)

    return TaskResponse.model_validate(task)


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: uuid.UUID, db: Session = Depends(get_db)):
    task = db.query(CommanderTask).filter(CommanderTask.id == task_id).first()
    if task is None:
        return _error(404, "not_found", "Task not found")

    db.delete(task)
    db.commit()


@router.get("/notes", response_model=NoteListResponse)
def list_notes(
    tag: str | None = Query(None),
    mood: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(CommanderNote)

    if tag:
        query = query.filter(CommanderNote.tag == tag)
    if mood:
        query = query.filter(CommanderNote.mood == mood)

    total = query.count()
    items = (
        query.order_by(CommanderNote.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return NoteListResponse(
        items=[NoteResponse.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/notes/{note_id}", response_model=NoteResponse)
def get_note(note_id: uuid.UUID, db: Session = Depends(get_db)):
    note = db.query(CommanderNote).filter(CommanderNote.id == note_id).first()
    if note is None:
        return _error(404, "not_found", "Note not found")
    return NoteResponse.model_validate(note)


@router.patch("/notes/{note_id}", response_model=NoteResponse)
def update_note(
    note_id: uuid.UUID,
    body: NoteUpdateRequest,
    db: Session = Depends(get_db),
):
    note = db.query(CommanderNote).filter(CommanderNote.id == note_id).first()
    if note is None:
        return _error(404, "not_found", "Note not found")

    update_data = body.model_dump(exclude_unset=True)
    for field_name, value in update_data.items():
        setattr(note, field_name, value)

    note.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(note)

    return NoteResponse.model_validate(note)


@router.delete("/notes/{note_id}", status_code=204)
def delete_note(note_id: uuid.UUID, db: Session = Depends(get_db)):
    note = db.query(CommanderNote).filter(CommanderNote.id == note_id).first()
    if note is None:
        return _error(404, "not_found", "Note not found")

    db.delete(note)
    db.commit()


@router.post("/process", response_model=ProcessResponse)
def trigger_process(db: Session = Depends(get_db)):
    count = poll_transcriptions(db)
    return ProcessResponse(
        message=f"Processed {count} transcription(s).",
        processed_count=count,
    )


@router.get("/vocabulary", response_model=VocabularyResponse)
def get_vocabulary():
    return VocabularyResponse(
        grammar="COMMAND <operation> <table> [<preamble>] [DIMENSION <key> VALUE <val>] ...",
        operations=["create", "read", "update", "delete"],
        tables={
            "task": {
                "dimensions": ["title", "content", "date", "priority", "label", "status"],
                "notes": "For CREATE: preamble text becomes title if no DIMENSION title provided. Priority defaults to medium.",
            },
            "note": {
                "dimensions": ["title", "content", "tag", "date"],
                "notes": "For CREATE: content is required. Preamble text becomes title if no DIMENSION title provided.",
            },
        },
        priorities=["low", "medium", "high", "critical"],
        examples=[
            "COMMAND CREATE task buy groceries DIMENSION priority VALUE high DIMENSION date VALUE tomorrow",
            "COMMAND CREATE task DIMENSION title VALUE fix the login bug DIMENSION content VALUE users cannot log in DIMENSION priority VALUE critical DIMENSION label VALUE backend",
            "COMMAND CREATE note DIMENSION content VALUE had a productive morning meeting DIMENSION tag VALUE work",
            "COMMAND CREATE note morning thoughts DIMENSION content VALUE feeling good about the project progress DIMENSION date VALUE today",
            "COMMAND READ task 1",
            "COMMAND UPDATE task 2 DIMENSION priority VALUE critical",
            "COMMAND UPDATE task 5 DIMENSION status VALUE closed",
            "COMMAND DELETE task 3",
            "COMMAND UPDATE note 1 DIMENSION content VALUE updated diary entry",
            "COMMAND DELETE note 2",
        ],
    )
