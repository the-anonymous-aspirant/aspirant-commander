# Commander Service

Command parser microservice for the aspirant-online system. Extracts structured CRUD commands from transcribed voice text and stores the results as tasks and notes.

## Service Overview

The commander polls the transcriber service for completed voice transcriptions, parses them using a keyword-based grammar, and persists extracted tasks and notes to PostgreSQL. It runs as a standalone FastAPI service.

- **Port:** 8000
- **Framework:** FastAPI + SQLAlchemy + dateparser
- **Language:** Python 3.11

## Dependencies

- **PostgreSQL** -- shared database with other aspirant-online services
- **Transcriber service** -- the commander polls the `voice_messages` table (owned by the transcriber) for completed transcriptions

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check (database connectivity, polling status) |
| POST | `/process` | Manually trigger transcription processing |
| GET | `/tasks` | List tasks (filterable by status, priority, label; paginated) |
| GET | `/tasks/{id}` | Get a single task |
| PATCH | `/tasks/{id}` | Update task fields |
| DELETE | `/tasks/{id}` | Delete a task |
| GET | `/notes` | List notes (filterable by tag, mood; paginated) |
| GET | `/notes/{id}` | Get a single note |
| PATCH | `/notes/{id}` | Update note fields |
| DELETE | `/notes/{id}` | Delete a note |
| GET | `/vocabulary` | Command grammar reference with examples |

## Database Tables Owned

- `commander_tasks` -- extracted tasks with title, description, due_date, priority, label, status
- `commander_notes` -- extracted notes with title, content, mood, tag, noted_at
- `commander_processed` -- processing audit log (tracks which voice messages have been parsed)

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| DB_HOST | postgres | PostgreSQL hostname |
| DB_USER | postgres | PostgreSQL username |
| DB_PASSWORD | postgres | PostgreSQL password |
| DB_NAME | aspirant_online_db | Database name |
| DATABASE_URL | (built from above) | Full connection string (overrides individual vars) |
| TRANSCRIBER_POLL_INTERVAL | 30 | Seconds between polling cycles |

## Running

```bash
# With Docker
docker build -t commander .
docker run -p 8000:8000 \
  -e DB_HOST=localhost \
  -e DB_USER=postgres \
  -e DB_PASSWORD=postgres \
  -e DB_NAME=aspirant_online_db \
  commander

# Local development
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## Testing

```bash
pytest tests/ -v
```

Tests use SQLite in-memory and do not require PostgreSQL.

### Fixture gate

PRs touching a parser branch (`app/valuation_statement/parsers/*.py`) or the
classifier (`app/valuation_statement/classifier.py`) MUST include ≥1 new file
under `tests/fixtures/`. Enforced by `.github/workflows/fixture-gate.yml`
(diff-only, no test execution). Override: apply the `fixture-exempt` label and
include a `Fixture-exempt: <reason>` line in the PR body.

Run locally before opening a PR:

```bash
BASE_REF=origin/main HEAD_REF=HEAD ./scripts/check_fixture_gate.sh
```

## Key Files

| File | Purpose |
|------|---------|
| `app/main.py` | FastAPI app, lifespan, background polling loop |
| `app/parser.py` | Command grammar parser (preprocessing, splitting, dimension extraction) |
| `app/poller.py` | Polls voice_messages, runs parser, persists results |
| `app/routes.py` | API endpoint handlers |
| `app/models.py` | SQLAlchemy models (CommanderTask, CommanderNote, CommanderProcessed) |
| `app/schemas.py` | Pydantic request/response schemas |
| `app/config.py` | Environment variable configuration |
| `app/database.py` | SQLAlchemy engine and session setup |

## Conventions

This service follows the [aspirant-meta conventions](https://github.com/the-anonymous-aspirant/aspirant-meta/blob/main/CONVENTIONS.md) for API contract, logging, testing, and Docker standards.
