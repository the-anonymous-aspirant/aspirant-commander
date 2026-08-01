"""Tests for the idempotent owner_user_id startup migration (system_3 #3125).

The conftest builds the schema fresh with ``create_all``, so ``owner_user_id`` is
already NOT NULL there. These tests simulate the *production* starting point —
an existing ``processed_valuations`` table with rows and no owner column — by
dropping the column and inserting a legacy row via raw SQL, then assert the
migration adds it, backfills it, and only then enforces NOT NULL.
"""

from __future__ import annotations

from sqlalchemy import inspect, text

import app.db_migrate as db_migrate
from app.db_migrate import ensure_owner_user_id_column
from tests.conftest import engine

TABLE = "processed_valuations"
COLUMN = "owner_user_id"

_LEGACY_INSERT = text(
    f"INSERT INTO {TABLE} "
    "(id, name, input_files, extracted_values, final_values, "
    " was_manually_edited, created_at, updated_at) "
    "VALUES (:id, :name, '[]', '{}', '{}', false, now(), now())"
)


def _column(engine_):
    for col in inspect(engine_).get_columns(TABLE):
        if col["name"] == COLUMN:
            return col
    return None


def _drop_column_and_seed_legacy_row(name: str) -> None:
    """Regress the schema to the pre-#3125 shape with one un-owned row."""
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}"))
        conn.execute(
            _LEGACY_INSERT,
            {"id": "11111111-1111-1111-1111-111111111111", "name": name},
        )


def test_migration_adds_backfills_and_enforces_not_null(monkeypatch):
    _drop_column_and_seed_legacy_row("legacy-owned")
    monkeypatch.setattr(db_migrate, "VALUATION_BACKFILL_OWNER_ID", 777)

    ensure_owner_user_id_column(engine)

    col = _column(engine)
    assert col is not None
    assert col["nullable"] is False  # NOT NULL enforced after backfill
    with engine.connect() as conn:
        owner = conn.execute(
            text(f"SELECT {COLUMN} FROM {TABLE} WHERE name = 'legacy-owned'")
        ).scalar_one()
    assert owner == 777


def test_migration_failsafe_leaves_nullable_when_id_unset(monkeypatch):
    _drop_column_and_seed_legacy_row("legacy-unowned")
    monkeypatch.setattr(db_migrate, "VALUATION_BACKFILL_OWNER_ID", None)

    # Must not raise even though an un-owned row cannot satisfy NOT NULL.
    ensure_owner_user_id_column(engine)

    col = _column(engine)
    assert col is not None
    assert col["nullable"] is True  # left nullable — service still boots
    with engine.connect() as conn:
        owner = conn.execute(
            text(f"SELECT {COLUMN} FROM {TABLE} WHERE name = 'legacy-unowned'")
        ).scalar_one()
    assert owner is None


def test_migration_is_noop_on_fresh_not_null_schema():
    # create_all (autouse setup_database) already made the column NOT NULL;
    # running the migration must be a safe no-op, however many times.
    assert _column(engine)["nullable"] is False
    ensure_owner_user_id_column(engine)
    ensure_owner_user_id_column(engine)
    assert _column(engine)["nullable"] is False


def test_migration_creates_supporting_index(monkeypatch):
    _drop_column_and_seed_legacy_row("legacy-idx")
    monkeypatch.setattr(db_migrate, "VALUATION_BACKFILL_OWNER_ID", 5)
    ensure_owner_user_id_column(engine)
    index_names = {ix["name"] for ix in inspect(engine).get_indexes(TABLE)}
    assert "ix_processed_valuations_owner_user_id" in index_names
