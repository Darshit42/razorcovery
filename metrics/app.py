"""Metrics view + merchant intake workflow + accounts.

    uvicorn metrics.app:app --port 8000

Every page requires a signed-in account (auth/). Data comes only from
real use — uploads and real calls; there is no synthetic seeding.
"""
from __future__ import annotations

import asyncio
import csv as _csv
import dataclasses
import io
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from audit import db
from audit.log import append_event, query
from auth import service as auth_service
from intake import parse as intake_parse
from intake import store as intake_store
from metrics import _ctx, compute, templates

_REPO_ROOT = Path(__file__).resolve().parent.parent

load_dotenv()
app = FastAPI(title="razorcovery", docs_url=None, redoc_url=None)

_PUBLIC_PATHS = {"/login", "/signup", "/logout", "/favicon.ico"}


@app.middleware("http")
async def _require_login(request: Request, call_next):
    path = request.url.path
    if path in _PUBLIC_PATHS:
        return await call_next(request)
    try:
        user = auth_service.session_user(request.cookies.get(auth_service.SESSION_COOKIE))
    except Exception:  # DB blip — fail closed to the login page
        user = None
    if user is None:
        nxt = "" if path == "/" else f"?next={path}"
        return RedirectResponse(f"/login{nxt}", status_code=303)
    tok = _ctx.current_email.set(user.email)
    try:
        return await call_next(request)
    finally:
        _ctx.current_email.reset(tok)


@app.get("/login", response_class=HTMLResponse)
def login_form(next: str = "/", error: str = "") -> str:
    try:
        first_run = auth_service.user_count() == 0
    except Exception:
        first_run = False
    return templates.login_page(next=next, error=error, first_run=first_run)


@app.post("/login")
def login(email: str = Form(...), password: str = Form(...),
          next: str = Form("/")) -> RedirectResponse:
    try:
        token = auth_service.log_in(email, password)
    except auth_service.AuthError as exc:
        return RedirectResponse(f"/login?error={exc}", status_code=303)
    resp = RedirectResponse(next or "/", status_code=303)
    resp.set_cookie(auth_service.SESSION_COOKIE, token, httponly=True,
                    samesite="lax", max_age=14 * 24 * 3600)
    return resp


@app.get("/signup", response_class=HTMLResponse)
def signup_form(error: str = "") -> str:
    return templates.signup_page(error=error)


@app.post("/signup")
def signup(email: str = Form(...), password: str = Form(...),
           name: str = Form("")) -> RedirectResponse:
    try:
        auth_service.sign_up(email, password, name)
        token = auth_service.log_in(email, password)
    except auth_service.AuthError as exc:
        return RedirectResponse(f"/signup?error={exc}", status_code=303)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(auth_service.SESSION_COOKIE, token, httponly=True,
                    samesite="lax", max_age=14 * 24 * 3600)
    return resp


@app.post("/logout")
def logout(request: Request) -> RedirectResponse:
    auth_service.log_out(request.cookies.get(auth_service.SESSION_COOKIE))
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth_service.SESSION_COOKIE)
    return resp


_ROW_COLS = ["id", "ts", "event_id", "customer_id", "entry_type", "failure_type",
             "intervention", "reason", "amount_inr", "attempt_number", "payload"]


_TEST_EVENT_PREFIXES = ("pytest_", "smoketest_")


def _is_test_artifact(event_id: str) -> bool:
    """pytest's own fixtures (and one-off manual smoke checks) write
    directly-into-the-shared-DB rows with these prefixes (see
    tests/conftest.py) — hide them from the web UI. Nothing is deleted;
    this is a display filter only."""
    return event_id.startswith(_TEST_EVENT_PREFIXES)


def _rows(batch: str | None = None):
    with db.get_conn() as conn:
        if batch:
            raw = conn.execute(
                f"SELECT {', '.join(_ROW_COLS)} FROM audit_log "
                "WHERE payload->>'batch_id' = %s ORDER BY id",
                (batch,),
            ).fetchall()
            rows = [dict(zip(_ROW_COLS, r)) for r in raw]
        else:
            rows = query(conn)
    return [r for r in rows if not _is_test_artifact(r["event_id"])]


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


