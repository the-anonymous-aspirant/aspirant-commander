"""Lightweight, idempotent schema migrations run at startup.

This service manages its schema with ``Base.metadata.create_all`` at lifespan
startup (``app/main.py``) rather than a migration framework. ``create_all``
creates *missing tables* but never *alters existing* ones, so a new column on a
table that already holds rows in production needs an explicit, idempotent step.

``ensure_owner_user_id_column`` is that step for the ``owner_user_id`` column
(system_3 #3125): it adds the column, backfills pre-existing rows to an
operator-supplied id, and only then enforces NOT NULL. It is safe to run on
every boot and a no-op once the column is present and fully owned — including on
a fresh database where ``create_all`` already made the column NOT NULL.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.config import VALUATION_BACKFILL_OWNER_ID

logger = logging.getLogger(__name__)

_TABLE = "processed_valuations"
_COLUMN = "owner_user_id"
_INDEX = "ix_processed_valuations_owner_user_id"


def _column_exists(engine: Engine) -> bool:
    inspector = inspect(engine)
    if not inspector.has_table(_TABLE):
        return False
    return any(col["name"] == _COLUMN for col in inspector.get_columns(_TABLE))


def _column_is_nullable(engine: Engine) -> bool:
    for col in inspect(engine).get_columns(_TABLE):
        if col["name"] == _COLUMN:
            return bool(col["nullable"])
    return False


def _unowned_row_count(engine: Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(
            text(f"SELECT count(*) FROM {_TABLE} WHERE {_COLUMN} IS NULL")
        ).scalar_one()


def ensure_owner_user_id_column(engine: Engine) -> None:
    """Add + backfill + enforce NOT NULL on ``processed_valuations.owner_user_id``.

    Steps, each idempotent:

    1. If the table is absent (brand-new DB before ``create_all``), do nothing —
       ``create_all`` will build it NOT NULL from the model.
    2. If the column is absent, ``ADD COLUMN owner_user_id INTEGER`` (nullable,
       so existing rows survive the add).
    3. Ensure the supporting index exists.
    4. Backfill NULL owners to ``VALUATION_BACKFILL_OWNER_ID`` when it is set.
    5. If no un-owned rows remain, enforce NOT NULL. If un-owned rows remain and
       no backfill id is configured, leave the column nullable and warn — the
       service must not crash on boot, and enforcing NOT NULL would fail while
       rows are un-owned. Set ``VALUATION_BACKFILL_OWNER_ID`` and reboot to
       complete the migration.
    """
    inspector = inspect(engine)
    if not inspector.has_table(_TABLE):
        # Fresh DB: create_all will make the column NOT NULL directly.
        return

    if not _column_exists(engine):
        logger.info("Migrating %s: adding %s column (nullable).", _TABLE, _COLUMN)
        with engine.begin() as conn:
            conn.execute(
                text(f"ALTER TABLE {_TABLE} ADD COLUMN {_COLUMN} INTEGER")
            )

    with engine.begin() as conn:
        conn.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS {_INDEX} "
                f"ON {_TABLE} ({_COLUMN})"
            )
        )

    if _column_is_nullable(engine):
        if VALUATION_BACKFILL_OWNER_ID is not None:
            with engine.begin() as conn:
                result = conn.execute(
                    text(
                        f"UPDATE {_TABLE} SET {_COLUMN} = :owner "
                        f"WHERE {_COLUMN} IS NULL"
                    ),
                    {"owner": VALUATION_BACKFILL_OWNER_ID},
                )
            if result.rowcount:
                logger.info(
                    "Migrating %s: backfilled %d un-owned row(s) to owner %d.",
                    _TABLE,
                    result.rowcount,
                    VALUATION_BACKFILL_OWNER_ID,
                )

        remaining = _unowned_row_count(engine)
        if remaining == 0:
            logger.info("Migrating %s: enforcing NOT NULL on %s.", _TABLE, _COLUMN)
            with engine.begin() as conn:
                conn.execute(
                    text(
                        f"ALTER TABLE {_TABLE} "
                        f"ALTER COLUMN {_COLUMN} SET NOT NULL"
                    )
                )
        else:
            logger.warning(
                "%s.%s left nullable: %d un-owned row(s) and "
                "VALUATION_BACKFILL_OWNER_ID is unset. Set it and reboot to "
                "complete the migration.",
                _TABLE,
                _COLUMN,
                remaining,
            )
