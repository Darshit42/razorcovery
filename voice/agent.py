"""LiveKit entrypoint for one recovery call.

Run as a worker:
    python -m voice.agent dev          # local, joins rooms on a dev server
    python -m voice.agent start        # production worker

The job metadata must be JSON: a serialised FailureEvent plus
{"attempt_number": N, "merchant": "..."}. `voice.dispatch` builds that.
"""
from __future__ import annotations

import asyncio
import json
import logging

from dotenv import load_dotenv

from audit import db
from audit.log import append_event
from data.schemas import FailureEvent
from decision.stopping_rules import check_stopping_rules
from voice import config
from voice.flow import RecoveryAgent
from voice.outcome import write_call_audit

load_dotenv()
logger = logging.getLogger("voice.agent")


def _parse_metadata(raw: str) -> tuple[FailureEvent, int, str]:
    data = json.loads(raw)
    meta = data.pop("_call", {})
    event = FailureEvent.model_validate(data)
    return event, int(meta.get("attempt_number", event.prior_attempts + 1)), meta.get(
        "merchant", "the merchant"
    )


async def entrypoint(ctx) -> None:  # ctx: livekit.agents.JobContext
    from datetime import datetime, timezone

    from livekit.agents import AgentSession
    from livekit.plugins.google.beta import realtime

    event, attempt_number, merchant = _parse_metadata(ctx.job.metadata or "{}")

    # Belt-and-suspenders: never dial past a stopping rule even if the
    # dispatcher already checked.
    stop = check_stopping_rules(
        attempts=event.prior_attempts,
        refused=event.refused,
        timezone=event.customer.timezone,
        now=datetime.now(timezone.utc),
        intervention="voice",
    )
    if stop.blocked:
        logger.warning("call aborted by stopping rule: %s", stop.rule)
        with db.get_conn() as conn:
            append_event(
                conn,
                event_id=event.event_id,
                customer_id=event.customer.id,
                entry_type="stopping_rule_triggered",
                failure_type=event.failure_type,
                intervention="voice",
                reason=f"[{stop.rule}] {stop.reason} (checked at dial time)",
                payload={"rule": stop.rule, "stage": "agent_entrypoint"},
            )
        return

    await ctx.connect()

    agent = RecoveryAgent(event, attempt_number=attempt_number, merchant=merchant)
    model = realtime.RealtimeModel(
        model=config.GEMINI_LIVE_MODEL,
        api_key=config.google_api_key(),
        voice=config.GEMINI_VOICE,
        language=config.GEMINI_LANGUAGE,
        temperature=0.6,
    )
    session = AgentSession(llm=model)

    @session.on("conversation_item_added")
    def _on_item(ev) -> None:
        item = getattr(ev, "item", ev)
        role = getattr(item, "role", "unknown")
        text = getattr(item, "text_content", None) or getattr(item, "content", "")
        agent.record_turn(role, text if isinstance(text, str) else str(text))

    await session.start(agent=agent, room=ctx.room)
    await session.generate_reply(
        instructions="Call ki shuruaat karo: apna intro do aur identity confirm karo."
    )

    try:
        await asyncio.wait_for(_wait_for_end(session, agent), timeout=config.MAX_CALL_DURATION_S)
    except asyncio.TimeoutError:
        agent.outcome.error = "max_call_duration_exceeded"
        logger.warning("call hit max duration")
    finally:
        await session.aclose()
        _finalise(event, agent)


async def _wait_for_end(session, agent: RecoveryAgent) -> None:
    """Resolve when end_call fired or the room emptied."""
    done = asyncio.Event()

    @session.on("close")
    def _c(_ev) -> None:
        done.set()

    while not done.is_set():
        if agent.outcome.result in ("recovered", "refused", "wrong_number", "declined"):
            # let the agent read its closing line, then stop
            await asyncio.sleep(3)
            return
        await asyncio.sleep(0.5)


def _finalise(event: FailureEvent, agent: RecoveryAgent) -> None:
    with db.get_conn() as conn:
        write_call_audit(lambda **kw: append_event(conn, **kw), event, agent.outcome)
    logger.info("call finalised: %s (%s)", event.event_id, agent.outcome.result)


def main() -> None:
    from livekit.agents import WorkerOptions, cli

    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))


if __name__ == "__main__":
    main()
