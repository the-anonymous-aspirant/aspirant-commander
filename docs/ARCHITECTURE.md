# Commander Architecture

Technical architecture, database schema, and integration details for the commander microservice.

## Service Architecture

The commander is a FastAPI microservice that runs alongside the Go backend, Vue.js frontend, and transcriber service. It shares the same PostgreSQL instance via Docker Compose networking.

```
                  +---------------+
                  |    Client     |
                  |   (Vue.js)    |
                  |     :80       |
                  +---------------+
                        |
         +--------------+--------------+
         |              |              |
  +------+------+ +-----+------+ +----+--------+
  |   Server    | | Transcriber| |  Commander  |
  |  (Go/Gin)   | |  (FastAPI) | |  (FastAPI)  |
  |   :8081     | |   :8082    | |    :8083    |
  +------+------+ +-----+------+ +------+------+
         |              |               |
         +--------------+---------------+
                        |
                 +------+------+
                 |  PostgreSQL |
                 |    :5432    |
                 +-------------+
```

- **Container port:** 8000
- **Host port:** 8083
- **Admin UI access:** Proxied through the Go server, same as the transcriber

### Background Poller

The commander uses `asyncio` to run a background polling loop alongside the FastAPI request handler. The poller:

- Starts on application startup via FastAPI lifespan events.
- Runs every 30 seconds.
- Queries for completed, unprocessed transcriptions.
- Processes them sequentially (no concurrency needed; parsing is fast and CPU-light).
- Logs each cycle with the number of transcriptions found and commands extracted.

## Database Schema

The commander owns two tables in the shared PostgreSQL database.

### `commander_tasks`

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | Primary key, generated server-side |
| voice_message_id | UUID | FK to `voice_messages.id` (nullable for manually created tasks) |
| title | VARCHAR(255) | Task title |
| description | TEXT | Task body |
| due_date | DATE | Normalized from natural language input |
| priority | VARCHAR(20) | low / medium / high / critical (default: medium) |
| label | VARCHAR(100) | Free-text categorization label |
| status | VARCHAR(20) | open / closed (default: open) |
| created_at | TIMESTAMPTZ | Row creation timestamp |
| updated_at | TIMESTAMPTZ | Last modification timestamp |

**Indexes:** status, priority, voice_message_id, created_at (DESC)

### `commander_processed`

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | Primary key |
| voice_message_id | UUID | UNIQUE constraint; FK to `voice_messages.id` |
| parsed_at | TIMESTAMPTZ | When processing completed |
| commands_found | INTEGER | Number of commands extracted from this transcription |
| parse_status | VARCHAR(20) | success / partial / failed |
| error_message | TEXT | Error details (null on success) |
| raw_transcription | TEXT | Snapshot of the transcription text at parse time |

**Indexes:** voice_message_id (unique), parse_status

The `voice_message_id` unique constraint on `commander_processed` is the idempotency mechanism. If a transcription has already been processed, the poller skips it.

## Processing Flow

```
voice_messages table                 commander_processed table
+---------------------------+        +---------------------------+
| id | status    | text     |        | voice_message_id | status |
+---------------------------+        +---------------------------+
| a1 | completed | "start.."| -----> | a1 | success             |
| b2 | completed | "hello"  | -----> | b2 | success (0 cmds)    |
| c3 | completed | "start.."| (not yet processed)                |
| d4 | pending   | null     | (ignored - not completed)          |
+---------------------------+        +---------------------------+

Poller query:
  SELECT vm.* FROM voice_messages vm
  LEFT JOIN commander_processed cp ON vm.id = cp.voice_message_id
  WHERE vm.status = 'completed' AND cp.id IS NULL

Result: only c3 is picked up for processing.
```

**Step-by-step:**

1. Poller wakes up (every 30s).
2. Runs the LEFT JOIN query above to find unprocessed completed transcriptions.
3. For each result, passes the transcription text to the parser.
4. Parser returns a list of extracted commands (tasks, status changes, queries).
5. Task commands are inserted into `commander_tasks`.
6. Status change commands (`close`, `reopen`, `update`) are applied directly to `commander_tasks`.
7. A `commander_processed` row is written with the parse results.
8. Poller sleeps for 30 seconds.

## Integration Points

### Shared PostgreSQL

All three backend services (Go server, transcriber, commander) connect to the same PostgreSQL instance. Each service owns its own tables:

| Service | Tables |
|---------|--------|
| Server (Go) | Application tables (users, etc.) |
| Transcriber | `voice_messages` |
| Commander | `commander_tasks`, `commander_processed` |

The commander reads from `voice_messages` (owned by the transcriber) but never writes to it.

### Go Server Proxy

The Go server proxies commander API requests from the frontend, following the same pattern used for the transcriber:

```
Client -> :80/api/commander/* -> Go server :8081 -> Commander :8083
```

This keeps all frontend requests going through a single origin, avoiding CORS configuration.

## Tech Stack

| Component | Version / Details |
|-----------|-------------------|
| Python | 3.11 |
| FastAPI | Web framework + lifespan background tasks |
| SQLAlchemy | ORM and database schema management |
| dateparser | Natural language date normalization |
| asyncio | Background polling loop |
| PostgreSQL | Shared database (same instance as server and transcriber) |

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| DB_USER | postgres | PostgreSQL username |
| DB_PASSWORD | postgres | PostgreSQL password |
| DB_HOST | postgres | PostgreSQL hostname |
| DB_NAME | aspirant_online_db | Database name |
| DATABASE_URL | (built from above) | Full connection string (overrides individual vars) |
| POLL_INTERVAL_SECONDS | 30 | Seconds between processing cycles |

## Resource Requirements

- **RAM:** Minimal (~100 MB). No ML models, just text parsing.
- **CPU:** Negligible. Parsing is string manipulation, not compute-heavy.
- **Port:** 8083 (host) mapped to 8000 (container).
