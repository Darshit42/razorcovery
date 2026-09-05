"""Cost model for the recovery batch.

The LLM cost is computed from the *real* token usage captured on each
call (LiveKit's usage tracking -> the outcome payload) priced at Gemini's
published list price. Telephony is a separate, honest line: no provider
is selected yet (PRD §10), so it contributes nothing until one is.
"""
from __future__ import annotations

# gemini-2.5-flash-native-audio-preview-* (the Live API model this app
# actually calls) is priced on its *native audio* tier, not the plain
# Gemini 2.5 Flash text tier -- audio input/output run ~6x/4.8x the text
# rate. A live phone call is audio in, audio out for virtually its whole
# duration (the text system prompt is a rounding error against sustained
# real-time audio), so the audio rate is the honest number to apply to
# the totals this app tracks. USD per 1M tokens.
# Source: https://ai.google.dev/gemini-api/docs/pricing (native audio
# section for gemini-2.5-flash-native-audio-preview -- update here if the
# list price changes).
GEMINI_INPUT_USD_PER_MTOK = 3.00
GEMINI_OUTPUT_USD_PER_MTOK = 12.00
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
    f"LLM cost = real token usage x Gemini 2.5 Flash native-audio list price "
    f"(${GEMINI_INPUT_USD_PER_MTOK}/${GEMINI_OUTPUT_USD_PER_MTOK} per 1M in/out, "
    f"USD/INR {USD_INR:.0f}). Telephony: no provider selected yet, so ₹0."
)
