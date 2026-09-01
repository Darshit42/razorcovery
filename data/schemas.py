"""Shared data models for recovery events.

Modeled loosely on Razorpay webhook payloads (payment.failed,
order.paid absence, subscription.charged failure) but trimmed to only
what the decision layer needs. Synthetic only in this pass.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

FailureType = Literal["payment_retry", "checkout_abandonment", "mandate_failure"]

# Razorpay-style error codes we simulate, grouped by failure type.
ERROR_CODES: dict[str, list[str]] = {
    "payment_retry": [
        "BAD_REQUEST_PAYMENT_FAILED",
        "GATEWAY_ERROR",
        "insufficient_funds",
        "card_declined",
        "payment_authentication_failed",
    ],
    "checkout_abandonment": [
        "checkout_closed",
        "otp_not_entered",
        "user_dropped_upi_intent",
    ],
    "mandate_failure": [
        "mandate_revoked",
        "mandate_insufficient_funds",
        "mandate_bank_declined",
    ],
}


class Customer(BaseModel):
    id: str
    name: str
    phone: str
    # IANA timezone string; drives the call-window stopping-rule check.
    timezone: str = "Asia/Kolkata"


class FailureEvent(BaseModel):
    event_id: str
    created_at: datetime
    failure_type: FailureType
    customer: Customer
    amount_inr: int = Field(gt=0)
    # payment_id / order_id / subscription_id depending on failure_type
    reference_id: str
    error_code: str
    # prior *voice call* attempts already made for this customer
    prior_attempts: int = 0
    # customer has explicitly said "do not contact me again"
    refused: bool = False
