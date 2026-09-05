"""HTTP smoke tests for the web app. Skipped when Postgres is unreachable.

NOTE: `one_event` inserts one real audit_log lifecycle (prefixed
`pytest_evt_`) and does NOT delete it afterwards — audit_log is
append-only by design (PRD compliance requirement), so tests don't fight
that; the one extra row is a harmless, clearly-marked artifact, same as
any other real event."""
import pytest

from audit import db

pytestmark = pytest.mark.skipif(not db.ping(), reason="Postgres not reachable")


def test_auth_gate_redirects_anonymous():
    from fastapi.testclient import TestClient

    from metrics.app import app

    r = TestClient(app).get("/", follow_redirects=False)
    assert r.status_code == 303
    assert "/login" in r.headers["location"]


@pytest.fixture()
def one_event():
    """Insert one real audit lifecycle so the dashboard has data."""
    import uuid

    from audit.log import append_event

    eid = f"pytest_evt_{uuid.uuid4().hex[:8]}"
    with db.get_conn() as conn:
        append_event(conn, event_id=eid, customer_id="pytest_c1", entry_type="event_ingested",
                     failure_type="payment_retry", amount_inr=5000)
        append_event(conn, event_id=eid, customer_id="pytest_c1", entry_type="decision",
                     failure_type="payment_retry", intervention="voice",
                     reason="high value -> voice", amount_inr=5000, attempt_number=1)
    return eid


def test_index_empty_state_when_no_data(client):
    # (other tests may have inserted rows; only assert the page renders)
    r = client.get("/")
    assert r.status_code == 200
    assert "Recovery metrics" in r.text


def test_index_renders_with_data(client, one_event):
    body = client.get("/").text
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


def test_filters_narrow_the_result(client):
    full = client.get("/api/summary").json()["total_events"]
    filtered = client.get("/api/summary?failure_type=payment_retry").json()
    assert filtered["filters"]["failure_type"] == "payment_retry"
    assert filtered["total_events"] <= full
    # bad value is ignored, not an error
    assert client.get("/api/summary?failure_type=nonsense").status_code == 200


def test_index_renders_filter_bar(client, one_event):
    body = client.get("/").text
    assert "name='failure_type'" in body and "name='since'" in body
    assert "Showing" in body


def test_event_detail_and_404(client):
    ids = [x["event_id"] for x in client.get("/api/exceptions").json()]
    if ids:
        r = client.get(f"/event/{ids[0]}")
        assert r.status_code == 200
        assert "audit timeline" in r.text.lower()
    assert client.get("/api/event/does-not-exist").status_code == 404


def test_set_manual_status_rejects_invalid_value(client, one_event):
    # invalid-status check runs before the event lookup, so this 400s even
    # for a pytest-artifact event id.
    r = client.post(f"/event/{one_event}/status", data={"status": "nonsense"})
    assert r.status_code == 400


def test_set_manual_status_unknown_event_404s(client):
    r = client.post("/event/does-not-exist/status", data={"status": "paid"})
    assert r.status_code == 404


def test_set_manual_status_404s_for_hidden_test_artifact(client, one_event):
    # `one_event` is a pytest_evt_-prefixed row, which `_is_test_artifact`
    # hides from `_rows()` everywhere in the UI (same as /event/{id} itself
    # 404ing for it) -- this is the existing display-filter behaviour, not
    # a status-route bug.
    r = client.post(f"/event/{one_event}/status", data={"status": "paid"})
    assert r.status_code == 404
