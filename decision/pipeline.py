"""Glue: event -> decision -> audit rows.

`process_event` is deliberately storage-agnostic. It takes a `sink`
callable with the same signature as `audit.log.append_event` (minus the
connection). The batch runner binds it to a real Postgres connection;
tests bind it to an in-memory list.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol

from data.schemas import FailureEvent
from decision.rules import RoutedDecision, route


class Sink(Protocol):
    def __call__(
        self,
        *,
        event_id: str,
        customer_id: str,
        entry_type: str,
        failure_type: str | None = ...,
        intervention: str | None = ...,
        reason: str | None = ...,
        amount_inr: int | None = ...,
        attempt_number: int | None = ...,
        payload: dict | None = ...,
    ) -> object: ...


def process_event(
    event: FailureEvent,
    *,
    now: datetime,
    sink: Sink,
    attempts_override: int | None = None,
) -> RoutedDecision:
    """Route one event and write its audit trail. Returns the decision.

    Emits, in order:
      event_ingested -> [stopping_rule_triggered]? -> decision
    The `action` and `outcome` rows are written later by whatever
    actually performs the intervention (voice agent / SMS stub), not here.
    """
    sink(
        event_id=event.event_id,
        customer_id=event.customer.id,
        entry_type="event_ingested",
        failure_type=event.failure_type,
        amount_inr=event.amount_inr,
        reason=None,
        payload={
            "error_code": event.error_code,
            "reference_id": event.reference_id,
            "prior_attempts": event.prior_attempts,
            "refused": event.refused,
            "timezone": event.customer.timezone,
            "customer_name": event.customer.name,
            "customer_phone": event.customer.phone,
            "created_at": event.created_at.isoformat(),
        },
    )

    decision = route(event, now=now, attempts_override=attempts_override)

    if decision.stop is not None:
        sink(
            event_id=event.event_id,
            customer_id=event.customer.id,
            entry_type="stopping_rule_triggered",
            failure_type=event.failure_type,
            intervention=decision.desired,
            reason=f"[{decision.stop.rule}] {decision.stop.reason}",
            amount_inr=event.amount_inr,
            payload={
                "rule": decision.stop.rule,
                "blocks_all_contact": decision.stop.blocks_all_contact,
                "desired_intervention": decision.desired,
                "now": now.isoformat(),
            },
        )

    sink(
        event_id=event.event_id,
        customer_id=event.customer.id,
        entry_type="decision",
        failure_type=event.failure_type,
        intervention=decision.intervention,
        reason=decision.reason,
        amount_inr=event.amount_inr,
        attempt_number=decision.attempt_number or None,
        payload={"desired_intervention": decision.desired},
    )

    return decision


def bind_sink(conn) -> Sink:
    """Adapt audit.log.append_event to the Sink signature."""
    from audit.log import append_event

    def _sink(**kwargs):
        return append_event(conn, **kwargs)

    return _sink
