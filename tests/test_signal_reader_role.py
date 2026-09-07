"""The least-privilege read-only role for the system_3 cell-signal reader (#5539).

Security ruling on #5542: the scheduled reader must connect as a role that can
SELECT `processed_valuations` and nothing else — never `aspirant_admin`. These
tests provision the role on the test database (whose superuser stands in for
`aspirant_admin`), then connect *as the role* and assert the grant is both
sufficient (can read the one table) and bounded (cannot write it, cannot read
another table). A read that a wrong grant would still pass is not evidence, so
the negative controls carry the weight.

Roles are cluster-scoped and survive the per-test table drop_all, so the
fixture drops the role in teardown to keep tests independent.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.config import SIGNAL_READER_ROLE
from app.db_migrate import _quote_literal, ensure_signal_reader_role


RO_PW = "sig_ro_test_pw_9f3k!x"  # note the '!' and 'x' — exercises literal quoting


def _admin_url():
    return make_url(os.environ["TEST_DATABASE_URL"])


def _admin_engine():
    return create_engine(_admin_url())


def _ro_engine(pw: str = RO_PW):
    url = _admin_url().set(username=SIGNAL_READER_ROLE, password=pw)
    return create_engine(url)


def _drop_role(admin) -> None:
    with admin.begin() as c:
        exists = c.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :r"),
            {"r": SIGNAL_READER_ROLE},
        ).scalar()
        if exists:
            c.exec_driver_sql(f"DROP OWNED BY {SIGNAL_READER_ROLE}")
            c.exec_driver_sql(f"DROP ROLE IF EXISTS {SIGNAL_READER_ROLE}")


@pytest.fixture()
def provisioned():
    admin = _admin_engine()
    _drop_role(admin)  # start clean even if a prior run leaked it
    ensure_signal_reader_role(admin, password=RO_PW)
    yield admin
    _drop_role(admin)
    admin.dispose()


def test_role_can_select_processed_valuations(provisioned):
    """Positive: the grant is sufficient for the reader's one query."""
    ro = _ro_engine()
    try:
        with ro.connect() as c:
            # empty table is fine — the point is no permission error is raised
            c.execute(text("SELECT count(*) FROM processed_valuations")).scalar_one()
    finally:
        ro.dispose()


def test_role_cannot_write_processed_valuations(provisioned):
    """Negative control: SELECT-only means INSERT is refused."""
    ro = _ro_engine()
    try:
        with pytest.raises(DBAPIError):
            with ro.begin() as c:
                c.execute(
                    text(
                        "INSERT INTO processed_valuations "
                        "(id, name, input_files, extracted_values, final_values, "
                        " was_manually_edited, created_at, updated_at, owner_user_id) "
                        "VALUES (gen_random_uuid(), 'x', '[]'::jsonb, '{}'::jsonb, "
                        " '{}'::jsonb, false, now(), now(), 1)"
                    )
                )
    finally:
        ro.dispose()


def test_role_cannot_read_another_table(provisioned):
    """Negative control: the grant is scoped to the one table, not the schema."""
    ro = _ro_engine()
    try:
        with pytest.raises(DBAPIError):
            with ro.connect() as c:
                c.execute(text("SELECT count(*) FROM commander_tasks"))
    finally:
        ro.dispose()


def test_provisioning_is_idempotent(provisioned):
    """Running it a second time re-syncs without error and the role still reads."""
    ensure_signal_reader_role(provisioned, password=RO_PW)
    ro = _ro_engine()
    try:
        with ro.connect() as c:
            c.execute(text("SELECT 1 FROM processed_valuations LIMIT 1"))
    finally:
        ro.dispose()


def test_no_password_is_a_noop():
    """No secret configured -> no role created (never a default credential)."""
    admin = _admin_engine()
    _drop_role(admin)
    ensure_signal_reader_role(admin, password=None)
    with admin.connect() as c:
        exists = c.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :r"),
            {"r": SIGNAL_READER_ROLE},
        ).scalar()
    admin.dispose()
    assert exists is None


def test_quote_literal_escapes_single_quotes():
    assert _quote_literal("a'b") == "'a''b'"
    with pytest.raises(ValueError):
        _quote_literal("bad\x00secret")
