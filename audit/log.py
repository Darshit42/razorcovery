"""Append-only audit-trail writer + reader (PRD §6).

Only two operations are exposed: append (INSERT) and read (SELECT).
There is deliberately no update/delete path; the DB trigger in
schema.sql enforces the same rule server-side.
"""
from __future__ import annotations

import json
from typing import Any, Iterable

from psycopg.types.json import Jsonb

ENTRY_TYPES = {
    "event_ingested",
    "decision",
    "action",
    "outcome",
    "stopping_rule_triggered",
    "manual_status",
}
_REASON_REQUIRED = {"decision", "stopping_rule_triggered"}


def append_event(
    conn,
    *,
    event_id: str,
    customer_id: str,
    entry_type: str,
    failure_type: str | None = None,
    intervention: str | None = None,
    reason: str | None = None,
    amount_inr: int | None = None,
    attempt_number: int | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert one audit row. Returns {'id', 'ts'}.

    Raises ValueError before touching the DB if a required reason is missing.
    """
    if entry_type not in ENTRY_TYPES:
        raise ValueError(f"unknown entry_type: {entry_type!r}")
    if entry_type in _REASON_REQUIRED and not (reason and reason.strip()):
        raise ValueError(f"entry_type {entry_type!r} requires a non-empty reason")

    row = conn.execute(
        """
        INSERT INTO audit_log (
            event_id, customer_id, entry_type, failure_type,
            intervention, reason, amount_inr, attempt_number, payload
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, ts
        """,
        (
            event_id,
            customer_id,
            entry_type,
            failure_type,
            intervention,
            reason,
            amount_inr,
            attempt_number,
            Jsonb(payload) if payload is not None else None,
        ),
    ).fetchone()
    return {"id": row[0], "ts": row[1]}


_COLUMNS = [
    "id", "ts", "event_id", "customer_id", "entry_type", "failure_type",
    "intervention", "reason", "amount_inr", "attempt_number", "payload",
]


def query(
    conn,
    *,
    event_id: str | None = None,
    customer_id: str | None = None,
    entry_type: str | None = None,
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if event_id is not None:
        clauses.append("event_id = %s")
        params.append(event_id)
    if customer_id is not None:
        clauses.append("customer_id = %s")
        params.append(customer_id)
    if entry_type is not None:
        clauses.append("entry_type = %s")
        params.append(entry_type)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM audit_log {where} ORDER BY id",
        params,
    ).fetchall()
    return [dict(zip(_COLUMNS, r)) for r in rows]


def voice_attempts(conn, customer_id: str) -> int:
    """Count prior voice-call actions logged for a customer.

    Combined with the event's own `prior_attempts`, this lets the batch
    runner keep the stopping-rule counter honest across a run.
    """
    row = conn.execute(
        """
        SELECT count(*) FROM audit_log
        WHERE customer_id = %s AND entry_type = 'action' AND intervention = 'voice'
        """,
        (customer_id,),
    ).fetchone()
    return int(row[0])


def export_jsonl(conn, path: str) -> int:
    """Dump the whole trail as newline-delimited JSON. Returns row count."""
    rows = query(conn)
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            r = dict(r)
            r["ts"] = r["ts"].isoformat() if r["ts"] is not None else None
            fh.write(json.dumps(r) + "\n")
    return len(rows)


def iter_all(conn) -> Iterable[dict[str, Any]]:
    yield from query(conn)
