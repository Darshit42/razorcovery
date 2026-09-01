"""Run the decision layer over the synthetic batch and write the audit
trail to Postgres.

    python -m data.generate_events          # (re)build the fixture
    python -m audit.db                      # ensure schema
    python -m decision.run_batch            # route every event, log it

Voice/SMS/link execution is a later pass -- this stops at the logged
decision for each event.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone

from audit import db
from audit.log import voice_attempts
from data.generate_events import load_events
from decision.pipeline import bind_sink, process_event


def main(now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    events = load_events()
    outcomes: Counter[str] = Counter()
    blocked: Counter[str] = Counter()

    db.init_db()
    with db.get_conn() as conn:
        sink = bind_sink(conn)
        for event in events:
            live_attempts = event.prior_attempts + voice_attempts(conn, event.customer.id)
            decision = process_event(
                event, now=now, sink=sink, attempts_override=live_attempts
            )
            outcomes[decision.intervention] += 1
            if decision.stop is not None:
                blocked[decision.stop.rule] += 1

    print(f"processed {len(events)} events @ {now.isoformat()}")
    print(f"interventions: {dict(outcomes)}")
    print(f"stopping rules fired: {dict(blocked)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--now",
        help="ISO-8601 timestamp to route as (e.g. 2026-09-02T12:00+05:30). "
        "Defaults to real now; use a daytime value to exercise voice calls.",
    )
    args = ap.parse_args()
    override = datetime.fromisoformat(args.now) if args.now else None
    if override is not None and override.tzinfo is None:
        override = override.replace(tzinfo=timezone.utc)
    main(now=override)
