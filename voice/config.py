"""Voice-agent configuration. Call-behaviour limits that belong to the
agent (not the routing decision) live here."""
from __future__ import annotations

import os

# Gemini Live model + voice. Native-audio model handles Hinglish
# code-switching in a single model (STT + LLM + TTS). This is the
# AI-Studio (Gemini API key) variant; the "gemini-live-*" ids need Vertex.
GEMINI_LIVE_MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"
GEMINI_VOICE = "Aoede"          # warm female voice, works well for Hindi
GEMINI_LANGUAGE = "hi-IN"       # primary; the prompt allows English too

# Hard call limits (belt-and-suspenders over the decision layer).
MAX_CALL_DURATION_S = 240       # cut the call at 4 minutes no matter what
GREETING_TIMEOUT_S = 20         # if nobody speaks after connect, give up

# Retry-link time-to-live communicated to the customer.
RETRY_LINK_TTL_HOURS = 24


def google_api_key() -> str:
    key = os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError("GOOGLE_API_KEY is not set (see .env.example)")
    return key
