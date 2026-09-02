"""Execute a batch: for each row, decide -> (call | headless conversation) -> log.

Every audit_log row written here carries payload['batch_id'] so the
metrics view can slice by batch. Row status in batch_row drives the live
table. Concurrency is capped so we don't hammer the LLM / SIP trunk.
Each row uses its own DB connection (psycopg connections are not
concurrency-safe).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from audit import db
from audit.log import append_event, query, voice_attempts
from data.schemas import Customer, FailureEvent
from decision.pipeline import process_event
from intake import store
from voice.dialer import Dialer, from_env
from voice.headless import PERSONAS, converse
from voice.outcome import CallOutcome, write_call_audit

MAX_CONCURRENCY = 2          # gentle on the LLM / SIP trunk
LIVE_CALL_TIMEOUT_S = 900


def _event_from_row(row: dict) -> FailureEvent:
    return FailureEvent(
        event_id=row["event_id"],
        created_at=datetime.now(timezone.utc),
        failure_type=row["failure_type"],
        customer=Customer(
            id=f"{row['batch_id']}-c{row['row_index']:04d}",
            name=row["customer_name"] or "Customer",
            phone=row["phone"],
            timezone=row["timezone"] or "Asia/Kolkata",
        ),
        amount_inr=int(row["amount_inr"]),
        reference_id=row["reference_id"],
        error_code=row["error_code"],
        prior_attempts=0,
        refused=False,
    )


def _tagged_sink(conn, batch_id: str):
    def sink(**kw):
        payload = dict(kw.get("payload") or {})
        payload["batch_id"] = batch_id
        kw["payload"] = payload
        return append_event(conn, **kw)
    return sink


def _call_metadata(event: FailureEvent, attempt_number: int, merchant: str) -> str:
    d = json.loads(event.model_dump_json())
    d["_call"] = {"attempt_number": attempt_number, "merchant": merchant, "dial": True}
    return json.dumps(d)


async def _await_live_outcome(conn, event_id: str) -> str | None:
    for _ in range(LIVE_CALL_TIMEOUT_S // 3):
        rows = query(conn, event_id=event_id, entry_type="outcome")
        if rows:
            return (rows[-1].get("payload") or {}).get("result")
        await asyncio.sleep(3)
    return None


def _persist_row_status(rid: int, **fields) -> None:
    """Best-effort status write with a couple of retries — a batch row
    must not get stuck in 'in_progress' just because the network blipped."""
    import time as _t

    for attempt in range(4):
        try:
            with db.get_conn() as conn:
                store.update_row(conn, rid, **fields)
            return
        except Exception:
            if attempt < 3:
                _t.sleep(1.5 * (attempt + 1))


async def _run_row(batch_id: str, row: dict, *, now: datetime, dialer: Dialer,
                   merchant: str, sem: asyncio.Semaphore, on_update) -> None:
    async with sem:
        rid = row["id"]
        _persist_row_status(rid, status="in_progress")
        on_update()

        event = _event_from_row(row)
        final_status, result, err = "completed", None, None

        with db.get_conn() as conn:
            sink = _tagged_sink(conn, batch_id)
            decision = process_event(
                event, now=now, sink=sink,
                attempts_override=voice_attempts(conn, event.customer.id),
            )
        result = decision.intervention

        if decision.intervention == "voice":
            try:
                if getattr(dialer, "configured", False):
                    await dialer.place_call(
                        room_name=f"recovery-{event.event_id}",
                        phone=event.customer.phone,
                        metadata=_call_metadata(event, decision.attempt_number or 1, merchant),
                    )
                    with db.get_conn() as conn:
                        result = await _await_live_outcome(conn, event.event_id) or "no_answer"
                else:
                    persona = PERSONAS[row["row_index"] % len(PERSONAS)]
                    outcome = None
                    for attempt in range(3):
                        try:
                            outcome = await converse(
                                event, attempt_number=decision.attempt_number or 1,
                                persona=persona, merchant=merchant,
                            )
                            break
                        except Exception:
                            if attempt < 2:
                                await asyncio.sleep(2)
                            else:
                                raise
                    with db.get_conn() as conn:
                        write_call_audit(_tagged_sink(conn, batch_id), event, outcome)
                    result = outcome.result
            except Exception as exc:  # noqa: BLE001
                outcome = CallOutcome(result="failed",
                                      attempt_number=decision.attempt_number or 1,
                                      error=f"{type(exc).__name__}: {exc}"[:200])
                with db.get_conn() as conn:
                    write_call_audit(_tagged_sink(conn, batch_id), event, outcome)
                final_status, result, err = "failed", "failed", outcome.error
        elif decision.intervention == "none":
            final_status, result = "blocked", "none"
        # sms / link_only fall through: logged decision, no delivery channel

        _persist_row_status(rid, status=final_status,
                            decided_intervention=decision.intervention,
                            result=result, error=err)
        on_update()


async def run_batch(batch_id: str, *, now: datetime | None = None,
                    on_update=lambda: None) -> dict:
    now = now or datetime.now(timezone.utc)
    dialer = from_env()

    with db.get_conn() as conn:
        batch = store.get_batch(conn, batch_id)
        if not batch:
            raise ValueError(f"unknown batch {batch_id}")
        if not batch["attested"]:
            raise PermissionError("batch is not attested — cannot run")
        merchant = batch["merchant"]
        store.set_batch_status(conn, batch_id, "running")
        rows = [r for r in store.batch_rows(conn, batch_id) if r["status"] == "queued"]

    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    # return_exceptions: one row failing (e.g. a transient DB/LLM error)
    # must not abort the whole batch — that row is marked failed below.
    results = await asyncio.gather(*(
        _run_row(batch_id, r, now=now, dialer=dialer, merchant=merchant,
                 sem=sem, on_update=on_update)
        for r in rows
    ), return_exceptions=True)

    for r, res in zip(rows, results):
        if isinstance(res, Exception):
            _persist_row_status(r["id"], status="failed", result="failed",
                                error=f"{type(res).__name__}: {res}"[:200])

    all_failed = bool(results) and all(isinstance(x, Exception) for x in results)
    status = "failed" if all_failed else "done"
    try:
        with db.get_conn() as conn:
            store.set_batch_status(conn, batch_id, status)
    except Exception:
        pass

    try:
        with db.get_conn() as conn:
            return store.batch_progress(conn, batch_id)
    except Exception:
        return {"total": len(rows), "done": len(rows), "running": 0,
                "recovered": 0, "pct": 100}


def run_batch_blocking(batch_id: str, now: datetime | None = None,
                       on_update=lambda: None) -> dict:
    return asyncio.run(run_batch(batch_id, now=now, on_update=on_update))
