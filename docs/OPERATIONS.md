# Commander Operations

Setup, running, testing, and debugging the commander service.

## Prerequisites

- **PostgreSQL 16+** -- the commander stores tasks, notes, and processing records in PostgreSQL
- **Transcriber service** -- the commander polls the `voice_messages` table owned by the transcriber; the transcriber must be running and writing completed transcriptions for the commander to have work to do
- **Python 3.11+** -- required for local development
- **Docker** -- required for containerized deployment

## Setup

### 1. Database

The commander expects a PostgreSQL database. Tables are created automatically on startup via SQLAlchemy `create_all`.

If running with Docker Compose (full aspirant-online stack), the database is shared across all services and no separate setup is needed.

For standalone operation, ensure PostgreSQL is running and accessible:

```bash
# Example: local PostgreSQL
createdb aspirant_online_db
```

### 2. Environment Variables

Set the following environment variables (or use a `.env` file):

```bash
export DB_HOST=localhost
export DB_USER=postgres
export DB_PASSWORD=postgres
export DB_NAME=aspirant_online_db
```

Or override with a full connection string:

```bash
export DATABASE_URL=postgresql://postgres:postgres@localhost/aspirant_online_db
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

## Running

### Docker (recommended)

```bash
docker build -t commander .
docker run -p 8000:8000 \
  -e DB_HOST=host.docker.internal \
  -e DB_USER=postgres \
  -e DB_PASSWORD=postgres \
  -e DB_NAME=aspirant_online_db \
  commander
```

### Local Development

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The `--reload` flag enables auto-reload on file changes.

### Verify

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{
  "status": "ok",
  "service": "commander",
  "version": "1.0.0",
  "database": true,
  "polling": true
}
```

## Testing

Tests use SQLite (no PostgreSQL required) and run entirely in-memory.

```bash
# Run all tests
pytest tests/ -v

# Run only parser tests
pytest tests/test_parser.py -v

# Run only API tests
pytest tests/test_tasks.py -v

# Run only health check test
pytest tests/test_health.py -v
```

### Test Structure

| File | What it tests |
|------|---------------|
| `tests/conftest.py` | Test fixtures (SQLite database, FastAPI test client) |
| `tests/test_health.py` | Health endpoint |
| `tests/test_parser.py` | Parser logic (preprocessing, splitting, dimensions, normalization) |
| `tests/test_tasks.py` | Task/note CRUD endpoints, vocabulary, process trigger |

## Debugging

### Check Health

```bash
curl http://localhost:8000/health
```

- `database: false` -- cannot connect to PostgreSQL; check DB_HOST, DB_USER, DB_PASSWORD, DB_NAME
- `polling: false` -- background poller did not start; check startup logs for errors

### Check Vocabulary

```bash
curl http://localhost:8000/vocabulary | python -m json.tool
```

Returns the full grammar reference. Useful for verifying the service is running and responsive.

### Trigger Manual Processing

```bash
curl -X POST http://localhost:8000/process
```

Returns `{"message": "Processed N transcription(s).", "processed_count": N}`. If count is 0, either there are no unprocessed completed transcriptions or the `voice_messages` table is empty/inaccessible.

### List Tasks

```bash
# All tasks
curl http://localhost:8000/tasks | python -m json.tool

# Filtered
curl "http://localhost:8000/tasks?status=open&priority=high" | python -m json.tool
```

### List Notes

```bash
curl http://localhost:8000/notes | python -m json.tool
```

### Common Issues

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `database: false` in health | PostgreSQL not reachable | Check DB_HOST and network connectivity |
| `polling: false` in health | Startup error | Check container/process logs |
| `processed_count: 0` always | No completed voice messages | Verify transcriber is running and has completed transcriptions |
| Tasks not appearing | Parser did not match grammar | Check transcription text matches `COMMAND <op> <table>` pattern |
| Date not parsed | Unrecognized date format | dateparser handles most natural language; check logs for the raw value |

### Logs

The commander logs to stdout in structured format:

```
2026-03-10T12:00:00Z [INFO] app.main: Background polling started (interval=30s).
2026-03-10T12:00:30Z [INFO] app.main: Polling cycle processed 2 message(s).
2026-03-10T12:01:00Z [ERROR] app.poller: Failed to query voice_messages: ...
```

Set `LOG_LEVEL` environment variable to adjust verbosity (default: INFO).
