"""Metrics computed from the audit trail — the only source of truth
(PRD §6). Pure functions over lists of audit_log rows (dicts as returned
by audit.log.query); no DB access, no web concerns.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from metrics import cost

RECOVERED_RESULTS = {"recovered"}


# --------------------------------------------------------------------------
# Lifecycle reconstruction
# --------------------------------------------------------------------------
@dataclass
class EventLifecycle:
    event_id: str
    customer_id: str | None = None
    failure_type: str | None = None
    amount_inr: int | None = None
    decided_intervention: str | None = None
    decision_reason: str | None = None
    stopping_rules: list[dict[str, Any]] = field(default_factory=list)
    attempts: int = 0                       # count of 'action' rows
    call_seconds: float = 0.0
    result: str | None = None               # from the last 'outcome' row
    recovered_amount_inr: int = 0
    retry_link_url: str | None = None
    transcript: list[dict[str, str]] = field(default_factory=list)
    transcript_source: str | None = None
    timeline: list[dict[str, Any]] = field(default_factory=list)

    @property
    def contacted(self) -> bool:
        return self.attempts > 0 or self.decided_intervention in {"sms", "link_only"}

    @property
    def recovered(self) -> bool:
        return self.result in RECOVERED_RESULTS

    @property
    def blocked(self) -> bool:
        return self.decided_intervention == "none" or any(
            s.get("blocks_all_contact") for s in self.stopping_rules
        )


def reconstruct(rows: list[dict[str, Any]]) -> dict[str, EventLifecycle]:
    """Group audit rows by event_id into ordered lifecycles."""
    out: dict[str, EventLifecycle] = {}
    for r in sorted(rows, key=lambda x: x["id"]):
        eid = r["event_id"]
        lc = out.setdefault(eid, EventLifecycle(event_id=eid))
        lc.timeline.append(r)
        if r["customer_id"] and not lc.customer_id:
            lc.customer_id = r["customer_id"]
        if r["failure_type"] and not lc.failure_type:
            lc.failure_type = r["failure_type"]
        if r["amount_inr"] and lc.amount_inr is None:
            lc.amount_inr = r["amount_inr"]

        et = r["entry_type"]
        payload = r.get("payload") or {}
        if et == "decision":
            lc.decided_intervention = r["intervention"]
            lc.decision_reason = r["reason"]
        elif et == "stopping_rule_triggered":
            lc.stopping_rules.append(
                {
                    "rule": payload.get("rule"),
                    "reason": r["reason"],
                    "stage": payload.get("stage") or payload.get("source"),
                    "blocks_all_contact": payload.get("blocks_all_contact", False),
                }
            )
        elif et == "action":
            # 'attempts' = outbound *call* attempts (PRD §7); SMS/link
            # actions are tracked separately via decided_intervention.
            if r["intervention"] == "voice":
                lc.attempts += 1
        elif et == "outcome":
            lc.result = payload.get("result")
            lc.call_seconds += float(payload.get("duration_s") or 0)
            lc.retry_link_url = payload.get("retry_link_url") or lc.retry_link_url
            if payload.get("transcript"):
                lc.transcript = payload["transcript"]
            lc.transcript_source = payload.get("transcript_source", lc.transcript_source)
            if payload.get("result") in RECOVERED_RESULTS and lc.amount_inr:
                lc.recovered_amount_inr = lc.amount_inr
    return out


# --------------------------------------------------------------------------
# Aggregates
# --------------------------------------------------------------------------
@dataclass
class Bucket:
    key: str
    events: int = 0
    contacted: int = 0
    recovered: int = 0
    amount_at_risk_inr: int = 0
    amount_recovered_inr: int = 0

    @property
    def recovery_rate(self) -> float:
        base = self.contacted or self.events
        return round(self.recovered / base, 4) if base else 0.0

    @property
    def value_recovery_rate(self) -> float:
        return (
            round(self.amount_recovered_inr / self.amount_at_risk_inr, 4)
            if self.amount_at_risk_inr
            else 0.0
        )


def _bucketise(lifecycles, key_fn) -> list[Bucket]:
    buckets: dict[str, Bucket] = {}
    for lc in lifecycles:
        k = key_fn(lc) or "unknown"
        b = buckets.setdefault(k, Bucket(key=k))
        b.events += 1
        b.contacted += 1 if lc.contacted else 0
        b.recovered += 1 if lc.recovered else 0
        b.amount_at_risk_inr += lc.amount_inr or 0
        b.amount_recovered_inr += lc.recovered_amount_inr
    return sorted(buckets.values(), key=lambda b: -b.amount_at_risk_inr)


def recovery_by_intervention(lifecycles) -> list[Bucket]:
    return _bucketise(lifecycles, lambda lc: lc.decided_intervention)


def recovery_by_failure_type(lifecycles) -> list[Bucket]:
    return _bucketise(lifecycles, lambda lc: lc.failure_type)


@dataclass
class Effort:
    recovered: int
    total_attempts: int
    total_call_minutes: float
    total_cost_inr: float
    sms_sent: int
    links_only: int

    @property
    def attempts_per_recovery(self) -> float:
        return round(self.total_attempts / self.recovered, 2) if self.recovered else 0.0

    @property
    def call_minutes_per_recovery(self) -> float:
        return round(self.total_call_minutes / self.recovered, 2) if self.recovered else 0.0

    @property
    def cost_per_recovery_inr(self) -> float:
        return round(self.total_cost_inr / self.recovered, 2) if self.recovered else 0.0


def effort_per_recovery(lifecycles) -> Effort:
    lifecycles = list(lifecycles)
    recovered = sum(1 for lc in lifecycles if lc.recovered)
    attempts = sum(lc.attempts for lc in lifecycles)
    call_seconds = sum(lc.call_seconds for lc in lifecycles)
    sms = sum(1 for lc in lifecycles if lc.decided_intervention == "sms")
    links = sum(1 for lc in lifecycles if lc.decided_intervention == "link_only")
    total_cost = cost.voice_cost(call_seconds) + cost.sms_cost(sms)
    return Effort(
        recovered=recovered,
        total_attempts=attempts,
        total_call_minutes=round(call_seconds / 60.0, 2),
        total_cost_inr=round(total_cost, 2),
        sms_sent=sms,
        links_only=links,
    )


def exceptions(lifecycles) -> list[dict[str, Any]]:
    """Every event that did not recover, plus why. Nothing hidden."""
    rows = []
    for lc in lifecycles:
        if lc.recovered:
            continue
        why = None
        if lc.stopping_rules:
            why = "; ".join(s["reason"] for s in lc.stopping_rules)
        elif lc.result:
            why = f"call ended: {lc.result}"
        elif lc.decided_intervention in {"sms", "link_only"}:
            why = f"{lc.decided_intervention} sent, no confirmed payment"
        else:
            why = "no outcome recorded"
        rows.append(
            {
                "event_id": lc.event_id,
                "failure_type": lc.failure_type,
                "intervention": lc.decided_intervention,
                "amount_inr": lc.amount_inr,
                "result": lc.result,
                "blocked": lc.blocked,
                "why": why,
            }
        )
    return sorted(rows, key=lambda r: -(r["amount_inr"] or 0))


@dataclass
class Summary:
    total_events: int
    total_customers: int
    contacted: int
    recovered: int
    amount_at_risk_inr: int
    amount_recovered_inr: int
    by_intervention: list[Bucket]
    by_failure_type: list[Bucket]
    effort: Effort
    stopping_rule_counts: dict[str, int]
    exception_count: int

    @property
    def recovery_rate(self) -> float:
        base = self.contacted or self.total_events
        return round(self.recovered / base, 4) if base else 0.0


def summarise(rows: list[dict[str, Any]]) -> Summary:
    lifecycles = list(reconstruct(rows).values())
    stop_counts: dict[str, int] = {}
    for lc in lifecycles:
        for s in lc.stopping_rules:
            stop_counts[s["rule"]] = stop_counts.get(s["rule"], 0) + 1
    return Summary(
        total_events=len(lifecycles),
        total_customers=len({lc.customer_id for lc in lifecycles if lc.customer_id}),
        contacted=sum(1 for lc in lifecycles if lc.contacted),
        recovered=sum(1 for lc in lifecycles if lc.recovered),
        amount_at_risk_inr=sum(lc.amount_inr or 0 for lc in lifecycles),
        amount_recovered_inr=sum(lc.recovered_amount_inr for lc in lifecycles),
        by_intervention=recovery_by_intervention(lifecycles),
        by_failure_type=recovery_by_failure_type(lifecycles),
        effort=effort_per_recovery(lifecycles),
        stopping_rule_counts=stop_counts,
        exception_count=sum(1 for lc in lifecycles if not lc.recovered),
    )
