"""Call result -> audit rows.

The decision layer already logged `event_ingested` and `decision`. The
voice call adds:
  - `action`  : the call was placed (intervention='voice', attempt N)
  - `outcome` : how it ended, with the transcript in the payload
  - `stopping_rule_triggered` : if the customer refused *during* the call
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from data.schemas import FailureEvent

CallResult = Literal[
    "recovered",          # customer agreed, retry link sent
    "link_sent_no_commit",  # link sent, no verbal commitment
    "declined",           # soft no / not now
    "refused",            # hard "don't contact me again"
    "wrong_number",
    "no_answer",
    "failed",             # technical failure
]


@dataclass
class CallOutcome:
    result: CallResult
    attempt_number: int
    duration_s: float = 0.0
    retry_link_url: str | None = None
    consent_captured: bool = False
    refusal_captured: bool = False
    transcript: list[dict[str, str]] = field(default_factory=list)
    error: str | None = None
    # real LLM token usage for this call (from LiveKit's UsageCollector)
    prompt_tokens: int = 0
    completion_tokens: int = 0


def write_call_audit(sink, event: FailureEvent, outcome: CallOutcome) -> None:
    """Emit action + outcome (+ refusal) rows via an append_event-style sink."""
    sink(
        event_id=event.event_id,
        customer_id=event.customer.id,
        entry_type="action",
        failure_type=event.failure_type,
        intervention="voice",
        reason=None,
        amount_inr=event.amount_inr,
        attempt_number=outcome.attempt_number,
        payload={"channel": "voice", "attempt_number": outcome.attempt_number},
    )

    if outcome.refusal_captured:
        sink(
            event_id=event.event_id,
            customer_id=event.customer.id,
            entry_type="stopping_rule_triggered",
            failure_type=event.failure_type,
            intervention="voice",
            reason="[explicit_refusal] Customer refused further contact during the call.",
            amount_inr=event.amount_inr,
            payload={"rule": "explicit_refusal", "source": "in_call",
                     "blocks_all_contact": True},
        )

    sink(
        event_id=event.event_id,
        customer_id=event.customer.id,
        entry_type="outcome",
        failure_type=event.failure_type,
        intervention="voice",
        reason=f"call ended: {outcome.result}",
        amount_inr=event.amount_inr,
        attempt_number=outcome.attempt_number,
        payload={
            "result": outcome.result,
            "duration_s": round(outcome.duration_s, 1),
            "consent_captured": outcome.consent_captured,
            "refusal_captured": outcome.refusal_captured,
            "retry_link_url": outcome.retry_link_url,
            "error": outcome.error,
            "transcript": outcome.transcript,
            "prompt_tokens": outcome.prompt_tokens,
            "completion_tokens": outcome.completion_tokens,
            "ended_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def recovered_amount(event: FailureEvent, outcome: CallOutcome) -> int:
    """₹ credited to the recovery metric for this call. Conservative:
    only a verbal commitment + link sent counts as (potential) recovery;
    metrics later reconcile against an actual test-mode payment."""
    if outcome.result == "recovered":
        return event.amount_inr
    return 0
