"""HTTP smoke tests for the metrics app. Skipped when Postgres is
unreachable (the app reads audit_log at request time)."""
import pytest

from audit import db

pytestmark = pytest.mark.skipif(not db.ping(), reason="Postgres not reachable")


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from metrics.app import app

    return TestClient(app)


def test_index_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    body = r.text
    assert "Recovery metrics" in body
    assert "Recovery by intervention" in body
    assert "Recovery by failure type" in body
    assert "Cost / effort per recovery" in body
    assert "Exceptions" in body


def test_api_summary_shape(client):
    d = client.get("/api/summary").json()
    for k in ("total_events", "recovered", "recovery_rate", "by_intervention",
              "by_failure_type", "effort", "exception_count"):
        assert k in d
    assert isinstance(d["by_failure_type"], list)


def test_api_exceptions_is_list(client):
    d = client.get("/api/exceptions").json()
    assert isinstance(d, list)
    if d:
        assert {"event_id", "why", "blocked"} <= d[0].keys()


def test_event_detail_and_404(client):
    ids = [x["event_id"] for x in client.get("/api/exceptions").json()]
    if ids:
        r = client.get(f"/event/{ids[0]}")
        assert r.status_code == 200
        assert "audit timeline" in r.text.lower()
    assert client.get("/api/event/does-not-exist").status_code == 404
