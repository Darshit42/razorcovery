"""Workspace-editable agent persona (the part of the system prompt a
vendor can tune). Single row for the *current* prompt (`agent_prompt`) --
this app is one shared workspace, not multi-tenant (PRD §4) -- plus an
append-only version history (`agent_prompt_version`) so a vendor can see
what changed and roll back.

Versions are stored as diffs, not full copies: the first customisation is
a full-text "base" row, and every save/reset/rollback after that stores
only the line-level edit against the previous version (via difflib). A
one-line change to an 8000-line prompt costs one line of storage, not a
second 8000-line copy. Reconstructing any past version replays the diff
chain from the base -- cheap here since it only runs from the settings
page (listing versions / rolling back), never on the per-call hot path,
which always reads the single current-row `agent_prompt.template`.
"""
from __future__ import annotations

import difflib
import json
from datetime import datetime, timezone
from typing import Any

from audit import db

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS agent_prompt (
    id         SMALLINT PRIMARY KEY DEFAULT 1,
    template   TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by TEXT,
    CONSTRAINT single_row CHECK (id = 1)
);

CREATE TABLE IF NOT EXISTS agent_prompt_version (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by TEXT,
    kind       TEXT NOT NULL CHECK (kind IN ('base', 'edit', 'reset', 'rollback')),
    full_text  TEXT,
    diff_ops   JSONB,
    note       TEXT
);
"""


def init_schema() -> None:
    with db.get_conn() as conn:
        conn.execute(SCHEMA_SQL)


# --------------------------------------------------------------------------
# Diff engine -- line-level, pure Python, no external patch dependency.
# --------------------------------------------------------------------------
def _lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def make_diff(old: str, new: str) -> list[dict[str, Any]]:
    """Opcodes to turn `old` into `new`. 'equal' spans reference the old
    text by index (nothing duplicated); 'insert'/'replace' carry the new
    lines inline; 'delete' carries nothing."""
    old_lines, new_lines = _lines(old), _lines(new)
    sm = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    ops: list[dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            ops.append({"tag": "equal", "i1": i1, "i2": i2})
        else:
            ops.append({"tag": tag, "i1": i1, "i2": i2, "lines": new_lines[j1:j2]})
    return ops


def apply_diff(old: str, ops: list[dict[str, Any]]) -> str:
    old_lines = _lines(old)
    out: list[str] = []
    for op in ops:
        if op["tag"] == "equal":
            out.extend(old_lines[op["i1"]:op["i2"]])
        else:
            out.extend(op.get("lines") or [])
    return "".join(out)


def _diff_stats(ops: list[dict[str, Any]]) -> tuple[int, int]:
    """(lines_added, lines_removed) without reconstructing any full text."""
    added = sum(len(op["lines"]) for op in ops if op["tag"] in ("insert", "replace"))
    removed = sum(op["i2"] - op["i1"] for op in ops if op["tag"] in ("delete", "replace"))
    return added, removed


# --------------------------------------------------------------------------
# Version chain
# --------------------------------------------------------------------------
def _all_version_rows(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, created_at, updated_by, kind, full_text, diff_ops, note "
        "FROM agent_prompt_version ORDER BY id"
    ).fetchall()
    return [
        {
            "id": r[0], "created_at": r[1], "updated_by": r[2], "kind": r[3],
            "full_text": r[4], "diff_ops": r[5], "note": r[6],
        }
        for r in rows
    ]


def _reconstruct(rows: list[dict[str, Any]], up_to_id: int | None = None) -> str | None:
    text: str | None = None
    for r in rows:
        text = r["full_text"] if r["kind"] == "base" else apply_diff(text or "", r["diff_ops"])
        if up_to_id is not None and r["id"] == up_to_id:
            return text
    return text if up_to_id is None else None


def list_versions(limit: int = 50) -> list[dict[str, Any]]:
    """Newest first, with cheap +added/-removed line stats -- no full-text
    reconstruction needed just to render the history list."""
    init_schema()
    with db.get_conn() as conn:
        rows = _all_version_rows(conn)
    out = []
    for r in reversed(rows):
        if r["kind"] == "base":
            added, removed = len(_lines(r["full_text"] or "")), 0
        else:
            added, removed = _diff_stats(r["diff_ops"])
        out.append({
            "id": r["id"], "created_at": r["created_at"], "updated_by": r["updated_by"],
            "kind": r["kind"], "note": r["note"], "lines_added": added, "lines_removed": removed,
        })
    return out[:limit]


def get_version_text(version_id: int) -> str | None:
    init_schema()
    with db.get_conn() as conn:
        rows = _all_version_rows(conn)
    return _reconstruct(rows, up_to_id=version_id)


def delete_version(version_id: int) -> None:
    """For test cleanup only -- the app itself never deletes/updates a
    version row once written."""
    with db.get_conn() as conn:
        conn.execute("DELETE FROM agent_prompt_version WHERE id=%s", (version_id,))


# --------------------------------------------------------------------------
# Current prompt
# --------------------------------------------------------------------------
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


def _current_or_default(conn, rows: list[dict[str, Any]]) -> str:
    from voice.prompt import DEFAULT_TEMPLATE

    row = conn.execute("SELECT template FROM agent_prompt WHERE id=1").fetchone()
    if row:
        return row[0]
    chained = _reconstruct(rows)
    return chained if chained is not None else DEFAULT_TEMPLATE


def _insert_version(conn, **fields) -> int:
    row = conn.execute(
        "INSERT INTO agent_prompt_version (updated_by, kind, full_text, diff_ops, note) "
        "VALUES (%(updated_by)s, %(kind)s, %(full_text)s, %(diff_ops)s, %(note)s) RETURNING id",
        {
            "updated_by": fields.get("updated_by"),
            "kind": fields["kind"],
            "full_text": fields.get("full_text"),
            "diff_ops": json.dumps(fields["diff_ops"]) if fields.get("diff_ops") is not None else None,
            "note": fields.get("note"),
        },
    ).fetchone()
    return row[0]


def set_template(template: str, *, updated_by: str) -> int | None:
    """Save a new prompt. Returns the new version id, or None if the text
    is unchanged from the current one (no-op, nothing written)."""
    template = (template or "").strip()
    if not template:
        raise ValueError("Prompt can't be empty.")
    init_schema()
    with db.get_conn() as conn:
        rows = _all_version_rows(conn)
        baseline = _current_or_default(conn, rows)
        version_id = None
        if template != baseline:
            if not rows:
                version_id = _insert_version(conn, kind="base", full_text=template, updated_by=updated_by)
            else:
                version_id = _insert_version(
                    conn, kind="edit", diff_ops=make_diff(baseline, template), updated_by=updated_by,
                )
        conn.execute(
            """INSERT INTO agent_prompt (id, template, updated_at, updated_by)
               VALUES (1, %s, %s, %s)
               ON CONFLICT (id) DO UPDATE
               SET template=EXCLUDED.template, updated_at=EXCLUDED.updated_at,
                   updated_by=EXCLUDED.updated_by""",
            (template, datetime.now(timezone.utc), updated_by),
        )
    return version_id


def reset_to_default(*, updated_by: str | None = None) -> int | None:
    from voice.prompt import DEFAULT_TEMPLATE

    init_schema()
    with db.get_conn() as conn:
        rows = _all_version_rows(conn)
        baseline = _current_or_default(conn, rows)
        version_id = None
        if baseline != DEFAULT_TEMPLATE:
            version_id = _insert_version(
                conn, kind="reset", diff_ops=make_diff(baseline, DEFAULT_TEMPLATE),
                updated_by=updated_by, note="Reset to default",
            )
        conn.execute("DELETE FROM agent_prompt WHERE id=1")
    return version_id


def rollback_to(version_id: int, *, updated_by: str) -> int:
    """Restore a past version as the current prompt. Recorded as a new
    forward version (a diff from the current text to the restored one) --
    history is append-only, a rollback is never a rewrite of the past."""
    init_schema()
    with db.get_conn() as conn:
        rows = _all_version_rows(conn)
        target = _reconstruct(rows, up_to_id=version_id)
        if target is None:
            raise ValueError(f"unknown prompt version: {version_id}")
        baseline = _current_or_default(conn, rows)
        new_id = _insert_version(
            conn, kind="rollback", diff_ops=make_diff(baseline, target),
            updated_by=updated_by, note=f"Rolled back to version {version_id}",
        )
        conn.execute(
            """INSERT INTO agent_prompt (id, template, updated_at, updated_by)
               VALUES (1, %s, %s, %s)
               ON CONFLICT (id) DO UPDATE
               SET template=EXCLUDED.template, updated_at=EXCLUDED.updated_at,
                   updated_by=EXCLUDED.updated_by""",
            (target, datetime.now(timezone.utc), updated_by),
        )
    return new_id
