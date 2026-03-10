import uuid
from datetime import date, datetime

from pydantic import BaseModel


class TaskResponse(BaseModel):
    id: uuid.UUID
    voice_message_id: uuid.UUID
    title: str
    description: str | None
    due_date: date | None
    priority: str
    label: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None

    model_config = {"from_attributes": True}


class TaskListResponse(BaseModel):
    items: list[TaskResponse]
    total: int
    page: int
    page_size: int


class TaskUpdateRequest(BaseModel):
    status: str | None = None
    priority: str | None = None
    label: str | None = None
    title: str | None = None
    description: str | None = None
    due_date: date | None = None


class NoteResponse(BaseModel):
    id: uuid.UUID
    voice_message_id: uuid.UUID
    title: str | None
    content: str
    mood: str | None
    tag: str | None
    noted_at: date | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class NoteListResponse(BaseModel):
    items: list[NoteResponse]
    total: int
    page: int
    page_size: int


class NoteUpdateRequest(BaseModel):
    title: str | None = None
    content: str | None = None
    tag: str | None = None
    noted_at: date | None = None


class ProcessResponse(BaseModel):
    message: str
    processed_count: int


class VocabularyResponse(BaseModel):
    grammar: str
    operations: list[str]
    tables: dict
    priorities: list[str]
    examples: list[str]


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    database: bool
    polling: bool
