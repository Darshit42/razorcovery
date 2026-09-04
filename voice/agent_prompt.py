"""Workspace-editable agent persona (the part of the system prompt a
vendor can tune). Single row — this app is one shared workspace, not
multi-tenant (PRD §4)."""
from __future__ import annotations

from datetime import datetime, timezone

from audit import db

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS agent_prompt (
    id         SMALLINT PRIMARY KEY DEFAULT 1,
    template   TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by TEXT,
    CONSTRAINT single_row CHECK (id = 1)
);
"""


def init_schema() -> None:
    with db.get_conn() as conn:
        conn.execute(SCHEMA_SQL)


def get_template() -> str | None:
    """The saved override, or None if the workspace hasn't customised it."""
    init_schema()
    with db.get_conn() as conn:
        row = conn.execute("SELECT template FROM agent_prompt WHERE id=1").fetchone()
    return row[0] if row else None


def get_meta() -> dict | None:
    init_schema()
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT template, updated_at, updated_by FROM agent_prompt WHERE id=1"
        ).fetchone()
    if not row:
        return None
    return {"template": row[0], "updated_at": row[1], "updated_by": row[2]}


def set_template(template: str, *, updated_by: str) -> None:
    template = (template or "").strip()
    if not template:
        raise ValueError("Prompt can't be empty.")
    if len(template) > 8000:
        raise ValueError("Prompt is too long (max 8000 characters).")
    init_schema()
    with db.get_conn() as conn:
        conn.execute(
            """INSERT INTO agent_prompt (id, template, updated_at, updated_by)
               VALUES (1, %s, %s, %s)
               ON CONFLICT (id) DO UPDATE
               SET template=EXCLUDED.template, updated_at=EXCLUDED.updated_at,
                   updated_by=EXCLUDED.updated_by""",
            (template, datetime.now(timezone.utc), updated_by),
        )


def reset_to_default() -> None:
    init_schema()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM agent_prompt WHERE id=1")
