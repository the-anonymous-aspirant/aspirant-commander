import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import CommanderNote, CommanderProcessed, CommanderTask
from app.parser import parse_transcription

logger = logging.getLogger(__name__)


def poll_transcriptions(db: Session) -> int:
    """
    Poll for completed voice messages that have not yet been processed.
    Parse each transcription for tasks and commands, then store results.
    Returns count of processed messages.
    """
    processed_count = 0

    try:
        rows = db.execute(
            text(
                "SELECT id, transcription FROM voice_messages "
                "WHERE status = 'completed' "
                "AND id NOT IN (SELECT voice_message_id FROM commander_processed)"
            )
        ).fetchall()
    except Exception as exc:
        logger.error("Failed to query voice_messages: %s", exc)
        return 0

    for row in rows:
        voice_message_id = row[0]
        transcription = row[1]

        if not transcription:
            _record_processed(
                db,
                voice_message_id=voice_message_id,
                commands_found=0,
                parse_status="no_commands",
                raw_transcription=None,
            )
            processed_count += 1
            continue

        try:
            result = parse_transcription(transcription)

            # Create tasks from parsed task blocks
            for task_data in result.tasks:
                task = CommanderTask(
                    voice_message_id=voice_message_id,
                    title=task_data["title"],
                    description=task_data.get("description"),
                    due_date=task_data.get("due_date"),
                    priority=task_data.get("priority", "medium"),
                    label=task_data.get("label"),
                )
                db.add(task)

            # Create notes from parsed note blocks
            for note_data in result.notes:
                note = CommanderNote(
                    voice_message_id=voice_message_id,
                    title=note_data.get("title"),
                    content=note_data["content"],
                    tag=note_data.get("tag"),
                    noted_at=note_data.get("noted_at"),
                )
                db.add(note)

            # Execute commands (update, delete, read)
            for cmd in result.commands:
                _execute_command(db, cmd)

            total_found = len(result.tasks) + len(result.notes) + len(result.commands)
            parse_status = "success" if total_found > 0 else "no_commands"

            error_msg = "; ".join(result.errors) if result.errors else None

            _record_processed(
                db,
                voice_message_id=voice_message_id,
                commands_found=total_found,
                parse_status=parse_status,
                raw_transcription=transcription,
                error_message=error_msg,
            )

            db.commit()
            processed_count += 1

        except Exception as exc:
            db.rollback()
            logger.error(
                "Error processing voice message %s: %s",
                voice_message_id,
                exc,
            )
            try:
                _record_processed(
                    db,
                    voice_message_id=voice_message_id,
                    commands_found=0,
                    parse_status="error",
                    raw_transcription=transcription,
                    error_message=str(exc),
                )
                db.commit()
            except Exception as record_exc:
                db.rollback()
                logger.error(
                    "Failed to record error for voice message %s: %s",
                    voice_message_id,
                    record_exc,
                )
            processed_count += 1

    return processed_count


def _execute_command(db: Session, cmd: dict) -> None:
    """Execute a parsed CRUD command against commander_tasks or commander_notes."""
    operation = cmd["operation"]
    table = cmd["table"]
    target_id = cmd.get("target_id")

    if operation == "read":
        # Read commands are informational; no DB mutation needed
        return

    if target_id is None:
        logger.warning("Command '%s %s' has no target_id, skipping.", operation, table)
        return

    if table == "task":
        _execute_task_command(db, operation, target_id, cmd.get("dimensions", {}))
    elif table == "note":
        _execute_note_command(db, operation, target_id, cmd.get("dimensions", {}))


def _execute_task_command(
    db: Session, operation: str, target_id: int, dimensions: dict
) -> None:
    """Execute an update or delete command against commander_tasks."""
    tasks = (
        db.query(CommanderTask).order_by(CommanderTask.created_at.asc()).all()
    )

    if target_id < 1 or target_id > len(tasks):
        logger.warning(
            "Task target_id %d is out of range (1-%d).", target_id, len(tasks)
        )
        return

    task = tasks[target_id - 1]
    now = datetime.now(timezone.utc)

    if operation == "delete":
        db.delete(task)
        return

    if operation == "update":
        for field_name, value in dimensions.items():
            if field_name == "title" and value:
                task.title = value
            elif field_name == "content" and value:
                task.description = value
            elif field_name == "priority" and value:
                task.priority = value.lower()
            elif field_name == "label" and value:
                task.label = value
            elif field_name == "status" and value:
                old_status = task.status
                task.status = value.lower()
                if value.lower() == "closed" and old_status != "closed":
                    task.closed_at = now
                elif value.lower() != "closed" and old_status == "closed":
                    task.closed_at = None
        task.updated_at = now


def _execute_note_command(
    db: Session, operation: str, target_id: int, dimensions: dict
) -> None:
    """Execute an update or delete command against commander_notes."""
    notes = (
        db.query(CommanderNote).order_by(CommanderNote.created_at.asc()).all()
    )

    if target_id < 1 or target_id > len(notes):
        logger.warning(
            "Note target_id %d is out of range (1-%d).", target_id, len(notes)
        )
        return

    note = notes[target_id - 1]
    now = datetime.now(timezone.utc)

    if operation == "delete":
        db.delete(note)
        return

    if operation == "update":
        for field_name, value in dimensions.items():
            if field_name == "title" and value:
                note.title = value
            elif field_name == "content" and value:
                note.content = value
            elif field_name == "tag" and value:
                note.tag = value
        note.updated_at = now


def _record_processed(
    db: Session,
    voice_message_id,
    commands_found: int,
    parse_status: str,
    raw_transcription: str | None,
    error_message: str | None = None,
) -> None:
    """Insert a record into commander_processed."""
    record = CommanderProcessed(
        voice_message_id=voice_message_id,
        commands_found=commands_found,
        parse_status=parse_status,
        error_message=error_message,
        raw_transcription=raw_transcription,
    )
    db.add(record)
