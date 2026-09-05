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

# Import LiveKit + the Google plugin at module level: plugins must register
# on the process main thread, which happens here, not inside the job task.
from google.genai import types as genai_types
from livekit.agents import AgentSession, WorkerOptions, cli
from livekit.plugins.google.beta import realtime

from audit import db
from audit.log import append_event
from data.schemas import FailureEvent
from decision.stopping_rules import check_stopping_rules
from voice import config
from voice.dialer import dial_sip_participant
from voice.flow import RecoveryAgent
from voice.outcome import write_call_audit
from voice.recording import recording_url, start_recording, stop_recording

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
    started_at = datetime.now(timezone.utc)

    agent = RecoveryAgent(event, attempt_number=attempt_number, merchant=merchant)
    model = realtime.RealtimeModel(
        model=config.GEMINI_LIVE_MODEL, api_key=config.google_api_key(),
        voice=config.GEMINI_VOICE, language=config.GEMINI_LANGUAGE, temperature=0.6,
        input_audio_transcription=genai_types.AudioTranscriptionConfig(),
        output_audio_transcription=genai_types.AudioTranscriptionConfig(),
        # Cut turn-taking latency: the default silence window before the
        # model decides the customer is done talking is noticeably laggy
        # on a live phone call. Shorter silence + higher-sensitivity
        # start/end-of-speech detection makes Priya respond right after
        # the customer stops, instead of a beat later.
        realtime_input_config=genai_types.RealtimeInputConfig(
            automatic_activity_detection=genai_types.AutomaticActivityDetection(
                start_of_speech_sensitivity=genai_types.StartSensitivity.START_SENSITIVITY_HIGH,
                end_of_speech_sensitivity=genai_types.EndSensitivity.END_SENSITIVITY_HIGH,
                prefix_padding_ms=100,
                silence_duration_ms=400,
            ),
        ),
    )
    session = AgentSession(llm=model)

    # transcript: both sides. The agent's own turns come through
    # conversation_item_added; the customer's come through
    # user_input_transcribed. Both fire for the user's side of the
    # conversation, so conversation_item_added skips role="user" here --
    # recording both was writing every customer line twice.
    @session.on("conversation_item_added")
    def _on_item(ev) -> None:
        item = getattr(ev, "item", ev)
        role = getattr(item, "role", "unknown")
        if role == "user":
            return
        text = getattr(item, "text_content", None) or getattr(item, "content", "")
        if isinstance(text, list):
            text = " ".join(str(x) for x in text)
        agent.record_turn(role, str(text))

    @session.on("user_input_transcribed")
    def _on_user(ev) -> None:
        if getattr(ev, "is_final", True) and getattr(ev, "transcript", ""):
            agent.record_turn("user", ev.transcript)

    # real token usage for cost tracking (metrics/cost.py). Was previously
    # never captured -- every call logged 0 tokens and so cost ₹0 no
    # matter how long it ran. session_usage_updated fires with a running
    # cumulative total each time it changes; the last one we see before
    # the call ends is the final tally.
    usage_holder: list = []

    @session.on("session_usage_updated")
    def _on_usage(ev) -> None:
        usage_holder[:] = [ev.usage]

    # --- ring the customer in -----------------------------------------
    trunk_id = os.environ.get("SIP_OUTBOUND_TRUNK_ID")
    if should_dial and trunk_id:
        try:
            answered = await dial_sip_participant(
                ctx, phone=event.customer.phone, trunk_id=trunk_id,
                caller_id=os.environ.get("SIP_CALLER_ID") or None,
            )
        except Exception as exc:
            logger.warning("SIP dial failed: %s", exc)
            answered = False
            agent.outcome.error = f"sip_dial_failed: {type(exc).__name__}"
        if not answered:
            agent.outcome.result = "no_answer"
            await session.aclose()
            _finalise(event, agent, started_at, None)
            return

    # the call is connected — default outcome for a call that just ends
    agent.outcome.result = "declined"

    egress_id = await start_recording(ctx, event.event_id)

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
        try:
            await session.aclose()
        except Exception:
            pass
        try:
            await stop_recording(egress_id)
        except Exception:
            pass
        _apply_usage(agent, usage_holder)
        _finalise(event, agent, started_at, egress_id)


def _apply_usage(agent: RecoveryAgent, usage_holder: list) -> None:
    if not usage_holder:
        return
    for u in usage_holder[0].model_usage:
        if getattr(u, "type", None) == "llm_usage":
            agent.outcome.prompt_tokens += u.input_tokens
            agent.outcome.completion_tokens += u.output_tokens


async def _wait_for_end(session, agent: RecoveryAgent) -> None:
    """Resolve when the customer hangs up, or shortly after a terminal
    tool (refusal / wrong-number / recovery) fires."""
    done = asyncio.Event()

    @session.on("close")
    def _c(_ev) -> None:
        done.set()

    while not done.is_set():
        if agent.call_ended_by_agent or agent.outcome.result in ("recovered", "refused", "wrong_number"):
            await asyncio.sleep(3)  # let the agent read its closing line
            return
        await asyncio.sleep(0.5)


def _finalise(event: FailureEvent, agent: RecoveryAgent, started_at, egress_id) -> None:
    from datetime import datetime, timezone

    if agent.outcome.duration_s <= 0 and agent.outcome.result != "no_answer":
        agent.outcome.duration_s = (
            datetime.now(timezone.utc) - started_at
        ).total_seconds()
    if egress_id:
        agent.outcome.recording_url = recording_url(event.event_id)

    # best-effort — a DB blip at the end of a call must not crash the job
    for attempt in range(4):
        try:
            with db.get_conn() as conn:
                write_call_audit(lambda **kw: append_event(conn, **kw), event, agent.outcome)
            logger.info("call finalised: %s (%s, %.0fs)",
                        event.event_id, agent.outcome.result, agent.outcome.duration_s)
            return
        except Exception as exc:  # noqa: BLE001
            if attempt < 3:
                import time as _t
                _t.sleep(2 * (attempt + 1))
            else:
                logger.error("could not write call outcome for %s: %s",
                             event.event_id, exc)


def main() -> None:
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name=AGENT_NAME))


if __name__ == "__main__":
    main()
