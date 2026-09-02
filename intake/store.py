"""Persistence for batches and their rows (Postgres, reuses audit.db)."""
from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from audit import db
from intake.schemas import ParsedRow

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def init_schema() -> None:
    with db.get_conn() as conn:
        conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))


def new_batch_id() -> str:
    return "batch_" + secrets.token_hex(5)


def create_batch(
    conn,
    *,
    name: str,
    merchant: str,
    source_filename: str,
    attested: bool,
    attested_text: str,
    rows: list[ParsedRow],
    config: dict[str, Any],
) -> str:
    batch_id = new_batch_id()
    conn.execute(
        """
        INSERT INTO batch (id, name, merchant, source_filename, attested,
                           attested_text, status, total_rows, config)
        VALUES (%s,%s,%s,%s,%s,%s,'pending',%s,%s)
        """,
        (batch_id, name, merchant, source_filename, attested, attested_text,
         len(rows), Jsonb(config)),
    )
    for r in rows:
        conn.execute(
            """
            INSERT INTO batch_row (batch_id, row_index, event_id, customer_name,
                phone, amount_inr, failure_type, reference_id, error_code,
                timezone, raw, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'queued')
            """,
            (batch_id, r.row_index, f"{batch_id}-{r.row_index:04d}", r.customer_name,
             r.phone, r.amount_inr, r.failure_type, r.reference_id, r.error_code,
             r.timezone, Jsonb(r.raw)),
        )
    return batch_id


_ROW_COLS = [
    "id", "batch_id", "row_index", "event_id", "customer_name", "phone",
    "amount_inr", "failure_type", "reference_id", "error_code", "timezone",
    "status", "decided_intervention", "result", "error", "updated_at",
]


def set_batch_status(conn, batch_id: str, status: str) -> None:
    conn.execute("UPDATE batch SET status=%s WHERE id=%s", (status, batch_id))


def update_row(conn, row_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=%s" for k in fields)
    conn.execute(
        f"UPDATE batch_row SET {cols}, updated_at=now() WHERE id=%s",
        (*fields.values(), row_id),
    )


def get_batch(conn, batch_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT id, created_at, name, merchant, source_filename, attested,
                  attested_text, status, total_rows, config
           FROM batch WHERE id=%s""",
        (batch_id,),
    ).fetchone()
    if not row:
        return None
    keys = ["id", "created_at", "name", "merchant", "source_filename", "attested",
            "attested_text", "status", "total_rows", "config"]
    return dict(zip(keys, row))


def list_batches(conn, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT b.id, b.created_at, b.name, b.merchant, b.status, b.total_rows,
               count(r.*) FILTER (WHERE r.status = 'completed') AS completed,
               count(r.*) FILTER (WHERE r.status IN ('failed','blocked','skipped')) AS not_done,
               count(r.*) FILTER (WHERE r.result = 'recovered') AS recovered
        FROM batch b LEFT JOIN batch_row r ON r.batch_id = b.id
        GROUP BY b.id ORDER BY b.created_at DESC LIMIT %s
        """,
        (limit,),
    ).fetchall()
    keys = ["id", "created_at", "name", "merchant", "status", "total_rows",
            "completed", "not_done", "recovered"]
    return [dict(zip(keys, r)) for r in rows]


def batch_rows(conn, batch_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""SELECT {', '.join(_ROW_COLS)} FROM batch_row
            WHERE batch_id=%s ORDER BY row_index""",
        (batch_id,),
    ).fetchall()
    return [dict(zip(_ROW_COLS, r)) for r in rows]


def batch_progress(conn, batch_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT count(*) AS total,
               count(*) FILTER (WHERE status IN ('completed','failed','blocked','skipped')) AS done,
               count(*) FILTER (WHERE status = 'in_progress') AS running,
               count(*) FILTER (WHERE result = 'recovered') AS recovered
        FROM batch_row WHERE batch_id=%s
        """,
        (batch_id,),
    ).fetchone()
    total, done, running, recovered = row
    return {"total": total, "done": done, "running": running,
            "recovered": recovered,
            "pct": round(100 * done / total) if total else 0}
