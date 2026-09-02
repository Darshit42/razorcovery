"""Typed shapes for the intake workflow."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Canonical fields we try to fill from an uploaded sheet. `phone` is the
# only hard requirement; the rest have sensible fallbacks.
CANONICAL_FIELDS = {
    "phone": ["phone", "phone_number", "mobile", "number", "contact", "msisdn"],
    "customer_name": ["name", "customer", "customer_name", "full_name"],
    "amount_inr": ["amount", "amount_inr", "value", "outstanding", "due", "invoice_amount"],
    "failure_type": ["failure_type", "type", "reason", "category"],
    "reference_id": ["reference_id", "ref", "reference", "order_id", "payment_id",
                     "invoice_id", "subscription_id", "txn_id"],
    "error_code": ["error_code", "error", "failure_code", "decline_reason"],
    "timezone": ["timezone", "tz", "time_zone"],
}


@dataclass
class RowError:
    row: int            # 1-based sheet row (excluding header)
    field: str | None
    message: str


@dataclass
class ParsedRow:
    row_index: int
    phone: str                       # normalized +91XXXXXXXXXX
    customer_name: str
    amount_inr: int
    failure_type: str                # payment_retry | checkout_abandonment | mandate_failure
    reference_id: str
    error_code: str
    timezone: str
    raw: dict[str, Any]              # the original sheet row


@dataclass
class ParseResult:
    columns: list[str]
    total_rows: int
    valid_rows: list[ParsedRow]
    errors: list[RowError]
    duplicate_phones: list[str]
    sample: list[dict[str, str]] = field(default_factory=list)
    detected_mapping: dict[str, str | None] = field(default_factory=dict)

    @property
    def valid_count(self) -> int:
        return len(self.valid_rows)

    @property
    def invalid_count(self) -> int:
        return self.total_rows - self.valid_count
