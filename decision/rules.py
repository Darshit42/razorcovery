"""Decision layer (PRD §3, §6).

One clear function per failure type maps an event to a *desired*
intervention with a logged reason. `route()` then applies the stopping
rules and downgrades or blocks as needed. Nothing here is a black box:
every branch produces a `reason` string.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from data.schemas import FailureEvent
from decision import config
from decision.stopping_rules import StopDecision, check_stopping_rules

Intervention = str  # 'voice' | 'sms' | 'link_only' | 'none'


@dataclass(frozen=True)
class InterventionDecision:
    intervention: Intervention
    reason: str
    attempt_number: int  # which voice attempt this would be (1-based); 0 if non-voice


@dataclass(frozen=True)
class RoutedDecision:
    intervention: Intervention
    reason: str
    attempt_number: int
    desired: Intervention          # what the failure-type rule wanted
    stop: StopDecision | None      # populated when a rule fired


# --- per-failure-type rules -------------------------------------------------

def _amount_tier(amount_inr: int) -> Intervention:
    if amount_inr >= config.VOICE_MIN_AMOUNT_INR:
        return "voice"
    if amount_inr >= config.SMS_MIN_AMOUNT_INR:
        return "sms"
    return "link_only"


def decide_payment_retry(event: FailureEvent) -> InterventionDecision:
    """A payment that failed after authorization intent. Highest recovery
    odds on a call when the amount justifies it; otherwise nudge."""
    tier = _amount_tier(event.amount_inr)
    next_attempt = event.prior_attempts + 1 if tier == "voice" else 0
    reason = (
        f"payment_retry, amount INR {event.amount_inr} -> {tier} "
        f"(error_code={event.error_code}, prior_attempts={event.prior_attempts})"
    )
    return InterventionDecision(tier, reason, next_attempt)


def decide_checkout_abandonment(event: FailureEvent) -> InterventionDecision:
    """Customer dropped before paying. Lower intent than a failed charge,
    so we only call for high-value carts; most get an SMS/link."""
    if event.amount_inr >= max(config.VOICE_MIN_AMOUNT_INR * 2, 3000):
        tier = "voice"
    elif event.amount_inr >= config.SMS_MIN_AMOUNT_INR:
        tier = "sms"
    else:
        tier = "link_only"
    next_attempt = event.prior_attempts + 1 if tier == "voice" else 0
    reason = (
        f"checkout_abandonment, cart INR {event.amount_inr} -> {tier} "
        f"(higher voice threshold for abandonment; error_code={event.error_code})"
    )
    return InterventionDecision(tier, reason, next_attempt)


def decide_mandate_failure(event: FailureEvent) -> InterventionDecision:
    """Recurring mandate charge failed. A revoked mandate needs the
    customer to re-authorize, which a link handles better than a call;
    insufficient-funds is worth a call for larger amounts."""
    if event.error_code == "mandate_revoked":
        tier = "link_only"
        reason = (
            "mandate_failure, mandate revoked -> link_only "
            "(customer must re-authorize the mandate; a call cannot fix this)"
        )
        return InterventionDecision(tier, reason, 0)

    tier = _amount_tier(event.amount_inr)
    next_attempt = event.prior_attempts + 1 if tier == "voice" else 0
    reason = (
        f"mandate_failure ({event.error_code}), amount INR {event.amount_inr} -> {tier}"
    )
    return InterventionDecision(tier, reason, next_attempt)


_DISPATCH = {
    "payment_retry": decide_payment_retry,
    "checkout_abandonment": decide_checkout_abandonment,
    "mandate_failure": decide_mandate_failure,
}


def decide_by_type(event: FailureEvent) -> InterventionDecision:
    return _DISPATCH[event.failure_type](event)


# --- routing = decision + stopping rules -----------------------------------

def route(event: FailureEvent, *, now: datetime, attempts_override: int | None = None) -> RoutedDecision:
    """Full routing for one event.

    `attempts_override` lets the batch runner pass a live count from the
    audit trail instead of trusting the event's static `prior_attempts`.
    """
    desired = decide_by_type(event)
    attempts = event.prior_attempts if attempts_override is None else attempts_override

    stop = check_stopping_rules(
        attempts=attempts,
        refused=event.refused,
        timezone=event.customer.timezone,
        now=now,
        intervention=desired.intervention,
    )

    if not stop.blocked:
        return RoutedDecision(
            intervention=desired.intervention,
            reason=desired.reason,
            attempt_number=desired.attempt_number,
            desired=desired.intervention,
            stop=None,
        )

    if stop.blocks_all_contact:
        return RoutedDecision(
            intervention="none",
            reason=f"All contact blocked. {stop.reason}",
            attempt_number=0,
            desired=desired.intervention,
            stop=stop,
        )

    # Voice-only block (max_attempts / call_window): fall back to SMS,
    # which is cheaper, non-intrusive, and still time-appropriate.
    return RoutedDecision(
        intervention="sms",
        reason=(
            f"Voice blocked by rule '{stop.rule}' -> downgraded to SMS. {stop.reason}"
        ),
        attempt_number=0,
        desired=desired.intervention,
        stop=stop,
    )
