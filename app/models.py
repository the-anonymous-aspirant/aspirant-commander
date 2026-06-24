import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CommanderTask(Base):
    __tablename__ = "commander_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    voice_message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    priority: Mapped[str] = mapped_column(
        String(20), nullable=False, default="medium"
    )
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_commander_tasks_status", "status"),
        Index("ix_commander_tasks_priority", "priority"),
        Index(
            "ix_commander_tasks_created_at",
            "created_at",
            postgresql_ops={"created_at": "DESC"},
        ),
        Index("ix_commander_tasks_voice_message_id", "voice_message_id"),
    )


class CommanderNote(Base):
    __tablename__ = "commander_notes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    voice_message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    mood: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tag: Mapped[str | None] = mapped_column(String(100), nullable=True)
    noted_at: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_commander_notes_tag", "tag"),
        Index("ix_commander_notes_mood", "mood"),
        Index(
            "ix_commander_notes_created_at",
            "created_at",
            postgresql_ops={"created_at": "DESC"},
        ),
        Index("ix_commander_notes_voice_message_id", "voice_message_id"),
    )


class CommanderProcessed(Base):
    __tablename__ = "commander_processed"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    voice_message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, index=True
    )
    parsed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    commands_found: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    parse_status: Mapped[str] = mapped_column(String(20), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_transcription: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProcessedValuation(Base):
    """A Värdeutlåtande processing iteration the operator may revisit.

    Stores the post-extract values (`extracted_values`) alongside the
    values actually committed for the docx (`final_values`); divergence
    flips `was_manually_edited`. Docx/PDF artefacts are regenerated on
    demand from `final_values` (no blob storage) so the table stays
    light and the live tool already runs the generator.

    Edit-in-place: PATCH mutates the row directly; no history snapshot
    table (operator decision 2026-06-24).
    """

    __tablename__ = "processed_valuations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    input_files: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    extracted_values: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    final_values: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    was_manually_edited: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index(
            "ix_processed_valuations_created_at",
            "created_at",
            postgresql_ops={"created_at": "DESC"},
        ),
    )
