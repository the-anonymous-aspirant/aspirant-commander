# Changelog

## [1.0.0] - 2025-03-10

### Initial Release

Extracted from the [aspirant-online](https://github.com/the-anonymous-aspirant/aspirant-online) monorepo as a standalone service.

#### Features

- CRUD command grammar: `COMMAND <operation> <table> [DIMENSION <key> VALUE <val>]`
- Operations: CREATE, READ, UPDATE, DELETE
- Tables: task, note
- Background polling of completed voice transcriptions (30s interval)
- Natural language date parsing via dateparser
- Filler word stripping (selective: preamble only, not dimension values)
- Priority normalization (low/medium/high/critical)
- Full REST API for tasks and notes (list, get, update, delete)
- Manual processing trigger endpoint
- Vocabulary/grammar reference endpoint
- Health check with database and polling status
- Idempotent processing via `commander_processed` audit table
