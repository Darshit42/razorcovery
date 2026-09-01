"""Synthetic failure-event generator.

Produces 50+ events across the 3 failure types in PRD §3, with a
realistic prior-attempt distribution so the stopping rules in the
decision layer actually get exercised by the batch.

Run:  python -m data.generate_events [--count 60] [--seed 42] [--out data/fixtures/events.json]
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from faker import Faker

from data.schemas import ERROR_CODES, Customer, FailureEvent

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "events.json"

# Failure-type mix (roughly what a small merchant sees).
TYPE_WEIGHTS = {
    "payment_retry": 0.45,
    "checkout_abandonment": 0.35,
    "mandate_failure": 0.20,
}

# Amount bands (INR) per failure type — mandate charges skew small &
# recurring, checkout carts skew mid, one-off payments span widest.
AMOUNT_BANDS = {
    "payment_retry": (199, 25000),
    "checkout_abandonment": (299, 12000),
    "mandate_failure": (99, 4000),
}

# A few Indian-relevant timezones incl. one non-IST to prove the
# call-window check is timezone-aware, not hardcoded to IST.
TIMEZONES = ["Asia/Kolkata"] * 8 + ["Asia/Dubai", "Europe/London"]

# prior_attempts distribution: mostly fresh, some retried, a few maxed.
ATTEMPT_WEIGHTS = {0: 0.68, 1: 0.22, 2: 0.10}

REF_PREFIX = {
    "payment_retry": "pay_",
    "checkout_abandonment": "order_",
    "mandate_failure": "sub_",
}


def _weighted_choice(rng: random.Random, weights: dict):
    keys = list(weights)
    return rng.choices(keys, weights=[weights[k] for k in keys], k=1)[0]


def generate_events(count: int = 60, seed: int = 42) -> list[FailureEvent]:
    rng = random.Random(seed)
    fake = Faker("en_IN")
    fake.seed_instance(seed)

    events: list[FailureEvent] = []
    now = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)

    for i in range(count):
        ftype = _weighted_choice(rng, TYPE_WEIGHTS)
        lo, hi = AMOUNT_BANDS[ftype]
        amount = rng.randint(lo, hi)
        attempts = _weighted_choice(rng, ATTEMPT_WEIGHTS)
        # ~8% of customers have explicitly refused further contact.
        refused = rng.random() < 0.08

        customer = Customer(
            id=f"cust_{i:04d}",
            name=fake.name(),
            phone=fake.numerify("+9199########"),
            timezone=rng.choice(TIMEZONES),
        )
        event = FailureEvent(
            event_id=f"evt_{i:04d}",
            created_at=now - timedelta(hours=rng.randint(1, 72), minutes=rng.randint(0, 59)),
            failure_type=ftype,
            customer=customer,
            amount_inr=amount,
            reference_id=REF_PREFIX[ftype] + fake.lexify("??????????????"),
            error_code=rng.choice(ERROR_CODES[ftype]),
            prior_attempts=attempts,
            refused=refused,
        )
        events.append(event)

    return events


def write_fixture(events: list[FailureEvent], path: Path = FIXTURE_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [json.loads(e.model_dump_json()) for e in events]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_events(path: Path = FIXTURE_PATH) -> list[FailureEvent]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [FailureEvent.model_validate(r) for r in raw]


def _summary(events: list[FailureEvent]) -> str:
    by_type: dict[str, int] = {}
    for e in events:
        by_type[e.failure_type] = by_type.get(e.failure_type, 0) + 1
    maxed = sum(1 for e in events if e.prior_attempts >= 2)
    refused = sum(1 for e in events if e.refused)
    return (
        f"{len(events)} events | by type: {by_type} | "
        f"prior_attempts>=2: {maxed} | refused: {refused}"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=60)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=FIXTURE_PATH)
    args = ap.parse_args()

    events = generate_events(count=args.count, seed=args.seed)
    path = write_fixture(events, args.out)
    print(f"wrote {path}")
    print(_summary(events))


if __name__ == "__main__":
    main()
