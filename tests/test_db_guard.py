"""The startup database-identity guard (system_3 #7722, following #7635).

A commander process started inside a system_3 session can inherit system_3's
own `DATABASE_URL` env var and run `create_all` against system_3's database
instead of its own. `ensure_database_identity` must refuse to proceed when the
connected database's name doesn't match the service's configured own name, and
that refusal must actually crash `lifespan` startup rather than get swallowed
by the broad "likely test mode" catch-all in `app/main.py`.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

import app.main as main
from app.db_guard import WrongDatabaseError, ensure_database_identity
from tests.conftest import engine as test_engine


def _actual_db_name() -> str:
    with test_engine.connect() as conn:
        return conn.execute(text("SELECT current_database()")).scalar_one()


def test_passes_when_connected_database_matches():
    ensure_database_identity(test_engine, _actual_db_name())


def test_refuses_when_connected_database_does_not_match():
    with pytest.raises(WrongDatabaseError):
        ensure_database_identity(test_engine, "not-this-services-database")


def test_lifespan_crashes_rather_than_swallows_a_wrong_database(monkeypatch):
    """A real wrong-database answer must propagate out of lifespan, not be
    logged away as a "likely test mode" connection hiccup."""
    monkeypatch.setattr(main, "engine", test_engine)
    monkeypatch.setattr(main, "DB_NAME", "not-this-services-database")

    async def _enter_and_exit():
        async with main.lifespan(main.app):
            pass

    with pytest.raises(WrongDatabaseError):
        asyncio.run(_enter_and_exit())
