# Design Decisions

Key architectural and design decisions made during the development of the commander service.

## Command Grammar Design

**Decision:** Use a flat keyword-based grammar (`COMMAND <op> <table> [DIMENSION <key> VALUE <val>]`) instead of natural language understanding.

**Rationale:** The grammar must work reliably with speech-to-text output from Whisper, which can introduce punctuation, capitalization, and filler words unpredictably. A keyword-based approach is deterministic: if the keywords are present, the command is recognized. This avoids the complexity and unreliability of NLP-based intent classification for a small, well-defined command set.

**Trade-off:** Users must learn a specific vocabulary. This is mitigated by the `/vocabulary` endpoint, which returns the full grammar with examples.

## Filler Word Stripping

**Decision:** Strip filler words (uh, um, basically, you know, etc.) from structural segments (preamble/titles) but preserve them in dimension values.

**Rationale:** Filler words in preamble text produce ugly task titles ("uh fix the um bug"). But in dimension values (especially content/description), they represent the user's natural speech and should be preserved as-is. The selective approach cleans up titles without losing the natural feel of content.

## Background Polling

**Decision:** Use an asyncio background task that polls every 30 seconds, rather than event-driven processing (webhooks, message queue).

**Rationale:** The commander runs alongside the transcriber in a small deployment. Adding a message queue (Redis, RabbitMQ) or webhook system would add infrastructure complexity disproportionate to the scale. Polling is simple, reliable, and easy to debug. The 30-second interval balances responsiveness with resource usage. The manual `/process` endpoint provides immediate processing when needed.

**Trade-off:** Up to 30 seconds latency between transcription completion and command processing. Acceptable for the use case (voice memos, not real-time commands).

## Natural Language Date Parsing

**Decision:** Use the `dateparser` library for converting spoken dates ("tomorrow", "next friday", "in two weeks") to Python date objects.

**Rationale:** Date parsing is a well-solved problem. `dateparser` handles a wide range of natural language date expressions with the `PREFER_DATES_FROM='future'` setting ensuring forward-looking interpretation. Invalid dates silently resolve to NULL rather than raising errors, matching the forgiving nature of voice input.

**Special handling:** The word "next" is stripped before passing to dateparser because dateparser does not handle "next Monday" correctly -- it needs just "Monday" with the `PREFER_DATES_FROM='future'` setting to resolve to the upcoming occurrence.

## 1-Based Target IDs

**Decision:** Voice commands reference tasks/notes by 1-based creation-order index ("close task 3") rather than UUID.

**Rationale:** UUIDs are not speakable. A person cannot say "close task a1b2c3d4-e5f6-...". Instead, tasks are numbered by creation order (oldest = 1), which maps naturally to how a person thinks about their task list. The poller resolves the index to a UUID at execution time.

**Trade-off:** If tasks are deleted, the numbering shifts. This is acceptable for a personal task list but would be problematic at scale.

## SQLite for Tests

**Decision:** Tests use SQLite instead of PostgreSQL.

**Rationale:** Eliminates the need for a running PostgreSQL instance during development. The SQLAlchemy ORM abstracts database differences. The CI pipeline uses PostgreSQL for integration tests via Docker, ensuring production-compatible behavior is verified before merge.

## Idempotent Processing

**Decision:** Use a `commander_processed` table with a unique constraint on `voice_message_id` to prevent duplicate processing.

**Rationale:** The polling architecture means the same transcription could be picked up multiple times if processing is slow or the service restarts mid-cycle. The unique constraint ensures each voice message is processed exactly once. The audit table also provides observability into what was processed, when, and whether it succeeded.
