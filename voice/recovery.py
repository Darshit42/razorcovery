"""Retry-link generation, behind an interface.

This pass ships only the stub. The Razorpay test-mode implementation is
sketched but intentionally not wired (PRD §3: test-mode Payment Links
API comes in a later pass; no live money movement ever).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from data.schemas import FailureEvent
from voice import config


@dataclass(frozen=True)
class RetryLink:
    url: str
    expires_at: datetime
    provider: str
    reference_id: str


class RetryLinkProvider(Protocol):
    def create(self, event: FailureEvent) -> RetryLink: ...


class StubRetryLinkProvider:
    """Deterministic fake link. Safe for synthetic batches and demos."""

    provider = "stub"

    def create(self, event: FailureEvent) -> RetryLink:
        token = hashlib.sha256(
            f"{event.event_id}:{event.reference_id}".encode()
        ).hexdigest()[:16]
        expires = datetime.now(timezone.utc) + timedelta(
            hours=config.RETRY_LINK_TTL_HOURS
        )
        return RetryLink(
            url=f"https://pay.example-test.in/retry/{token}",
            expires_at=expires,
            provider=self.provider,
            reference_id=f"plink_stub_{token}",
        )


class RazorpayTestModeRetryLinkProvider:
    """Placeholder for the real Razorpay test-mode Payment Links call.

    Deliberately not implemented in this pass. Enabling it requires a
    test-mode key id/secret and must never accept live keys.
    """

    provider = "razorpay_test"

    def create(self, event: FailureEvent) -> RetryLink:  # pragma: no cover
        raise NotImplementedError(
            "Razorpay test-mode Payment Links integration is a later pass. "
            "Use StubRetryLinkProvider until then."
        )


def default_provider() -> RetryLinkProvider:
    return StubRetryLinkProvider()
