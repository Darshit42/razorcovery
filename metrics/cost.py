"""Cost model for the recovery batch. One place, documented rates.

These are order-of-magnitude Indian test-market figures for the demo, not
a billing system. Tune with real numbers once the telephony provider and
LLM usage are known (PRD §11 budget).
"""
from __future__ import annotations

# Outbound voice: SIP trunk + STT/TTS/LLM per minute, rolled into one rate.
VOICE_COST_PER_MINUTE_INR = 4.0

# Transactional SMS with a link (DLT-registered template).
SMS_COST_PER_MESSAGE_INR = 0.20

# Payment link only (no notification sent by us).
LINK_ONLY_COST_INR = 0.0


def voice_cost(total_call_seconds: float) -> float:
    return round(total_call_seconds / 60.0 * VOICE_COST_PER_MINUTE_INR, 2)


def sms_cost(message_count: int) -> float:
    return round(message_count * SMS_COST_PER_MESSAGE_INR, 2)


RATE_NOTE = (
    f"voice ₹{VOICE_COST_PER_MINUTE_INR:.2f}/min · "
    f"SMS ₹{SMS_COST_PER_MESSAGE_INR:.2f}/msg · link ₹0 "
    "(demo estimates, see metrics/cost.py)"
)
