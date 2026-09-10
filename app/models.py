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
    # The authenticated caller who owns this iteration. Every read/write scopes
    # on it so one Trusted user cannot see or mutate another's valuations
    # (system_3 #3096 / #3125). Populated from the `X-Aspirant-User-Id` header
    # the aspirant-server proxy sets from the verified session (#3124). NOT NULL:
    # a row with no owner would be readable by everyone, which is the bug.
    owner_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # Legacy free-text attribution kept for display/back-compat; NOT an
    # authorisation column (nullable, client-supplied) — owner_user_id is.
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
        # Every list/get/update/delete/export filters on owner_user_id.
        Index("ix_processed_valuations_owner_user_id", "owner_user_id"),
    )


class ExtractionDiagnostic(Base):
    """What the extractor saw on one uploaded PDF, kept past the container.

    `ExtractionDiagnostics` (#5361) was built so a failed extraction says
    what it saw. It reached `logger.warning` and stopped there, which put
    its lifetime at the mercy of the commander container's: the 2026-09-08
    extraction records were already unreadable on 2026-09-10 because the
    container had restarted on 09-09 (#5663). A diagnostic whose whole
    purpose is to be read after the fact cannot live only in stdout.

    Every row here is content-free by construction, which is what makes
    persisting it cheap and privacy-safe. The dataclass it mirrors carries
    a content hash, per-guard predicate outcomes and size metrics — no text
    from the PDF — and that constraint holds on the way into this table
    (#5359 item 5). `filename` is the one client-supplied string, and it is
    already stored in `processed_valuations.input_files`, so it is not a
    new exposure; it is also the handle every correlation in the #5359
    investigation actually turned on.

    Rows are diagnostic exhaust, not a record of value: they expire on a
    fixed window (`DIAGNOSTIC_RETENTION_DAYS`) swept at write time, so
    retention is enforced by something that runs rather than by intent.
    """

    __tablename__ = "extraction_diagnostics"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # One of extraction.OUTCOME_* — the discrimination the incident turned on.
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    # sha256 of the uploaded bytes: correlates a report with an upload, and
    # tells a re-upload of the same document apart from a different export.
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_length: Mapped[int] = mapped_column(Integer, nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    page1_text_length: Mapped[int] = mapped_column(Integer, nullable=False)
    full_text_length: Mapped[int] = mapped_column(Integer, nullable=False)
    # Guard names only, never the text they matched.
    guards_matched: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    guards_evaluated: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    value_fields_filled: Mapped[int] = mapped_column(Integer, nullable=False)
    value_fields_total: Mapped[int] = mapped_column(Integer, nullable=False)
    # Expected slot KEYS the document's shape did not fill (#5662). Non-empty
    # only on a `partial` outcome. Keys are schema identifiers, never document
    # text, so the row stays content-free like the rest of the record. Lets a
    # reader of the persisted row — the case this table exists for, after the
    # container that logged it restarted — name which slots missed, not only
    # count them.
    missed_expected_slots: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        # Every read is "what happened around <time>", newest first.
        Index(
            "ix_extraction_diagnostics_created_at",
            "created_at",
            postgresql_ops={"created_at": "DESC"},
        ),
        # The retention sweep and the re-upload correlation both filter here.
        Index("ix_extraction_diagnostics_content_sha256", "content_sha256"),
    )
