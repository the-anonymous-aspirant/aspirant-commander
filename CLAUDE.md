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

The `Default` column is what `app/config.py` falls back to, not what the
deployment runs: `aspirant-online-commander-1` runs with `DB_USER=aspirant_admin`
and `DB_NAME=aspirant_db`, and leaves `DATABASE_URL` unset. The defaults name a
database that does not exist on the cell, which matters because they are what an
unconfigured process silently connects to.

## Running

```bash
# With Docker
docker build -t commander .
docker run -p 8000:8000 \
  -e DB_HOST=localhost \
  -e DB_USER=aspirant_admin \
  -e DB_PASSWORD=... \
  -e DB_NAME=aspirant_db \
  commander

# Local development
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## Testing

The suite needs a **real PostgreSQL**. `tests/conftest.py` builds its engine
from `TEST_DATABASE_URL` (default `postgresql://aspirant_user:aspirant_pass@localhost:5433/aspirant_db`)
and runs `Base.metadata.create_all` against it. It could not be SQLite even in
principle: the models use `JSONB` and the PostgreSQL `UUID` type.

Bring up a disposable database and run the suite in a container built from the
service image, which already carries `pytest` and `PyMuPDF`:

```bash
docker run -d --name commander-test-pg \
  -e POSTGRES_USER=aspirant_user -e POSTGRES_PASSWORD=aspirant_pass \
  -e POSTGRES_DB=aspirant_db -p 5433:5432 postgres:16-alpine

DSN=postgresql://aspirant_user:aspirant_pass@127.0.0.1:5433/aspirant_db
docker run --rm --network host -v "$PWD:/app" -w /app \
  -e DATABASE_URL="$DSN" -e TEST_DATABASE_URL="$DSN" \
  ghcr.io/the-anonymous-aspirant/aspirant-commander:latest python -m pytest tests/ -q

docker rm -f commander-test-pg
```

**Pin `DATABASE_URL` as well as `TEST_DATABASE_URL`.** `app/database.py` builds
its engine at import time from `DATABASE_URL`, and `app/config.py` defaults that
to the deployed database's host — so a run that sets only `TEST_DATABASE_URL`
still constructs a live-database engine in-process.

Some tests skip without the real-document samples under `/tmp/vardeutlatande`
(`test_guard_tolerance.py`), so the skip count is environment-dependent. Read
the failure count, not the skip count.

### Fixture gate

PRs touching an extraction strategy MUST include ≥1 new file under
`tests/fixtures/`.

"An extraction strategy" means one of three declared files — the gate arms on
these and nothing else:

| path | why |
|------|-----|
| `app/valuation_statement/field_extractor.py` | the strategy library: `CONTENT_GUARDS`, the per-slot chains, every predicate they run |
| `app/valuation_statement/extraction.py` | the chain runner and the outcome semantics — what a miss is called |
| `app/valuation_statement/_context.py` | the two text projections every strategy queries |

`routes.py`, `api_schemas.py`, `processed.py`, `template.py`, `pdf_export.py`
and `transparency.py` carry the extraction to the operator without deciding
what it finds, and do not arm the gate.

The list lives in `ARMING_PATHS` in `scripts/check_fixture_gate.sh`, and
`tests/test_fixture_gate.py` asserts every entry is a real file. That assertion
is there because the gate previously armed on `*/parsers/*` and `classifier*.py`
— paths the #1113 refactor deleted — and passed unconditionally for months
while reading exactly like a gate that was protecting something (system_3
#5665). **Adding a new module that defines strategies means adding it to
`ARMING_PATHS`**; the test catches a path that disappears, not one that was
never listed.

Enforced by `.github/workflows/fixture-gate.yml` (diff-only, no test
execution). Override: apply the `fixture-exempt` label and include a
`Fixture-exempt: <reason>` line in the PR body.

Run locally before opening a PR:

```bash
BASE_REF=origin/main HEAD_REF=HEAD ./scripts/check_fixture_gate.sh
```

The script also takes an explicit file list instead of a git diff, which is how
the tests drive it (the service image carries no `git`). Both variables are
required and the mode announces itself on stdout, so a CI run cannot enter it
silently:

```bash
CHANGED_FILES=$'app/valuation_statement/field_extractor.py' ADDED_FILES='' \
  ./scripts/check_fixture_gate.sh
```

## Key Files

| File | Purpose |
|------|---------|
| `app/main.py` | FastAPI app, lifespan, background polling loop |
| `app/parser.py` | Command grammar parser (preprocessing, splitting, dimension extraction) |
| `app/poller.py` | Polls voice_messages, runs parser, persists results |
| `app/routes.py` | API endpoint handlers |
| `app/models.py` | SQLAlchemy models (CommanderTask, CommanderNote, CommanderProcessed, ProcessedValuation) |
| `app/valuation_statement/` | Värdeutlåtande subsystem: extraction strategies, guards, diagnostics, docx/pdf export |
| `app/schemas.py` | Pydantic request/response schemas |
| `app/config.py` | Environment variable configuration |
| `app/database.py` | SQLAlchemy engine and session setup |

## Conventions

This service follows the [aspirant-meta conventions](https://github.com/the-anonymous-aspirant/aspirant-meta/blob/main/CONVENTIONS.md) for API contract, logging, testing, and Docker standards.
