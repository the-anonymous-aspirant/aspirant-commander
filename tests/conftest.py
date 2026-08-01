import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.main import app

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://aspirant_user:aspirant_pass@localhost:5433/aspirant_db",
)

engine = create_engine(TEST_DATABASE_URL)
TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


@pytest.fixture(autouse=True)
def setup_database():
    """Create all tables before each test, drop after."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def db_session():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


# Default caller id the `client` fixture identifies as. The processed-valuation
# routes fail closed on a missing `X-Aspirant-User-Id` header (#3125), so tests
# that just exercise behaviour send this by default; owner-scoping tests use
# `make_client` to act as a specific owner (or none).
DEFAULT_OWNER_ID = 1


@pytest.fixture()
def make_client(db_session):
    """Factory: a TestClient identifying as `owner_id` (or none, to omit the header).

    Shares the same `db_session` override so rows created by one owner's client
    are visible to another's query, which is exactly what the isolation tests
    must be able to observe (and then assert are filtered out by owner scoping).
    """

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    clients = []

    def _make(owner_id: int | None = DEFAULT_OWNER_ID) -> TestClient:
        headers = {} if owner_id is None else {"X-Aspirant-User-Id": str(owner_id)}
        c = TestClient(app, headers=headers)
        clients.append(c)
        return c

    yield _make

    app.dependency_overrides.clear()


@pytest.fixture()
def client(make_client):
    return make_client(DEFAULT_OWNER_ID)
