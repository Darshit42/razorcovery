"""Turn a routed 'voice' decision into an actual outbound call job.

This is the seam between the decision layer and the LiveKit worker.
Telephony provider is not chosen yet (PRD §10), so the real dialer is a
pluggable interface; the default one refuses to place a call and tells
you why. Nothing here contacts a real number.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Protocol

from data.schemas import FailureEvent
from decision.rules import RoutedDecision
from decision.stopping_rules import check_stopping_rules


class Dialer(Protocol):
    def place_call(self, *, room_name: str, phone: str, metadata: str) -> str:
        """Start the outbound call and return a call/job id."""
        ...


class UnconfiguredDialer:
    """Default. No telephony provider wired -> no call placed."""

    def place_call(self, *, room_name: str, phone: str, metadata: str) -> str:
        raise RuntimeError(
            "No telephony provider configured. Pick Exotel/Twilio/Plivo "
            "(PRD §10), implement a Dialer that creates a LiveKit SIP "
            "participant, and pass it to dispatch_call(dialer=...)."
        )


def build_job_metadata(event: FailureEvent, *, attempt_number: int, merchant: str) -> str:
    payload = json.loads(event.model_dump_json())
    payload["_call"] = {"attempt_number": attempt_number, "merchant": merchant}
    return json.dumps(payload)


def dispatch_call(
    event: FailureEvent,
    decision: RoutedDecision,
    *,
    now: datetime,
    sink,
    dialer: Dialer | None = None,
    merchant: str = "the merchant",
) -> dict:
    """Pre-flight the stopping rules, then place the call.

    Returns {'placed': bool, 'reason': str, 'call_id': str | None}.
    A blocked call is logged as `stopping_rule_triggered` and NOT placed.
    """
    if decision.intervention != "voice":
        return {"placed": False, "reason": f"decision is '{decision.intervention}', not voice",
                "call_id": None}

    stop = check_stopping_rules(
        attempts=event.prior_attempts,
        refused=event.refused,
        timezone=event.customer.timezone,
        now=now,
        intervention="voice",
    )
    if stop.blocked:
        sink(
            event_id=event.event_id,
            customer_id=event.customer.id,
            entry_type="stopping_rule_triggered",
            failure_type=event.failure_type,
            intervention="voice",
            reason=f"[{stop.rule}] {stop.reason} (checked at dispatch)",
            amount_inr=event.amount_inr,
            payload={"rule": stop.rule, "stage": "dispatch",
                     "blocks_all_contact": stop.blocks_all_contact},
        )
        return {"placed": False, "reason": stop.rule, "call_id": None}

    dialer = dialer or UnconfiguredDialer()
    room_name = f"recovery-{event.event_id}"
    metadata = build_job_metadata(event, attempt_number=decision.attempt_number, merchant=merchant)
    call_id = dialer.place_call(
        room_name=room_name, phone=event.customer.phone, metadata=metadata
    )
    return {"placed": True, "reason": "dialed", "call_id": call_id}
