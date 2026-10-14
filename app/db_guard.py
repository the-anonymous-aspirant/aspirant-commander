"""Startup guard: refuse to boot against a database that isn't this service's own.

``DATABASE_URL`` is the same environment variable name system_3 uses for its
own database. A commander process started inside a system_3 session (an agent
pane, or a worktree shell) can inherit system_3's ``DATABASE_URL`` from the
environment and run ``Base.metadata.create_all`` against **system_3's**
database instead of ``aspirant_online_db`` — this happened on 2026-09-10
(system_3 #7635), creating commander's tables in system_3's database and
writing rows there. This module checks the connected database's own name
against the configured expected name and refuses to proceed when they
disagree, rather than silently creating tables in the wrong place.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


class WrongDatabaseError(RuntimeError):
    """Raised when the connected database's name is not this service's own."""


def ensure_database_identity(engine: Engine, expected_name: str) -> None:
    """Fail fast if ``engine`` is not connected to ``expected_name``.

    Call this before any schema-creating statement (``create_all``, the
    ``db_migrate`` steps) touches the connection. A mismatch means
    ``DATABASE_URL`` — or the ``DB_*`` parts that build it — points at some
    other application's database, most commonly because an unrelated process
    left its own same-named env var (``DATABASE_URL``) in this one's
    environment.
    """
    with engine.connect() as conn:
        actual = conn.execute(text("SELECT current_database()")).scalar_one()
    if actual != expected_name:
        raise WrongDatabaseError(
            f"Connected database is {actual!r}, not this service's own "
            f"{expected_name!r}. Refusing to start: this would run "
            "create_all and the startup migrations against another "
            "application's database. Check DATABASE_URL (and DB_NAME) in "
            "this process's environment."
        )
