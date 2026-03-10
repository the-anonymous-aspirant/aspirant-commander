# Commander Service Specification

A post-processing microservice that parses completed voice transcriptions for structured CRUD commands. Extracted commands are stored as tasks and notes with full CRUD support.

This service follows the [aspirant-meta conventions](https://github.com/the-anonymous-aspirant/aspirant-meta/blob/main/CONVENTIONS.md) for API contract, logging, testing, and Docker standards. This spec documents only what is specific to the commander.

## Command Grammar

The commander uses a flat CRUD grammar. Commands start with the `COMMAND` keyword followed by an operation and table name. No end delimiter is needed — commands end at the next `COMMAND` keyword or end of input.

```
COMMAND <operation> <table> [<preamble>] [DIMENSION <key> VALUE <val>] ...
```

### Operations

| Operation | Purpose |
|-----------|---------|
| CREATE | Create a new task or note |
| READ | Read a task or note by number |
| UPDATE | Modify dimensions of an existing task or note |
| DELETE | Remove a task or note by number |

### Tables

| Table | Description |
|-------|-------------|
| task | Actionable items with priority, status, and due dates |
| note | Timestamped diary entries with tags |

### Dimensions

Dimensions are key-value pairs specified with `DIMENSION <key> VALUE <val>`. The value extends to the next `DIMENSION` keyword or end of the command.

**Task dimensions:**

| Dimension | Required | Default | Notes |
|-----------|----------|---------|-------|
| title | No | Preamble text, or first 60 chars of content | If none available, the task is rejected |
| content | No | None | Free-text body (stored as `description` in database) |
| date | No | None | Natural language input normalized to ISO 8601 via dateparser |
| priority | No | medium | Accepts: low, medium, high, critical |
| label | No | None | Free-text label for categorization |
| status | No | open | For UPDATE: set to `closed` or `open` |

**Note dimensions:**

| Dimension | Required | Default | Notes |
|-----------|----------|---------|-------|
| title | No | Preamble text | Optional short title |
| content | Yes | — | Main diary text. If missing, the note is rejected |
| tag | No | None | Free-text tag for categorization |
| date | No | None | Natural language date the note refers to |

## Whisper Preprocessing

Raw transcriptions from Whisper undergo a two-stage preprocessing:

1. **Lightweight preprocessing** (applied to full text):
   - Lowercased
   - Punctuation stripped (commas, periods, question marks, etc.)
   - Whitespace collapsed

2. **Filler word stripping** (applied selectively to structural segments, not dimension values):
   - Removes: `uh`, `um`, `so`, `like`, `you know`, `basically`, `actually`, `well`, `right`, `just`, `i mean`
   - Applied to: preamble text (used as titles), structural parsing
   - NOT applied to: dimension values (preserves natural language in content)

This means dimension values retain filler words from the original speech, while structural elements (titles from preamble, operation/table identification) have them cleaned.

## Command Examples

### Create Task

```
COMMAND CREATE task buy groceries DIMENSION priority VALUE high DIMENSION date VALUE tomorrow
COMMAND CREATE task DIMENSION title VALUE fix the login bug DIMENSION content VALUE users cannot log in DIMENSION priority VALUE critical DIMENSION label VALUE backend
```

- **Preamble as title:** Text between `CREATE task` and the first `DIMENSION` keyword is used as the title if no explicit `DIMENSION title` is provided.
- Priority defaults to `medium` if not specified.

### Create Note

```
COMMAND CREATE note DIMENSION content VALUE had a productive morning meeting DIMENSION tag VALUE work
COMMAND CREATE note morning thoughts DIMENSION content VALUE feeling good about the project progress DIMENSION date VALUE today
```

- Content is required; notes without content are rejected.
- Preamble text becomes the title if no `DIMENSION title` is provided.

### Read

```
COMMAND READ task 1
COMMAND READ note 3
```

Read commands are informational and do not mutate the database.

### Update

```
COMMAND UPDATE task 2 DIMENSION priority VALUE critical
COMMAND UPDATE task 5 DIMENSION status VALUE closed
COMMAND UPDATE note 1 DIMENSION content VALUE updated diary entry
```

- Setting `DIMENSION status VALUE closed` on a task sets `closed_at` timestamp.
- Setting `DIMENSION status VALUE open` on a closed task clears `closed_at`.

### Delete

```
COMMAND DELETE task 3
COMMAND DELETE note 2
```

### Multiple Commands

Multiple commands in a single transcription are separated by the `COMMAND` keyword:

```
COMMAND CREATE task DIMENSION title VALUE new feature DIMENSION priority VALUE high COMMAND UPDATE task 1 DIMENSION status VALUE closed COMMAND DELETE task 3
```

### Date Normalization

| Voice input | Parsed result |
|-------------|---------------|
| "tomorrow" | Next calendar day |
| "next friday" | Following Friday |
| "march 15" | 2025-03-15 |
| "in two weeks" | Current date + 14 days |

## Parser Logic

1. Apply lightweight preprocessing (lowercase, strip punctuation, collapse whitespace).
2. Split on `COMMAND` keyword followed by a CRUD operation (using lookahead regex to avoid false splits when "command" appears in values).
3. For each segment, identify the operation and table name (skipping filler words between them).
4. For CREATE: extract preamble and dimensions, build task or note data.
5. For READ/UPDATE/DELETE: extract target ID and optional dimensions.
6. Validate dimensions against the table's allowlist; invalid keys are silently ignored.
7. On validation failure, log the error and skip the malformed command; continue processing.

## Processing Pipeline

1. Background poller runs every 30 seconds.
2. Queries `voice_messages` for rows where `status = 'completed'` and `id` is not present in `commander_processed`.
3. For each unprocessed transcription, runs the parser.
4. CREATE commands produce new records in `commander_tasks` or `commander_notes`.
5. UPDATE/DELETE commands modify existing records (tasks or notes are referenced by 1-based index ordered by `created_at`).
6. A record is written to `commander_processed` with parse results (commands found, status, errors).
7. Processing is idempotent: the unique constraint on `voice_message_id` in `commander_processed` prevents duplicate processing.

## API Endpoints

### Health Check

```
GET /health
```

Returns service status following the [standard health endpoint contract](https://github.com/the-anonymous-aspirant/aspirant-meta/blob/main/CONVENTIONS.md#health-endpoint).

### List Tasks

```
GET /tasks?page=1&page_size=20&status=open&priority=high
```

All query parameters are optional. Returns paginated results ordered by creation date (newest first).

### Get Task

```
GET /tasks/{id}
```

Returns full task details.

### Update Task

```
PATCH /tasks/{id}
```

Accepts JSON body with fields to update (title, description, due_date, priority, label, status).

### Delete Task

```
DELETE /tasks/{id}
```

Returns 204 No Content.

### List Notes

```
GET /notes?page=1&page_size=20&mood=positive&tag=work
```

All query parameters are optional. Mood filter is retained for backward compatibility with existing data. Returns paginated results ordered by creation date (newest first).

### Get Note

```
GET /notes/{id}
```

Returns full note details.

### Update Note

```
PATCH /notes/{id}
```

Accepts JSON body with fields to update (title, content, tag, noted_at).

### Delete Note

```
DELETE /notes/{id}
```

Returns 204 No Content.

### Trigger Processing

```
POST /process
```

Manually triggers the processing pipeline for any unprocessed transcriptions. Returns the number of transcriptions processed and commands extracted.

### Vocabulary Reference

```
GET /vocabulary
```

Returns the full command grammar with operations, tables, dimensions, and examples.
