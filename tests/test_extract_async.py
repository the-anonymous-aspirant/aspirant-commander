"""Async extraction job model (system_3 #5977).

`/extract-async` submits a background job and returns its id under the ~100s
Cloudflare edge (#5969); `GET /jobs/{id}` serves the outcome from the DB.

The background task opens its OWN session via `routes.SessionLocal` (bound to the
prod DATABASE_URL); the autouse fixture repoints it at the test engine so its
commits land in the test DB the assertions read. Starlette's TestClient runs the
BackgroundTask before `post()` returns, so a submitted job is already terminal
when we assert.
"""

import uuid

import pytest

import app.valuation_statement.routes as routes_mod
from app.models import ExtractionJob
from tests.conftest import TestingSessionLocal

# A well-formed minimal one-page PDF (same structure that extracted cleanly on
# the deployed commander). The extractor recognises no fields in it but returns a
# valid ExtractionResult rather than raising — enough to drive a job to `done`.
MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
    b"/Resources<</Font<</F1 5 0 R>>>>/Contents 4 0 R>>endobj\n"
    b"4 0 obj<</Length 46>>stream\n"
    b"BT /F1 12 Tf 72 720 Td (async job model test) Tj ET\n"
    b"endstream endobj\n"
    b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
    b"trailer<</Size 6/Root 1 0 R>>\n%%EOF"
)


def _files(name: str = "t.pdf", body: bytes = MINIMAL_PDF):
    return [("files", (name, body, "application/pdf"))]


@pytest.fixture(autouse=True)
def _background_session_on_test_db(monkeypatch):
    monkeypatch.setattr(routes_mod, "SessionLocal", TestingSessionLocal)


def _read_job(job_id: str) -> ExtractionJob | None:
    """Read the job on a FRESH session so the background task's committed write is
    seen (a session that created the pending row would serve it from its cache)."""
    with TestingSessionLocal() as s:
        return s.get(ExtractionJob, uuid.UUID(job_id))


def test_extract_async_accepts_and_completes(client):
    r = client.post("/valuation-statement/extract-async", files=_files())
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    job = _read_job(job_id)
    assert job is not None
    assert job.status == "done"
    assert job.error is None
    assert job.result is not None
    assert job.result["documents"][0]["filename"] == "t.pdf"


def test_async_result_matches_sync_extract(client):
    """The async path must return exactly what sync /extract does for the same
    file, so the two cannot drift (#5977 uses one shared per-file helper)."""
    sync = client.post("/valuation-statement/extract", files=_files())
    assert sync.status_code == 200

    r = client.post("/valuation-statement/extract-async", files=_files())
    job = _read_job(r.json()["job_id"])
    assert job.status == "done"
    assert job.result["documents"] == sync.json()["documents"]


def test_extraction_failure_lands_as_terminal_job(client, monkeypatch):
    """A crash in the extractor becomes status=failed + error, never a 500 to the
    submitter and never a job stuck non-terminal."""
    def boom(*args, **kwargs):
        raise RuntimeError("extractor exploded")

    monkeypatch.setattr(routes_mod, "extract_document", boom)
    r = client.post("/valuation-statement/extract-async", files=_files())
    assert r.status_code == 202  # the submit still succeeds; the failure is the job's

    job = _read_job(r.json()["job_id"])
    assert job.status == "failed"
    assert "extractor exploded" in (job.error or "")


def test_jobs_get_returns_completed_result(make_client, db_session):
    owner = make_client(7)
    r = owner.post("/valuation-statement/extract-async", files=_files())
    job_id = r.json()["job_id"]
    # The request session cached the pending row; expire so the GET (same session,
    # via the dependency override) re-reads the background task's committed update.
    db_session.expire_all()

    g = owner.get(f"/valuation-statement/jobs/{job_id}")
    assert g.status_code == 200
    body = g.json()
    assert body["status"] == "done"
    assert body["result"]["documents"][0]["filename"] == "t.pdf"
    assert body["error"] is None


def test_jobs_get_scoped_to_owner_other_caller_404(make_client, db_session):
    """#5986 IDOR: a job carries the submitter's valuation PII, so a DIFFERENT
    authenticated caller must not read it by id — and gets a 404 identical to an
    unknown id, so it cannot confirm the job exists or leak any result. The row
    IS visible to the other client's session (shared db_session), so the 404 is
    owner-scoping, not a missing row."""
    owner = make_client(7)
    other = make_client(8)
    r = owner.post("/valuation-statement/extract-async", files=_files())
    job_id = r.json()["job_id"]
    db_session.expire_all()

    g = other.get(f"/valuation-statement/jobs/{job_id}")
    assert g.status_code == 404
    assert "result" not in g.text and "documents" not in g.text


def test_jobs_get_requires_caller_identity(make_client, db_session):
    """The read is fail-closed on identity (#5986): no X-Aspirant-User-Id → 401,
    never the pre-#3096 open behaviour of serving per-user data without a caller."""
    owner = make_client(7)
    anon = make_client(None)
    r = owner.post("/valuation-statement/extract-async", files=_files())
    job_id = r.json()["job_id"]
    db_session.expire_all()

    g = anon.get(f"/valuation-statement/jobs/{job_id}")
    assert g.status_code == 401


def test_jobs_get_unknown_id_is_404(client):
    # `client` sends the default caller header, so this reaches the lookup and
    # 404s on the unknown id (not 401 on missing identity).
    g = client.get(f"/valuation-statement/jobs/{uuid.uuid4()}")
    assert g.status_code == 404


def test_extract_async_rejects_non_pdf(client):
    r = client.post(
        "/valuation-statement/extract-async",
        files=[("files", ("t.pdf", b"this is not a pdf", "application/pdf"))],
    )
    assert r.status_code == 415


def test_extract_async_requires_a_file(client):
    r = client.post("/valuation-statement/extract-async", files=[])
    # FastAPI rejects a missing required `files` field (422); either way no job.
    assert r.status_code in (400, 422)
