# aspirant-commander

Command parser microservice that extracts structured CRUD commands from transcribed voice text. Part of the [aspirant-online](https://github.com/the-anonymous-aspirant/aspirant-online) system.

The commander polls for completed voice transcriptions, parses them using a keyword-based grammar, and persists extracted tasks and notes to PostgreSQL.

## Quick Start

```bash
# Build and run with Docker
docker build -t commander .
docker run -p 8000:8000 \
  -e DB_HOST=localhost \
  -e DB_USER=postgres \
  -e DB_PASSWORD=postgres \
  -e DB_NAME=aspirant_online_db \
  commander

# Verify
curl http://localhost:8000/health
```

### Local Development

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Run Tests

```bash
pytest tests/ -v
```

## Command Grammar

Commands are embedded in voice transcriptions using a flat keyword-based grammar:

```
COMMAND <operation> <table> [<preamble>] [DIMENSION <key> VALUE <val>] ...
```

### Operations

| Operation | Description |
|-----------|-------------|
| CREATE | Create a new task or note |
| READ | Read a task or note by number |
| UPDATE | Modify fields of an existing task or note |
| DELETE | Remove a task or note by number |

### Tables

| Table | Description |
|-------|-------------|
| task | Actionable items with priority, status, due dates |
| note | Timestamped diary entries with tags |

### Examples

```
COMMAND CREATE task buy groceries DIMENSION priority VALUE high DIMENSION date VALUE tomorrow

COMMAND CREATE task DIMENSION title VALUE fix the login bug
  DIMENSION content VALUE users cannot log in
  DIMENSION priority VALUE critical DIMENSION label VALUE backend

COMMAND CREATE note DIMENSION content VALUE had a productive morning meeting
  DIMENSION tag VALUE work

COMMAND UPDATE task 2 DIMENSION priority VALUE critical
COMMAND UPDATE task 5 DIMENSION status VALUE closed
COMMAND DELETE task 3
COMMAND READ task 1
```

### Task Dimensions

| Dimension | Default | Notes |
|-----------|---------|-------|
| title | Preamble text or first 60 chars of content | Required (via title, preamble, or content) |
| content | None | Free-text body (stored as `description`) |
| date | None | Natural language, normalized via dateparser |
| priority | medium | low, medium, high, critical |
| label | None | Free-text categorization |
| status | open | For UPDATE: set to closed or open |

### Note Dimensions

| Dimension | Default | Notes |
|-----------|---------|-------|
| title | Preamble text | Optional |
| content | (required) | Main text body |
| tag | None | Free-text categorization |
| date | None | Natural language date the note refers to |

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Service health (database, polling status) |
| POST | `/process` | Manually trigger transcription processing |
| GET | `/tasks` | List tasks (query: status, priority, label, page, page_size) |
| GET | `/tasks/{id}` | Get task by UUID |
| PATCH | `/tasks/{id}` | Update task fields |
| DELETE | `/tasks/{id}` | Delete task (204) |
| GET | `/notes` | List notes (query: tag, mood, page, page_size) |
| GET | `/notes/{id}` | Get note by UUID |
| PATCH | `/notes/{id}` | Update note fields |
| DELETE | `/notes/{id}` | Delete note (204) |
| GET | `/vocabulary` | Full grammar reference with examples |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| DB_HOST | postgres | PostgreSQL hostname |
| DB_USER | postgres | PostgreSQL username |
| DB_PASSWORD | postgres | PostgreSQL password |
| DB_NAME | aspirant_online_db | Database name |
| DATABASE_URL | (built from above) | Full connection string (overrides individual vars) |
| TRANSCRIBER_POLL_INTERVAL | 30 | Seconds between polling cycles |

## Architecture

The commander runs a background polling loop (asyncio) that checks for unprocessed transcriptions every 30 seconds. Each transcription is passed through a multi-stage parser:

1. **Preprocess** -- lowercase, strip punctuation, collapse whitespace
2. **Split** -- separate into individual command segments at `COMMAND` keywords
3. **Parse** -- identify operation, table, extract preamble and dimensions
4. **Normalize** -- dates via dateparser, priority validation, filler word removal
5. **Persist** -- write tasks/notes to PostgreSQL, execute update/delete commands

Processing is idempotent: the `commander_processed` table tracks which voice messages have been handled.

## Documentation

| Document | Description |
|----------|-------------|
| [SPEC.md](docs/SPEC.md) | Full specification (grammar, endpoints, pipeline) |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Architecture, schema, integration points |
| [PARSER_GUIDE.md](docs/PARSER_GUIDE.md) | Step-by-step parser walkthrough |
| [OPERATIONS.md](docs/OPERATIONS.md) | Setup, running, testing, debugging |
| [DECISIONS.md](docs/DECISIONS.md) | Design decisions and rationale |
| [CHANGELOG.md](docs/CHANGELOG.md) | Release history |
