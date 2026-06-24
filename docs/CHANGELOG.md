# Changelog

## [Unreleased]

### Removed

- `GET /valuation-statement/about` HTTP route. The aspirant-client now
  bundles the transparency snapshot at build time (Wordweaver pattern,
  aspirant-client#112) by running `scripts/regen-valuation-about.sh`
  against `transparency.registry_as_dict()`; no runtime fetch is needed
  to render the operator-facing About disclosure. The `transparency`
  module and its tests remain — they are the build-time source of truth.

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
