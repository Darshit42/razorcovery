"""Stopping rules (PRD §7). Enforced here in code, not described in a
comment. Every triggered rule is returned with a human-readable reason
so the caller can log it as a `stopping_rule_triggered` audit event.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from decision import config


@dataclass(frozen=True)
class StopDecision:
    blocked: bool
    rule: str | None = None       # 'explicit_refusal' | 'max_attempts' | 'call_window'
    reason: str | None = None
    # Does this rule block *all* contact, or just the voice call?
    blocks_all_contact: bool = False


def local_hour(now: datetime, timezone: str) -> int:
    if now.tzinfo is None:
        raise ValueError("`now` must be timezone-aware")
    return now.astimezone(ZoneInfo(timezone)).hour


def within_call_window(now: datetime, timezone: str) -> bool:
    h = local_hour(now, timezone)
    return config.CALL_WINDOW_START_HOUR <= h < config.CALL_WINDOW_END_HOUR


def check_stopping_rules(
    *,
    attempts: int,
    refused: bool,
    timezone: str,
    now: datetime,
    intervention: str,
) -> StopDecision:
    """Return whether the proposed `intervention` is allowed for this customer.

    Order matters: an explicit refusal outranks everything and blocks all
    channels; attempt/window limits only constrain voice calls.
    """
    if refused:
        return StopDecision(
            blocked=True,
            rule="explicit_refusal",
            reason="Customer has explicitly refused further contact; no channel permitted.",
            blocks_all_contact=True,
        )

    if intervention == "voice":
        if attempts >= config.MAX_ATTEMPTS:
            return StopDecision(
                blocked=True,
                rule="max_attempts",
                reason=(
                    f"Voice attempts ({attempts}) have reached the max "
                    f"({config.MAX_ATTEMPTS}); no further calls."
                ),
            )
        if not within_call_window(now, timezone):
            h = local_hour(now, timezone)
            return StopDecision(
                blocked=True,
                rule="call_window",
                reason=(
                    f"Local time is {h:02d}:00 in {timezone}, outside the "
                    f"{config.CALL_WINDOW_START_HOUR:02d}:00-"
                    f"{config.CALL_WINDOW_END_HOUR:02d}:00 call window."
                ),
            )

    return StopDecision(blocked=False)
