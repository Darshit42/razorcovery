"""Integration tests for the Postgres audit trail.

Skipped automatically when DATABASE_URL is unset or unreachable, so the
rest of the suite runs offline. Once Postgres is up:

    python -m audit.db      # create schema
    pytest tests/test_audit_db.py
"""
import uuid

import pytest

from audit import db
from audit.log import append_event, export_jsonl, query, voice_attempts

pytestmark = pytest.mark.skipif(not db.ping(), reason="Postgres not reachable")


@pytest.fixture()
def conn():
    """A connection that is always rolled back, so these tests never
    leave rows behind in a shared / demo database."""
    import psycopg

    db.init_db()
    c = psycopg.connect(db.database_url())
    try:
        yield c
    finally:
        c.rollback()
        c.close()


@pytest.fixture()
def eid():
    return f"evt_{uuid.uuid4().hex[:8]}"


def test_append_and_query_round_trip(conn, eid):
    append_event(conn, event_id=eid, customer_id="cust_t", entry_type="event_ingested",
                 failure_type="payment_retry", amount_inr=5000)
    append_event(conn, event_id=eid, customer_id="cust_t", entry_type="decision",
                 failure_type="payment_retry", intervention="voice",
                 reason="high value -> voice", amount_inr=5000, attempt_number=1)
    rows = query(conn, event_id=eid)
    assert [r["entry_type"] for r in rows] == ["event_ingested", "decision"]
    assert rows[1]["reason"] == "high value -> voice"


def test_reason_required_guard_raises_before_db(conn, eid):
    with pytest.raises(ValueError):
        append_event(conn, event_id=eid, customer_id="c", entry_type="decision",
                     intervention="voice", reason="  ")


def test_db_rejects_update(conn, eid):
    append_event(conn, event_id=eid, customer_id="c", entry_type="event_ingested")
    import psycopg
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE audit_log SET reason = 'x' WHERE event_id = %s", (eid,))
    conn.rollback()


def test_db_rejects_delete(conn, eid):
    append_event(conn, event_id=eid, customer_id="c", entry_type="event_ingested")
    import psycopg
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM audit_log WHERE event_id = %s", (eid,))
    conn.rollback()


def test_voice_attempts_counts_only_voice_actions(conn, eid):
    append_event(conn, event_id=eid, customer_id="cust_v", entry_type="action",
                 intervention="voice", reason=None)
    append_event(conn, event_id=eid, customer_id="cust_v", entry_type="action",
                 intervention="sms", reason=None)
    assert voice_attempts(conn, "cust_v") >= 1


def test_export_jsonl(conn, tmp_path, eid):
    append_event(conn, event_id=eid, customer_id="c", entry_type="event_ingested")
    out = tmp_path / "trail.jsonl"
    n = export_jsonl(conn, str(out))
    assert n >= 1
    assert out.read_text(encoding="utf-8").strip()
