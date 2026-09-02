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
from datetime import date

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query
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


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


class Filters:
    """Query params shared by the HTML page and the JSON endpoints."""

    def __init__(self, failure_type=None, intervention=None, status=None, since=None, until=None):
        self.failure_type = failure_type if failure_type in compute.FAILURE_TYPES else None
        self.intervention = intervention if intervention in compute.INTERVENTIONS else None
        self.status = status if status in compute.STATUSES else None
        self.since = _parse_date(since)
        self.until = _parse_date(until)

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
) -> Filters:
    return Filters(failure_type, intervention, status, since, until)


@app.get("/", response_class=HTMLResponse)
def index(f: Filters = Depends(_filters)) -> str:
    rows = _rows()
    all_lcs = list(compute.reconstruct(rows).values())
    shown = f.apply(all_lcs)
    return templates.index_page(
        compute.summarise_lifecycles(shown),
        compute.exceptions(shown),
        shown,
        total_events=len(all_lcs),
        filters=f.as_dict(),
        filters_active=f.active,
        has_simulated=_has_simulated(shown),
    )


@app.get("/event/{event_id}", response_class=HTMLResponse)
def event_detail(event_id: str) -> str:
    lifecycles = compute.reconstruct(_rows())
    return templates.detail_page(lifecycles.get(event_id))


@app.get("/api/summary")
def api_summary(f: Filters = Depends(_filters)) -> dict:
    shown = f.apply(list(compute.reconstruct(_rows()).values()))
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
    shown = f.apply(list(compute.reconstruct(_rows()).values()))
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
