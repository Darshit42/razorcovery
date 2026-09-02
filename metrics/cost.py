"""Cost model for the recovery batch.

The LLM cost is computed from the *real* token usage captured on each
call (LiveKit's UsageCollector -> the outcome payload) priced at Gemini's
published list price. Telephony is a separate, honest line: no provider
is selected yet (PRD §10), so it contributes nothing until one is.
"""
from __future__ import annotations

# Gemini 2.5 Flash text pricing, USD per 1M tokens.
# Source: https://ai.google.dev/gemini-api/docs/pricing (2025 list price).
# Update here if the list price changes.
GEMINI_INPUT_USD_PER_MTOK = 0.30
GEMINI_OUTPUT_USD_PER_MTOK = 2.50
USD_INR = 88.0  # approximate spot rate; single place to adjust

# Telephony (SIP trunk, per-minute) — UNSET until a provider is chosen.
# Kept explicit rather than guessed so the number on the page is honest.
TELEPHONY_INR_PER_MIN: float | None = None

# Transactional SMS with a link (DLT template) — only used if we ever
# actually send one; there is no SMS provider wired yet.
SMS_INR_PER_MESSAGE: float | None = None


def llm_cost_inr(prompt_tokens: int, completion_tokens: int) -> float:
    usd = (
        prompt_tokens / 1_000_000 * GEMINI_INPUT_USD_PER_MTOK
        + completion_tokens / 1_000_000 * GEMINI_OUTPUT_USD_PER_MTOK
    )
    return round(usd * USD_INR, 4)


def telephony_cost_inr(total_call_seconds: float) -> float:
    if TELEPHONY_INR_PER_MIN is None:
        return 0.0
    return round(total_call_seconds / 60.0 * TELEPHONY_INR_PER_MIN, 2)


RATE_NOTE = (
    f"LLM cost = real token usage x Gemini 2.5 Flash list price "
    f"(${GEMINI_INPUT_USD_PER_MTOK}/${GEMINI_OUTPUT_USD_PER_MTOK} per 1M in/out, "
    f"USD/INR {USD_INR:.0f}). Telephony: no provider selected yet, so ₹0."
)