class Filters:
    """Query params shared by the HTML page and the JSON endpoints."""

    def __init__(self, failure_type=None, intervention=None, status=None,
                 since=None, until=None, batch=None):
        self.failure_type = failure_type if failure_type in compute.FAILURE_TYPES else None
        self.intervention = intervention if intervention in compute.INTERVENTIONS else None
        self.status = status if status in compute.STATUSES else None
        self.since = _parse_date(since)
        self.until = _parse_date(until)
        self.batch = batch or None

    def apply(self, lifecycles):
        return compute.filter_lifecycles(
            lifecycles,
            failure_type=self.failure_type,
            intervention=self.intervention,
            status=self.status,
            since=self.since,
            until=self.until,
        )

    def as_dict(self) -> dict:
        return {
            "failure_type": self.failure_type or "",
            "intervention": self.intervention or "",
            "status": self.status or "",
            "since": self.since.isoformat() if self.since else "",
            "until": self.until.isoformat() if self.until else "",
            "batch": self.batch or "",
        }

    @property
    def active(self) -> bool:
        return any(self.as_dict().values())


def _filters(
    failure_type: str | None = Query(None),
    intervention: str | None = Query(None),
    status: str | None = Query(None),
    since: str | None = Query(None),
    until: str | None = Query(None),
    batch: str | None = Query(None),
) -> Filters:
    return Filters(failure_type, intervention, status, since, until, batch)


@app.get("/", response_class=HTMLResponse)
def index(f: Filters = Depends(_filters)) -> str:
    rows = _rows(batch=f.batch)
    all_lcs = list(compute.reconstruct(rows).values())
    shown = f.apply(all_lcs)
    return templates.index_page(
        compute.summarise_lifecycles(shown),
        compute.exceptions(shown),
        shown,
        total_events=len(all_lcs),
        filters=f.as_dict(),
        filters_active=f.active,
    )


@app.get("/event/{event_id}", response_class=HTMLResponse)
def event_detail(event_id: str) -> str:
    lifecycles = compute.reconstruct(_rows())
    return templates.detail_page(lifecycles.get(event_id))


@app.post("/event/{event_id}/status")
def set_manual_status(event_id: str, status: str = Form(...)) -> RedirectResponse:
    """Update the manual recovery status for an event."""
    if status != "unset" and status not in compute.MANUAL_STATUSES:
        raise HTTPException(400, "invalid status")
    lifecycles = compute.reconstruct(_rows())
    lc = lifecycles.get(event_id)
    if lc is None:
        raise HTTPException(404, "unknown event")

    email = _ctx.current_email.get() or "unknown"
    label = "cleared (back to automatic)" if status == "unset" else f"set to '{status}'"
    with db.get_conn() as conn:
        append_event(
            conn,
            event_id=event_id,
            customer_id=lc.customer_id or "unknown",
            entry_type="manual_status",
            failure_type=lc.failure_type,
            reason=f"Recovery status manually {label} by {email}.",
            payload={"manual_status": status, "updated_by": email},
        )
    return RedirectResponse(f"/event/{event_id}", status_code=303)


@app.get("/calls", response_class=HTMLResponse)
def calls_page(f: Filters = Depends(_filters)) -> str:
    lcs = f.apply(list(compute.reconstruct(_rows(batch=f.batch)).values()))
    return templates.calls_page(compute.voice_calls(lcs), batch=f.batch)


@app.get("/api/calls")
def api_calls(f: Filters = Depends(_filters)) -> list[dict]:
    lcs = f.apply(list(compute.reconstruct(_rows(batch=f.batch)).values()))
    return compute.voice_calls(lcs)


@app.get("/api/summary")
def api_summary(f: Filters = Depends(_filters)) -> dict:
    shown = f.apply(list(compute.reconstruct(_rows(batch=f.batch)).values()))
    s = compute.summarise_lifecycles(shown)
    return dataclasses.asdict(s) | {
        "filters": f.as_dict(),
        "recovery_rate": s.recovery_rate,
        "by_intervention": [_bucket(b) for b in s.by_intervention],
        "by_failure_type": [_bucket(b) for b in s.by_failure_type],
        "effort": _effort(s.effort),
    }


@app.get("/api/exceptions")
def api_exceptions(f: Filters = Depends(_filters)) -> list[dict]:
    shown = f.apply(list(compute.reconstruct(_rows(batch=f.batch)).values()))
    return compute.exceptions(shown)


@app.get("/api/event/{event_id}")
def api_event(event_id: str) -> dict:
    lc = compute.reconstruct(_rows()).get(event_id)
    if lc is None:
        raise HTTPException(404, "unknown event")
    d = dataclasses.asdict(lc)
    d["recovered"] = lc.recovered
    d["blocked"] = lc.blocked
    d["contacted"] = lc.contacted
    return d


# ------------------------------------------------------------------ #
#  Intake workflow: upload -> batch -> run -> view / export
# ------------------------------------------------------------------ #

_MAX_UPLOAD = 5 * 1024 * 1024  # 5 MB


