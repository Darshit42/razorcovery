"""LiveKit worker: one job == one outbound recovery call.

Run:
    python -m voice.agent dev        # against a dev LiveKit server
    python -m voice.agent start      # production worker (registers as
                                     # agent_name so the dialer can
                                     # explicitly dispatch to it)

Job metadata is JSON: a serialised FailureEvent plus
  "_call": {"attempt_number": N, "merchant": "...", "dial": true}
When "dial" is set and SIP_OUTBOUND_TRUNK_ID is configured, the agent
rings the customer in over the outbound SIP trunk; otherwise it just
waits for someone to join the room (dev / console testing).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from dotenv import load_dotenv

from audit import db
from audit.log import append_event
from data.schemas import FailureEvent
from decision.stopping_rules import check_stopping_rules
from voice import config
from voice.dialer import dial_sip_participant
from voice.flow import RecoveryAgent
from voice.outcome import write_call_audit

load_dotenv()
logger = logging.getLogger("voice.agent")

AGENT_NAME = os.environ.get("LIVEKIT_AGENT_NAME", "razorcovery-agent")


def _parse_metadata(raw: str) -> tuple[FailureEvent, int, str, bool]:
    data = json.loads(raw)
    meta = data.pop("_call", {})
    event = FailureEvent.model_validate(data)
    return (
        event,
        int(meta.get("attempt_number", event.prior_attempts + 1)),
        meta.get("merchant", "the merchant"),
        bool(meta.get("dial", False)),
    )


async def entrypoint(ctx) -> None:  # ctx: livekit.agents.JobContext
    from datetime import datetime, timezone

    from livekit.agents import AgentSession
    from livekit.plugins.google.beta import realtime

    event, attempt_number, merchant, should_dial = _parse_metadata(ctx.job.metadata or "{}")

    # Never dial past a stopping rule, even if the dispatcher already checked.
    stop = check_stopping_rules(
        attempts=event.prior_attempts, refused=event.refused,
        timezone=event.customer.timezone, now=datetime.now(timezone.utc),
        intervention="voice",
    )
    if stop.blocked:
        logger.warning("call aborted by stopping rule: %s", stop.rule)
        with db.get_conn() as conn:
            append_event(
                conn, event_id=event.event_id, customer_id=event.customer.id,
                entry_type="stopping_rule_triggered", failure_type=event.failure_type,
                intervention="voice",
                reason=f"[{stop.rule}] {stop.reason} (checked at dial time)",
                payload={"rule": stop.rule, "stage": "agent_entrypoint",
                         "blocks_all_contact": stop.blocks_all_contact},
            )
        return

    await ctx.connect()

    agent = RecoveryAgent(event, attempt_number=attempt_number, merchant=merchant)
    model = realtime.RealtimeModel(
        model=config.GEMINI_LIVE_MODEL, api_key=config.google_api_key(),
        voice=config.GEMINI_VOICE, language=config.GEMINI_LANGUAGE, temperature=0.6,
    )
    session = AgentSession(llm=model)

    @session.on("conversation_item_added")
    def _on_item(ev) -> None:
        item = getattr(ev, "item", ev)
        role = getattr(item, "role", "unknown")
        text = getattr(item, "text_content", None) or getattr(item, "content", "")
        agent.record_turn(role, text if isinstance(text, str) else str(text))

    # --- ring the customer in -----------------------------------------
    trunk_id = os.environ.get("SIP_OUTBOUND_TRUNK_ID")
    if should_dial and trunk_id:
        try:
            answered = await dial_sip_participant(
                ctx, phone=event.customer.phone, trunk_id=trunk_id,
                caller_id=os.environ.get("SIP_CALLER_ID") or None,
            )
        except Exception as exc:  # SIP failure — log and stop
            logger.warning("SIP dial failed: %s", exc)
            answered = False
            agent.outcome.error = f"sip_dial_failed: {type(exc).__name__}"
        if not answered:
            agent.outcome.result = "no_answer"
            await session.aclose()
            _finalise(event, agent)
            return

    await session.start(agent=agent, room=ctx.room)
    await session.generate_reply(
        instructions="Call ki shuruaat karo: apna intro do aur identity confirm karo."
    )

    try:
        await asyncio.wait_for(_wait_for_end(session, agent),
                               timeout=config.MAX_CALL_DURATION_S)
    except asyncio.TimeoutError:
        agent.outcome.error = "max_call_duration_exceeded"
        logger.warning("call hit max duration")
    finally:
        await session.aclose()
        _finalise(event, agent)


async def _wait_for_end(session, agent: RecoveryAgent) -> None:
    done = asyncio.Event()

    @session.on("close")
    def _c(_ev) -> None:
        done.set()

    while not done.is_set():
        if agent.outcome.result in ("recovered", "refused", "wrong_number", "declined"):
            await asyncio.sleep(3)  # let the agent read its closing line
            return
        await asyncio.sleep(0.5)


def _finalise(event: FailureEvent, agent: RecoveryAgent) -> None:
    with db.get_conn() as conn:
        write_call_audit(lambda **kw: append_event(conn, **kw), event, agent.outcome)
    logger.info("call finalised: %s (%s)", event.event_id, agent.outcome.result)


def main() -> None:
    from livekit.agents import WorkerOptions, cli

    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name=AGENT_NAME))


if __name__ == "__main__":
    main()
