"""Metrics computed from the audit trail — the only source of truth
(PRD §6). Pure functions over lists of audit_log rows (dicts as returned
by audit.log.query); no DB access, no web concerns.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from metrics import cost

RECOVERED_RESULTS = {"recovered"}

# Manual recovery-status override (set from the event drill-down page).
# "unset" clears the override and reverts to the automatic call result.
MANUAL_STATUSES = ("pending", "paid", "failed", "disputed", "partial")


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
    retry_link_url: str | None = None
    recording_url: str | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    transcript: list[dict[str, str]] = field(default_factory=list)
    transcript_source: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    first_ts: Any = None          # datetime of the event_ingested row
    batch_id: str | None = None
    manual_status: str | None = None        # latest 'manual_status' row, if any
    timeline: list[dict[str, Any]] = field(default_factory=list)

    @property
    def contacted(self) -> bool:
        return self.attempts > 0 or self.decided_intervention in {"sms", "link_only"}

    @property
    def recovered(self) -> bool:
        """A manual 'paid' confirmation always wins — someone checked the
        bank statement — even if the call itself didn't end cleanly."""
        if self.manual_status == "paid":
            return True
        if self.manual_status in ("failed", "disputed"):
            return False
        return self.result in RECOVERED_RESULTS

    @property
    def recovered_amount_inr(self) -> int:
        return self.amount_inr or 0 if self.recovered else 0

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
        if r.get("ts") and lc.first_ts is None:
            lc.first_ts = r["ts"]
        if r["customer_id"] and not lc.customer_id:
            lc.customer_id = r["customer_id"]
        if r["failure_type"] and not lc.failure_type:
            lc.failure_type = r["failure_type"]
        if r["amount_inr"] and lc.amount_inr is None:
            lc.amount_inr = r["amount_inr"]

        et = r["entry_type"]
        payload = r.get("payload") or {}
        if payload.get("batch_id") and not lc.batch_id:
            lc.batch_id = payload["batch_id"]
        if et == "event_ingested":
            lc.customer_name = payload.get("customer_name") or lc.customer_name
            lc.customer_phone = payload.get("customer_phone") or lc.customer_phone
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
            lc.recording_url = payload.get("recording_url") or lc.recording_url
            if payload.get("transcript"):
                lc.transcript = payload["transcript"]
            lc.transcript_source = payload.get("transcript_source", lc.transcript_source)
            lc.prompt_tokens += int(payload.get("prompt_tokens") or 0)
            lc.completion_tokens += int(payload.get("completion_tokens") or 0)
        elif et == "manual_status":
            status = payload.get("manual_status")
            lc.manual_status = None if status == "unset" else status
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
    prompt_tokens: int
    completion_tokens: int
    llm_cost_inr: float
    telephony_cost_inr: float
    sms_sent: int
    links_only: int

    @property
    def total_cost_inr(self) -> float:
        return round(self.llm_cost_inr + self.telephony_cost_inr, 4)

    @property
    def attempts_per_recovery(self) -> float:
        return round(self.total_attempts / self.recovered, 2) if self.recovered else 0.0

    @property
    def call_minutes_per_recovery(self) -> float:
        return round(self.total_call_minutes / self.recovered, 2) if self.recovered else 0.0

    @property
    def cost_per_recovery_inr(self) -> float:
        return round(self.total_cost_inr / self.recovered, 4) if self.recovered else 0.0

    @property
    def tokens_per_recovery(self) -> int:
        tot = self.prompt_tokens + self.completion_tokens
        return round(tot / self.recovered) if self.recovered else 0


def effort_per_recovery(lifecycles) -> Effort:
    lifecycles = list(lifecycles)
    recovered = sum(1 for lc in lifecycles if lc.recovered)
    attempts = sum(lc.attempts for lc in lifecycles)
    call_seconds = sum(lc.call_seconds for lc in lifecycles)
    prompt_tok = sum(lc.prompt_tokens for lc in lifecycles)
    completion_tok = sum(lc.completion_tokens for lc in lifecycles)
    sms = sum(1 for lc in lifecycles if lc.decided_intervention == "sms")
    links = sum(1 for lc in lifecycles if lc.decided_intervention == "link_only")
    return Effort(
        recovered=recovered,
        total_attempts=attempts,
        total_call_minutes=round(call_seconds / 60.0, 2),
        prompt_tokens=prompt_tok,
        completion_tokens=completion_tok,
        llm_cost_inr=cost.llm_cost_inr(prompt_tok, completion_tok),
        telephony_cost_inr=cost.telephony_cost_inr(call_seconds),
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


def voice_calls(lifecycles) -> list[dict[str, Any]]:
    """One row per voice call attempt, newest first — for the Call Logs page."""
    from metrics import cost as _cost

    rows = []
    for lc in lifecycles:
        if lc.attempts == 0 and lc.decided_intervention != "voice":
            continue
        rows.append({
            "event_id": lc.event_id,
            "batch_id": lc.batch_id,
            "customer_name": lc.customer_name,
            "phone": lc.customer_phone,
            "ts": lc.first_ts.isoformat() if lc.first_ts is not None else None,
            "amount_inr": lc.amount_inr,
            "failure_type": lc.failure_type,
            "duration_s": round(lc.call_seconds, 1),
            "result": lc.result,
            "recovered": lc.recovered,
            "recovered_amount_inr": lc.recovered_amount_inr,
            "llm_cost_inr": _cost.llm_cost_inr(lc.prompt_tokens, lc.completion_tokens),
            "recording_url": lc.recording_url,
            "has_transcript": bool(lc.transcript),
        })
    rows.sort(key=lambda r: r["ts"] or "", reverse=True)
    return rows


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


FAILURE_TYPES = ("payment_retry", "checkout_abandonment", "mandate_failure")
INTERVENTIONS = ("voice", "sms", "link_only", "none")
STATUSES = ("recovered", "blocked", "open")


def filter_lifecycles(
    lifecycles,
    *,
    failure_type: str | None = None,
    intervention: str | None = None,
    status: str | None = None,
    since=None,
    until=None,
) -> list[EventLifecycle]:
    """Subset lifecycles by the dashboard filter controls. `since`/`until`
    are `date` objects compared against the event-ingest timestamp."""
    out = []
    for lc in lifecycles:
        if failure_type and lc.failure_type != failure_type:
            continue
        if intervention and lc.decided_intervention != intervention:
            continue
        if status == "recovered" and not lc.recovered:
            continue
        if status == "blocked" and not lc.blocked:
            continue
        if status == "open" and (lc.recovered or lc.blocked):
            continue
        d = lc.first_ts.date() if lc.first_ts is not None else None
        if since and (d is None or d < since):
            continue
        if until and (d is None or d > until):
            continue
        out.append(lc)
    return out


def summarise(rows: list[dict[str, Any]]) -> Summary:
    return summarise_lifecycles(list(reconstruct(rows).values()))


def summarise_lifecycles(lifecycles: list[EventLifecycle]) -> Summary:
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