_SAMPLE_CSV = (
    "name,phone,amount,failure_type,error_code,reference_id\n"
    "Example Customer One,9800000001,2499,payment_retry,card_declined,ord_1001\n"
    "Example Customer Two,9800000002,899,checkout_abandonment,checkout_closed,ord_1002\n"
    "Example Customer Three,9800000003,1499,mandate_failure,mandate_insufficient_funds,sub_2001\n"
)


@app.get("/upload/sample.csv")
def upload_sample_csv() -> StreamingResponse:
    """A template sheet — not real data, just shows the expected columns."""
    return StreamingResponse(
        iter([_SAMPLE_CSV]), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="razorcovery_sample_sheet.csv"'},
    )


@app.get("/upload", response_class=HTMLResponse)
def upload_form() -> str:
    return templates.upload_page()


@app.post("/upload", response_model=None)
async def upload(
    file: UploadFile = File(...),
    merchant: str = Form(...),
    batch_name: str = Form(""),
    attest: str = Form(""),
    phone_column: str = Form(""),
    failure_type: str = Form(""),
):
    data = await file.read()
    if len(data) > _MAX_UPLOAD:
        raise HTTPException(413, "file too large (max 5 MB)")
    attested = attest in ("on", "true", "1", "yes")

    mapping = {"phone": phone_column} if phone_column else None
    result = intake_parse.parse_sheet(
        data, file.filename or "upload.csv",
        mapping=mapping, default_failure_type=(failure_type or None),
    )

    if not result.valid_rows or not attested:
        return HTMLResponse(templates.upload_preview_page(
            result, merchant=merchant, batch_name=batch_name,
            filename=file.filename or "", attested=attested,
            failure_type=failure_type,
        ))

    intake_store.init_schema()
    with db.get_conn() as conn:
        batch_id = intake_store.create_batch(
            conn, name=batch_name or (file.filename or "batch"),
            merchant=merchant, source_filename=file.filename or "",
            attested=True,
            attested_text=("Uploader attested these are the merchant's own "
                           "customers with a transaction relationship and consent."),
            rows=result.valid_rows,
            config={"call_window": [9, 19], "max_voice_attempts": 2},
        )
    return RedirectResponse(f"/batch/{batch_id}", status_code=303)


_PROCS: dict[str, subprocess.Popen] = {}


def _proc_alive(batch_id: str) -> bool:
    p = _PROCS.get(batch_id)
    return p is not None and p.poll() is None


@app.post("/batch/{batch_id}/run")
def batch_run(batch_id: str, now: str | None = Query(None)) -> RedirectResponse:
    with db.get_conn() as conn:
        batch = intake_store.get_batch(conn, batch_id)
    if not batch:
        raise HTTPException(404, "unknown batch")
    if not batch["attested"]:
        raise HTTPException(403, "batch not attested")
    if batch["status"] == "running" or _proc_alive(batch_id):
        return RedirectResponse(f"/batch/{batch_id}", status_code=303)

    # Run as a subprocess: LiveKit plugins must register on a process main
    # thread, which a uvicorn worker thread is not.
    cmd = [sys.executable, "-m", "intake.run_batch", batch_id]
    if now:
        cmd += ["--now", now]
    _PROCS[batch_id] = subprocess.Popen(cmd, cwd=str(_REPO_ROOT))
    return RedirectResponse(f"/batch/{batch_id}", status_code=303)


@app.get("/batch/{batch_id}", response_class=HTMLResponse)
def batch_page(batch_id: str) -> str:
    with db.get_conn() as conn:
        batch = intake_store.get_batch(conn, batch_id)
        if not batch:
            raise HTTPException(404, "unknown batch")
        rows = intake_store.batch_rows(conn, batch_id)
        progress = intake_store.batch_progress(conn, batch_id)
    return templates.batch_page(batch, rows, progress, running=_proc_alive(batch_id))


@app.get("/batch/{batch_id}/progress")
def batch_progress_json(batch_id: str) -> dict:
    with db.get_conn() as conn:
        batch = intake_store.get_batch(conn, batch_id)
        if not batch:
            raise HTTPException(404, "unknown batch")
        p = intake_store.batch_progress(conn, batch_id)
    p["status"] = batch["status"]
    p["running"] = _proc_alive(batch_id)
    return p


@app.get("/batch/{batch_id}/stream")
async def batch_stream(batch_id: str) -> StreamingResponse:
    async def gen():
        while True:
            with db.get_conn() as conn:
                batch = intake_store.get_batch(conn, batch_id)
                if not batch:
                    yield "event: error\ndata: unknown batch\n\n"
                    return
                p = intake_store.batch_progress(conn, batch_id)
            p["status"] = batch["status"]
            yield f"data: {json.dumps(p)}\n\n"
            if batch["status"] in ("done", "failed") and not _proc_alive(batch_id):
                return
            await asyncio.sleep(1.5)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/batch/{batch_id}/export.csv")
