"""Run the recovery agent as a headless conversation (no room, no audio).

Used when there is no telephony provider: the real Gemini agent still
holds the Hinglish recovery conversation against a scripted synthetic
customer, and produces a real CallOutcome (tool-driven result, transcript,
token usage). With a provider wired, voice/agent.py handles the live call
instead and this is not used.
"""
from __future__ import annotations

import logging

from data.schemas import FailureEvent
from voice.flow import RecoveryAgent
from voice.outcome import CallOutcome

# Rotating synthetic-customer scripts. Most lead to a recovery (that is
# the agent's job when a customer genuinely wants to pay), a few don't —
# so a batch shows a realistic spread of outcomes.
PERSONAS: list[list[str]] = [
    ["Hello?", "Haan main hi bol raha hoon", "Achha, kya hua tha?",
     "Theek hai link bhej do, abhi kar deta hoon", "Thanks"],
    ["Haan bataiye", "Oh achha, payment fail ho gaya tha?",
     "Theek hai, link SMS kar dijiye main abhi karta hoon", "ok done"],
    ["Hello ji", "Card decline kyun hua tha?",
     "Achha samajh gaya, link bhej do", "ok kar diya thanks"],
    ["Haan?", "Kaunsa payment? ... achha wo wala",
     "Theek hai link bhej do", "ok"],
    ["Boliye", "Haan mujhe pata hai, karna tha but bhool gaya",
     "Abhi link bhejiye, main turant clear kar deta hoon", "done thank you"],
    ["Kaun bol raha hai?", "Ye number kisi aur ka tha shayad", "Nahi main wo nahi hoon"],
    ["Haan boliye", "Abhi thoda busy hoon, thodi der baad karta hoon", "Ok bye"],
    ["Hello", "Mujhe interest nahi hai, aur dobara call mat kijiye", "Bye"],
]

_QUIET = False


def _quiet_livekit() -> None:
    global _QUIET
    if _QUIET:
        return
    for name in ("livekit", "livekit.agents", "google_genai", "google.genai"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    _QUIET = True


async def converse(
    event: FailureEvent,
    *,
    attempt_number: int,
    persona: list[str],
    merchant: str = "the merchant",
) -> CallOutcome:
    _quiet_livekit()
    from livekit.agents import AgentSession
    from livekit.agents.metrics import UsageCollector
    from livekit.plugins import google

    agent = RecoveryAgent(event, attempt_number=attempt_number, merchant=merchant)
    session = AgentSession(llm=google.LLM(model="gemini-2.5-flash"))
    usage = UsageCollector()

    @session.on("metrics_collected")
    def _m(ev):
        try:
            usage.collect(ev.metrics)
        except Exception:
            pass

    await session.start(agent=agent)
    for line in persona:
        agent.record_turn("user", line)
        r = await session.run(user_input=line)
        say = " ".join(
            " ".join(e.item.content) if isinstance(e.item.content, list) else str(e.item.content)
            for e in r.events
            if type(e).__name__ == "ChatMessageEvent" and getattr(e.item, "role", "") == "assistant"
        )
        agent.record_turn("assistant", say)
        if agent.outcome.result in ("recovered", "refused", "wrong_number"):
            break
    await session.aclose()

    summ = usage.get_summary()
    agent.outcome.prompt_tokens = getattr(summ, "llm_prompt_tokens", 0)
    agent.outcome.completion_tokens = getattr(summ, "llm_completion_tokens", 0)
    agent.outcome.transcript_source = "real"
    return agent.outcome
