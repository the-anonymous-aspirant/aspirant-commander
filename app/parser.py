import logging
import re
from dataclasses import dataclass, field

import dateparser

logger = logging.getLogger(__name__)

FILLER_WORDS = {
    "uh", "um", "so", "like", "you know", "basically",
    "actually", "well", "right", "just", "i mean",
}

VALID_PRIORITIES = {"low", "medium", "high", "critical"}

VALID_TABLES = {"task", "note"}

TABLE_DIMENSIONS = {
    "task": {"title", "content", "date", "priority", "label", "status"},
    "note": {"title", "content", "tag", "date"},
}

CRUD_OPERATIONS = {"create", "read", "update", "delete"}


@dataclass
class ParseResult:
    tasks: list[dict] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)
    commands: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def preprocess_lightweight(text: str) -> str:
    """Convert to lowercase, strip punctuation, collapse whitespace."""
    cleaned = text.lower()
    cleaned = re.sub(r"[,\.\!\?\;\:\"\'\-]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def strip_filler_words(text: str) -> str:
    """Remove filler words from text."""
    cleaned = text
    for filler in sorted(FILLER_WORDS, key=lambda w: -len(w)):
        pattern = r"\b" + re.escape(filler) + r"\b"
        cleaned = re.sub(pattern, " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _normalize_date(raw_date: str):
    """Use dateparser to convert natural language dates to Python date objects."""
    if not raw_date:
        return None
    # dateparser doesn't handle "next {day}" — strip "next" so bare day names
    # resolve correctly with PREFER_DATES_FROM='future'
    normalized = re.sub(r"\bnext\s+", "", raw_date.strip())
    parsed = dateparser.parse(normalized, settings={"PREFER_DATES_FROM": "future"})
    if parsed:
        return parsed.date()
    return None


def _normalize_priority(raw_priority: str | None) -> str:
    """Validate and normalize priority. Default to 'medium' if invalid."""
    if raw_priority is None:
        return "medium"
    normalized = raw_priority.strip().lower()
    if normalized in VALID_PRIORITIES:
        return normalized
    return "medium"


def _split_commands(text: str) -> list[str]:
    """Split text into command segments at 'command' keyword followed by a CRUD operation.

    Uses lookahead to split only when 'command' is followed by a valid operation,
    avoiding false splits when 'command' appears in value content.
    """
    pattern = r"\bcommand\s+(?=(?:create|read|update|delete)\b)"
    parts = re.split(pattern, text)
    # First part is text before any command keyword — discard it
    return [p.strip() for p in parts[1:] if p.strip()]


def _extract_dimensions(text: str, table: str) -> tuple[str, dict[str, str]]:
    """Extract preamble and dimension/value pairs from text.

    Returns (preamble, dimensions) where preamble is the text before the first
    'dimension' keyword, and dimensions maps keys to their values.
    Filler words are preserved in values but stripped from the preamble.
    """
    valid_dims = TABLE_DIMENSIONS.get(table, set())
    dimensions = {}

    pattern = r"\bdimension\s+(\w+)\s+value\b"
    markers = list(re.finditer(pattern, text))

    if not markers:
        return strip_filler_words(text), {}

    preamble = strip_filler_words(text[: markers[0].start()].strip())

    for i, marker in enumerate(markers):
        dim_key = marker.group(1)
        value_start = marker.end()
        value_end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        value = text[value_start:value_end].strip()

        if dim_key in valid_dims and value:
            dimensions[dim_key] = value

    return preamble, dimensions


def _build_task_data(preamble: str, dimensions: dict) -> dict | None:
    """Build task data dict from preamble and dimensions."""
    title = dimensions.get("title") or preamble or None
    content = dimensions.get("content")
    raw_date = dimensions.get("date")
    raw_priority = dimensions.get("priority")
    label = dimensions.get("label")

    # If no title but content exists, use first 60 chars
    if not title and content:
        title = content[:60]

    if not title:
        return None

    due_date = _normalize_date(raw_date) if raw_date else None
    priority = _normalize_priority(raw_priority)

    return {
        "title": title,
        "description": content,
        "due_date": due_date,
        "priority": priority,
        "label": label,
    }


def _build_note_data(preamble: str, dimensions: dict) -> dict | None:
    """Build note data dict from preamble and dimensions."""
    title = dimensions.get("title") or preamble or None
    content = dimensions.get("content")
    tag = dimensions.get("tag")
    raw_date = dimensions.get("date")

    if not content:
        return None

    noted_at = _normalize_date(raw_date) if raw_date else None

    return {
        "title": title,
        "content": content,
        "tag": tag,
        "noted_at": noted_at,
    }


def _parse_create(table: str, body: str) -> dict | None:
    """Parse a CREATE command body into a task or note dict."""
    preamble, dimensions = _extract_dimensions(body, table)

    if table == "task":
        return _build_task_data(preamble, dimensions)
    elif table == "note":
        return _build_note_data(preamble, dimensions)
    return None


def _parse_read(table: str, body: str) -> dict | None:
    """Parse a READ command body."""
    match = re.search(r"\b(\d+)\b", body)
    if not match:
        return None
    return {
        "operation": "read",
        "table": table,
        "target_id": int(match.group(1)),
        "dimensions": {},
    }


def _parse_update(table: str, body: str) -> dict | None:
    """Parse an UPDATE command body."""
    id_match = re.search(r"\b(\d+)\b", body)
    if not id_match:
        return None
    target_id = int(id_match.group(1))
    rest = body[id_match.end() :].strip()
    _, dimensions = _extract_dimensions(rest, table)

    return {
        "operation": "update",
        "table": table,
        "target_id": target_id,
        "dimensions": dimensions,
    }


def _parse_delete(table: str, body: str) -> dict | None:
    """Parse a DELETE command body."""
    match = re.search(r"\b(\d+)\b", body)
    if not match:
        return None
    return {
        "operation": "delete",
        "table": table,
        "target_id": int(match.group(1)),
        "dimensions": {},
    }


def _parse_crud_command(segment: str) -> tuple[str | None, dict | None]:
    """Parse a single command segment into (result_type, data).

    result_type is 'task', 'note', 'command', 'error', or None.
    """
    words = segment.split()
    operation = None
    table = None
    table_idx = None

    for i, word in enumerate(words):
        if operation is None:
            if word in CRUD_OPERATIONS:
                operation = word
            continue
        if table is None:
            if word in VALID_TABLES:
                table = word
                table_idx = i
                break

    if not operation or not table:
        return None, None

    body = " ".join(words[table_idx + 1 :])

    if operation == "create":
        data = _parse_create(table, body)
        if data is None:
            return "error", {"table": table}
        return table, data
    elif operation == "read":
        cmd = _parse_read(table, body)
        return ("command", cmd) if cmd else (None, None)
    elif operation == "update":
        cmd = _parse_update(table, body)
        return ("command", cmd) if cmd else (None, None)
    elif operation == "delete":
        cmd = _parse_delete(table, body)
        return ("command", cmd) if cmd else (None, None)

    return None, None


def parse_transcription(raw_text: str) -> ParseResult:
    """Parse a voice transcription for structured CRUD commands.

    Grammar: COMMAND <operation> <table> [preamble] [DIMENSION <key> VALUE <val>] ...
    Commands end at the next COMMAND keyword or end of input.
    """
    preprocessed = preprocess_lightweight(raw_text)
    segments = _split_commands(preprocessed)

    tasks = []
    notes = []
    commands = []
    errors = []

    for segment in segments:
        try:
            result_type, data = _parse_crud_command(segment)

            if result_type == "task":
                tasks.append(data)
            elif result_type == "note":
                notes.append(data)
            elif result_type == "command":
                if data:
                    commands.append(data)
                else:
                    errors.append(f"Invalid command: '{segment[:80]}'")
            elif result_type == "error":
                table = data.get("table", "unknown") if data else "unknown"
                if table == "task":
                    errors.append(
                        f"Task missing title and description: '{segment[:80]}'"
                    )
                elif table == "note":
                    errors.append(f"Note missing content: '{segment[:80]}'")
                else:
                    errors.append(f"Invalid command: '{segment[:80]}'")
            else:
                errors.append(f"Unrecognized command: '{segment[:80]}'")
        except Exception as exc:
            errors.append(f"Error parsing command: {exc}")

    return ParseResult(tasks=tasks, notes=notes, commands=commands, errors=errors)
