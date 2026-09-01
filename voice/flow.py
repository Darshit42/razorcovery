"""The conversation agent: a LiveKit `Agent` with the recovery flow
expressed as function tools the model must call to take real actions.

Nothing the model *says* moves money or state; only these tools do, and
every one of them is logged. The refusal tool hard-ends the call.
"""
from __future__ import annotations

from datetime import datetime, timezone

from livekit.agents import Agent, RunContext, function_tool

from data.schemas import FailureEvent
from voice.outcome import CallOutcome
from voice.prompt import build_system_prompt
from voice.recovery import RetryLinkProvider, default_provider


class RecoveryAgent(Agent):
    """One instance per call. Holds call state + transcript."""

    def __init__(
        self,
        event: FailureEvent,
        *,
        attempt_number: int,
        merchant: str = "the merchant",
        link_provider: RetryLinkProvider | None = None,
    ) -> None:
        super().__init__(instructions=build_system_prompt(event, merchant=merchant))
        self.event = event
        self.merchant = merchant
        self._links = link_provider or default_provider()
        self.outcome = CallOutcome(result="no_answer", attempt_number=attempt_number)
        self._started = datetime.now(timezone.utc)

    # --- transcript capture ---------------------------------------------
    def record_turn(self, role: str, text: str) -> None:
        if text and text.strip():
            self.outcome.transcript.append({"role": role, "text": text.strip()})

    def _elapsed(self) -> float:
        return (datetime.now(timezone.utc) - self._started).total_seconds()

    # --- tools the model calls -----------------------------------------
    @function_tool
    async def send_retry_link(self, ctx: RunContext) -> str:
        """Call this once the customer agrees to complete the payment.
        Generates the secure retry link and (in production) sends it by SMS.
        Returns a short confirmation to read back to the customer."""
        link = self._links.create(self.event)
        self.outcome.retry_link_url = link.url
        self.outcome.consent_captured = True
        self.outcome.result = "recovered"
        self.outcome.duration_s = self._elapsed()
        return (
            f"Link bhej diya gaya hai (valid {link.expires_at:%d %b %H:%M} tak). "
            "Customer ko batao ki SMS check karein aur wahin se payment complete karein."
        )

    @function_tool
    async def offer_declined(self, ctx: RunContext, note: str = "") -> str:
        """Call this if the customer is not interested right now but has
        NOT asked to stop being contacted. A soft no."""
        self.outcome.result = "declined"
        self.outcome.duration_s = self._elapsed()
        if note:
            self.record_turn("system", f"declined: {note}")
        return "Theek hai, politely samjho aur call wrap up karo."

    @function_tool
    async def mark_do_not_contact(self, ctx: RunContext) -> str:
        """Call this the moment the customer clearly says do not call/contact
        me again, or is firmly refusing. After this, apologise briefly and
        end the call. No further persuasion."""
        self.outcome.result = "refused"
        self.outcome.refusal_captured = True
        self.outcome.duration_s = self._elapsed()
        return (
            "Customer ne further contact se mana kar diya. Sirf ek chhoti si "
            "apology do disturbance ke liye aur turant call band karo."
        )

    @function_tool
    async def wrong_person(self, ctx: RunContext) -> str:
        """Call this if the person on the line is not the customer, or the
        number is wrong."""
        self.outcome.result = "wrong_number"
        self.outcome.duration_s = self._elapsed()
        return "Galti se disturb karne ke liye sorry bolo aur call end karo."

    @function_tool
    async def end_call(self, ctx: RunContext) -> str:
        """Call this to hang up once the conversation is genuinely finished."""
        self.outcome.duration_s = self._elapsed()
        if self.outcome.result == "no_answer":
            # spoke to someone but no other tool fired
            self.outcome.result = "link_sent_no_commit" if self.outcome.retry_link_url else "declined"
        return "__END_CALL__"
