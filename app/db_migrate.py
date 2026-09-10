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

from app.config import (
    SIGNAL_READER_PASSWORD,
    SIGNAL_READER_ROLE,
    VALUATION_BACKFILL_OWNER_ID,
)

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


def _quote_literal(value: str) -> str:
    """Quote a string as a Postgres SQL literal (standard_conforming_strings on).

    Used only for the signal-reader password, which comes from our own secret
    store rather than user input. With ``standard_conforming_strings`` on (the
    Postgres default) a backslash is an ordinary character, so doubling the
    single quote is sufficient; a NUL byte cannot appear in a SQL string and
    means a corrupt secret, so reject it rather than emit broken DDL.
    """
    if "\x00" in value:
        raise ValueError("signal-reader password contains a NUL byte")
    return "'" + value.replace("'", "''") + "'"


def ensure_signal_reader_role(engine: Engine, password: str | None = None) -> None:
    """Idempotently provision the least-privilege role the system_3 cell-signal
    reader connects as (#5539; security ruling on #5542).

    The role may SELECT from ``processed_valuations`` and nothing else, and is
    never ``aspirant_admin``. Safe on every boot: it creates the role if absent,
    syncs its password to the configured secret (rotation-safe), and re-applies
    the idempotent grants.

    When no password is configured this is a NO-OP with a warning — a deploy
    that has not wired ``ASPIRANT_SIGNAL_RO_PASSWORD`` gets no role at all
    rather than one with a default/guessable credential.

    DDL that embeds the password literal is sent via ``exec_driver_sql`` so
    SQLAlchemy does not treat a ``:`` in the password as a bind parameter; the
    role name is a fixed identifier and the password literal is escaped by
    :func:`_quote_literal`.
    """
    pw = password if password is not None else SIGNAL_READER_PASSWORD
    if not pw:
        logger.warning(
            "Signal-reader role %s not provisioned: ASPIRANT_SIGNAL_RO_PASSWORD "
            "is unset. Set it from the secret store and reboot to create the "
            "least-privilege reader for the cell-signal loop (#5539).",
            SIGNAL_READER_ROLE,
        )
        return

    role = SIGNAL_READER_ROLE  # fixed identifier, never user input
    pw_lit = _quote_literal(pw)
    with engine.begin() as conn:
        dbname = conn.execute(text("SELECT current_database()")).scalar_one()
        exists = conn.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}
        ).scalar()
        if not exists:
            logger.info("Provisioning least-privilege signal-reader role %s.", role)
            conn.exec_driver_sql(f"CREATE ROLE {role} LOGIN")
        # Sync the password to the configured secret on every boot so a rotation
        # in the secret store takes effect without a manual ALTER.
        conn.exec_driver_sql(f"ALTER ROLE {role} WITH LOGIN PASSWORD {pw_lit}")
        # Least privilege: connect + read exactly one table. GRANT is idempotent.
        conn.exec_driver_sql(f'GRANT CONNECT ON DATABASE "{dbname}" TO {role}')
        conn.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO {role}")
        conn.exec_driver_sql(f"GRANT SELECT ON {_TABLE} TO {role}")


_DIAG_TABLE = "extraction_diagnostics"
_DIAG_COLUMN = "missed_expected_slots"


def ensure_missed_expected_slots_column(engine: Engine) -> None:
    """Add ``extraction_diagnostics.missed_expected_slots`` to a table predating it.

    The table shipped in #5663 without this column; #5662 adds it so a persisted
    ``partial`` row can name *which* expected slots missed, not only count them.
    ``create_all`` never ALTERs an existing table, so a deploy that already holds
    diagnostic rows needs an explicit add.

    Idempotent and safe on every boot: ``ADD COLUMN IF NOT EXISTS`` with a
    constant default is a metadata-only change on Postgres (no table rewrite)
    that backfills existing rows to ``[]``. On a fresh database ``create_all``
    already built the column NOT NULL from the model and this is a no-op.
    """
    inspector = inspect(engine)
    if not inspector.has_table(_DIAG_TABLE):
        # Fresh DB before create_all, or a deploy without the diagnostics table.
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                f"ALTER TABLE {_DIAG_TABLE} "
                f"ADD COLUMN IF NOT EXISTS {_DIAG_COLUMN} JSONB NOT NULL "
                f"DEFAULT '[]'::jsonb"
            )
        )
