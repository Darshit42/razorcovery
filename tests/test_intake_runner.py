"""Batch store + runner integration. Skipped when Postgres is unreachable.
Only rows that don't need an LLM call (link_only / blocked) are exercised
here, so the test is fast and deterministic."""
from datetime import datetime, timezone

import pytest

from audit import db

pytestmark = pytest.mark.skipif(not db.ping(), reason="Postgres not reachable")


@pytest.fixture()
def parsed_rows():
    from intake.parse import parse_sheet

    csv = (
        b"name,phone,amount,type,error_code\n"
        b"Low Value,9811111111,120,payment,card_declined\n"    # low amount -> link_only
        b"Revoked,9822222222,5000,mandate,mandate_revoked\n"   # revoked mandate -> link_only
    )
    return parse_sheet(csv, "t.csv").valid_rows


def test_create_batch_and_run_link_only(parsed_rows):
    from intake import runner, store

    store.init_schema()
    with db.get_conn() as conn:
        bid = store.create_batch(
            conn, name="pytest batch", merchant="TestCo", source_filename="t.csv",
            attested=True, attested_text="test", rows=parsed_rows,
            config={"call_window": [9, 19]},
        )

    prog = runner.run_batch_blocking(
        bid, now=datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc),
    )
    assert prog["total"] == 2
    assert prog["done"] == 2

    with db.get_conn() as conn:
        rows = store.batch_rows(conn, bid)
        assert {r["status"] for r in rows} == {"completed"}
        assert {r["decided_intervention"] for r in rows} == {"link_only"}
        batch = store.get_batch(conn, bid)
        assert batch["status"] == "done"
        # audit rows are tagged with the batch id
        n = conn.execute(
            "SELECT count(*) FROM audit_log WHERE payload->>'batch_id' = %s", (bid,)
        ).fetchone()[0]
        assert n >= 4  # 2 event_ingested + 2 decision


def test_run_refuses_unattested_batch(parsed_rows):
    from intake import runner, store

    store.init_schema()
    with db.get_conn() as conn:
        bid = store.create_batch(
            conn, name="unattested", merchant="X", source_filename="t.csv",
            attested=False, attested_text="", rows=parsed_rows[:1], config={},
        )
    with pytest.raises(PermissionError):
        runner.run_batch_blocking(bid)


def test_batch_progress_and_list(parsed_rows):
    from intake import store

    store.init_schema()
    with db.get_conn() as conn:
        bid = store.create_batch(
            conn, name="listing", merchant="ListCo", source_filename="t.csv",
            attested=True, attested_text="t", rows=parsed_rows, config={},
        )
        p = store.batch_progress(conn, bid)
        assert p["total"] == 2 and p["done"] == 0 and p["pct"] == 0
        ids = [b["id"] for b in store.list_batches(conn)]
        assert bid in ids