def batch_export_csv(batch_id: str) -> StreamingResponse:
    with db.get_conn() as conn:
        if not intake_store.get_batch(conn, batch_id):
            raise HTTPException(404, "unknown batch")
        rows = intake_store.batch_rows(conn, batch_id)
        lifecycles = compute.reconstruct(_rows(batch=batch_id))

    buf = io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["row", "event_id", "customer_name", "phone", "amount_inr",
                "failure_type", "reference_id", "decided_intervention",
                "status", "result", "recovered_amount_inr", "error"])
    for r in rows:
        lc = lifecycles.get(r["event_id"])
        w.writerow([
            r["row_index"], r["event_id"], r["customer_name"], r["phone"],
            r["amount_inr"], r["failure_type"], r["reference_id"],
            r["decided_intervention"] or "", r["status"], r["result"] or "",
            lc.recovered_amount_inr if lc else 0, r["error"] or "",
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{batch_id}.csv"'},
    )


@app.get("/batch/{batch_id}/export.json")
def batch_export_json(batch_id: str) -> dict:
    with db.get_conn() as conn:
        batch = intake_store.get_batch(conn, batch_id)
        if not batch:
            raise HTTPException(404, "unknown batch")
        rows = intake_store.batch_rows(conn, batch_id)
        lifecycles = compute.reconstruct(_rows(batch=batch_id))
    batch = dict(batch)
    batch["created_at"] = str(batch["created_at"])
    out_rows = []
    for r in rows:
        lc = lifecycles.get(r["event_id"])
        out_rows.append({
            "row_index": r["row_index"], "event_id": r["event_id"],
            "customer_name": r["customer_name"], "phone": r["phone"],
            "amount_inr": r["amount_inr"], "failure_type": r["failure_type"],
            "reference_id": r["reference_id"],
            "decided_intervention": r["decided_intervention"],
            "status": r["status"], "result": r["result"], "error": r["error"],
            "recovered_amount_inr": lc.recovered_amount_inr if lc else 0,
            "transcript": lc.transcript if lc else [],
        })
    return {"batch": batch, "rows": out_rows}


@app.get("/batches", response_class=HTMLResponse)
def batches_page() -> str:
    intake_store.init_schema()
    with db.get_conn() as conn:
        items = intake_store.list_batches(conn)
    return templates.batches_page(items)


# ------------------------------------------------------------------ #
#  Agent settings: the editable part of the voice agent's system prompt
# ------------------------------------------------------------------ #

@app.get("/settings", response_class=HTMLResponse)
def settings_page(saved: str = "", error: str = "") -> str:
    from voice import agent_prompt as prompt_store
    from voice.prompt import DEFAULT_TEMPLATE, GUARDRAILS

    meta = prompt_store.get_meta()
    template = meta["template"] if meta else DEFAULT_TEMPLATE
    return templates.settings_page(
        template=template, guardrails=GUARDRAILS,
        is_custom=meta is not None,
        updated_at=str(meta["updated_at"]) if meta else "",
        updated_by=(meta["updated_by"] or "") if meta else "",
        saved=(saved == "1"), error=error,
    )


@app.post("/settings/prompt")
def settings_save_prompt(request: Request, template: str = Form(...)) -> RedirectResponse:
    from voice import agent_prompt as prompt_store

    email = _ctx.current_email.get() or "unknown"
    try:
        prompt_store.set_template(template, updated_by=email)
    except ValueError as exc:
        return RedirectResponse(f"/settings?error={exc}", status_code=303)
    return RedirectResponse("/settings?saved=1", status_code=303)


@app.post("/settings/prompt/reset")
def settings_reset_prompt() -> RedirectResponse:
    from voice import agent_prompt as prompt_store

    prompt_store.reset_to_default()
    return RedirectResponse("/settings?saved=1", status_code=303)


def _bucket(b) -> dict:
    return dataclasses.asdict(b) | {
        "recovery_rate": b.recovery_rate,
        "value_recovery_rate": b.value_recovery_rate,
    }


def _effort(e) -> dict:
    return dataclasses.asdict(e) | {
        "total_cost_inr": e.total_cost_inr,
        "attempts_per_recovery": e.attempts_per_recovery,
        "call_minutes_per_recovery": e.call_minutes_per_recovery,
        "cost_per_recovery_inr": e.cost_per_recovery_inr,
        "tokens_per_recovery": e.tokens_per_recovery,
    }


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
