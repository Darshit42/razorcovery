"""Read-only metrics view over the audit trail.

    uvicorn metrics.app:app --port 8000
    # or: python -m metrics.app

Routes:
    GET /               HTML dashboard
    GET /event/{id}     HTML drill-down (facts + stopping rules + transcript + timeline)
    GET /api/summary    JSON
    GET /api/exceptions JSON
    GET /api/event/{id} JSON

No auth, no write paths. Everything is derived from audit_log at request time.
"""
from __future__ import annotations

import dataclasses

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from audit import db
from audit.log import query
from metrics import compute, templates

load_dotenv()
app = FastAPI(title="razorcovery metrics", docs_url=None, redoc_url=None)


def _rows():
    with db.get_conn() as conn:
        return query(conn)


def _has_simulated(lifecycles) -> bool:
    return any(lc.transcript_source == "simulated" for lc in lifecycles) or any(
        (r.get("payload") or {}).get("simulated") for lc in lifecycles for r in lc.timeline
    )


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    rows = _rows()
    lifecycles = list(compute.reconstruct(rows).values())
    summary = compute.summarise(rows)
    exceptions = compute.exceptions(lifecycles)
    return templates.index_page(
        summary, exceptions, lifecycles, has_simulated=_has_simulated(lifecycles)
    )


@app.get("/event/{event_id}", response_class=HTMLResponse)
def event_detail(event_id: str) -> str:
    lifecycles = compute.reconstruct(_rows())
    return templates.detail_page(lifecycles.get(event_id))


@app.get("/api/summary")
def api_summary() -> dict:
    s = compute.summarise(_rows())
    return dataclasses.asdict(s) | {
        "recovery_rate": s.recovery_rate,
        "by_intervention": [_bucket(b) for b in s.by_intervention],
        "by_failure_type": [_bucket(b) for b in s.by_failure_type],
        "effort": _effort(s.effort),
    }


@app.get("/api/exceptions")
def api_exceptions() -> list[dict]:
    lifecycles = list(compute.reconstruct(_rows()).values())
    return compute.exceptions(lifecycles)


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


def _bucket(b) -> dict:
    return dataclasses.asdict(b) | {
        "recovery_rate": b.recovery_rate,
        "value_recovery_rate": b.value_recovery_rate,
    }


def _effort(e) -> dict:
    return dataclasses.asdict(e) | {
        "attempts_per_recovery": e.attempts_per_recovery,
        "call_minutes_per_recovery": e.call_minutes_per_recovery,
        "cost_per_recovery_inr": e.cost_per_recovery_inr,
    }


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
