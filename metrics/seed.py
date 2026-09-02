"""Make the audit trail demo-ready.

The decision layer only logs event -> decision. This adds the
action/outcome rows.

    python -m metrics.seed --reset --real-batch   # fresh random batch +
                                                  # real Gemini agent per call
    python -m metrics.seed --reset --simulate     # fresh batch + fast estimates
    python -m metrics.seed --append-real 5        # top up real transcripts

Every `--reset` regenerates `data/fixtures/events.json` with a random
seed and a random count (45-75) so each run is a genuinely different
dataset — different customers, amounts, failure mix. `--keep-events`
reuses the current fixture; `--gen-seed N` / `--count N` pin it.

`--real-batch` runs the actual Gemini agent (scripted synthetic
customers, no live telephony) for every non-blocked voice event and
records real token usage. SMS and link_only interventions have no real
delivery channel yet, so they get NO outcome row and show as
"unconfirmed". `--simulate` is the fast estimate model.
"""
from __future__ import annotations

import argparse
import random
from datetime import datetime, timezone

from audit import db
from audit.log import append_event
from data.generate_events import FIXTURE_PATH, generate_events, load_events, write_fixture
from decision.pipeline import bind_sink, process_event
from decision.rules import route


def regenerate_fixture(gen_seed: int | None, count: int | None) -> tuple[int, int]:
    """Build a fresh synthetic batch so every run is a different dataset.
    Returns (seed, count) actually used."""
    seed = random.randrange(1_000_000) if gen_seed is None else gen_seed
    n = random.randint(45, 75) if count is None else count
    write_fixture(generate_events(count=n, seed=seed))
    return seed, n

# Seeded outcome model. Probabilities are deliberately conservative and
# documented; they are demo estimates, not measured rates.
VOICE_OUTCOMES = [
    ("recovered", 0.42),
    ("link_sent_no_commit", 0.16),
    ("declined", 0.16),
    ("no_answer", 0.16),
    ("wrong_number", 0.05),
    ("refused", 0.05),
]
SMS_OUTCOMES = [("recovered", 0.22), ("link_sent_no_commit", 0.78)]
LINK_OUTCOMES = [("recovered", 0.12), ("link_sent_no_commit", 0.88)]

_TRANSCRIPTS = {
    "recovered": [
        {"role": "assistant", "text": "Namaste, main Priya bol rahi hoon merchant ki taraf se. Aapka payment fail ho gaya tha, main help karne ke liye call kar rahi hoon."},
        {"role": "user", "text": "Haan haan bataiye kya karna hai"},
        {"role": "assistant", "text": "Main ek secure payment link SMS pe bhej rahi hoon, aap 2 minute mein retry kar sakte ho. Card ya OTP kabhi phone pe mat share kijiye."},
        {"role": "user", "text": "Theek hai bhej do"},
        {"role": "assistant", "text": "Link bhej diya hai. SMS check kijiye aur wahin se complete kijiye. Dhanyavaad!"},
    ],
    "declined": [
        {"role": "assistant", "text": "Namaste, aapka ek payment fail hua tha, kya main retry link bhej doon?"},
        {"role": "user", "text": "Abhi nahi, baad mein dekhunga"},
        {"role": "assistant", "text": "Koi baat nahi. Link 24 ghante valid rahega jab aap chaaho. Dhanyavaad."},
    ],
    "refused": [
        {"role": "assistant", "text": "Namaste, main Priya bol rahi hoon, aapka payment fail hua tha..."},
        {"role": "user", "text": "Mujhe interest nahi hai, dobara call mat karna"},
        {"role": "assistant", "text": "Bilkul, disturb karne ke liye maafi. Aapko dobara contact nahi karenge. Dhanyavaad."},
    ],
    "link_sent_no_commit": [
        {"role": "assistant", "text": "Aapka payment fail hua tha, main ek retry link bhej rahi hoon."},
        {"role": "user", "text": "Ok"},
        {"role": "assistant", "text": "Bhej diya. Jab time mile complete kar dijiyega. Dhanyavaad."},
    ],
    "no_answer": [],
    "wrong_number": [
        {"role": "assistant", "text": "Namaste, kya main Rahul ji se baat kar rahi hoon?"},
        {"role": "user", "text": "Nahi, galat number hai"},
        {"role": "assistant", "text": "Maafi chahti hoon, disturb karne ke liye. Dhanyavaad."},
    ],
}


def _pick(rng: random.Random, table):
    r = rng.random()
    acc = 0.0
    for name, p in table:
        acc += p
        if r <= acc:
            return name
    return table[-1][0]


def _sink(conn):
    return lambda **kw: append_event(conn, **kw)


def reset(conn) -> None:
    conn.execute("TRUNCATE audit_log RESTART IDENTITY")


def run_decisions(conn, events, now) -> dict:
    sink = bind_sink(conn)
    routed = {}
    for e in events:
        routed[e.event_id] = process_event(e, now=now, sink=sink)
    return routed


def simulate_outcomes(conn, events, routed, seed: int, skip: set[str]) -> None:
    rng = random.Random(seed)
    sink = _sink(conn)
    for e in events:
        if e.event_id in skip:
            continue
        d = routed[e.event_id]
        if d.intervention == "none":
            continue
        ptok = ctok = 0
        if d.intervention == "voice":
            result = _pick(rng, VOICE_OUTCOMES)
            duration = 0.0 if result == "no_answer" else rng.uniform(35, 175)
            if result != "no_answer":
                # rough shape of a real 3-5 turn Gemini conversation
                ptok, ctok = rng.randint(2500, 8000), rng.randint(300, 1100)
        elif d.intervention == "sms":
            result, duration = _pick(rng, SMS_OUTCOMES), 0.0
        else:
            result, duration = _pick(rng, LINK_OUTCOMES), 0.0

        sink(
            event_id=e.event_id, customer_id=e.customer.id, entry_type="action",
            failure_type=e.failure_type, intervention=d.intervention, reason=None,
            amount_inr=e.amount_inr, attempt_number=d.attempt_number or 1,
            payload={"channel": d.intervention, "simulated": True},
        )
        if result == "refused":
            sink(
                event_id=e.event_id, customer_id=e.customer.id,
                entry_type="stopping_rule_triggered", failure_type=e.failure_type,
                intervention="voice",
                reason="[explicit_refusal] Customer refused further contact during the call.",
                amount_inr=e.amount_inr,
                payload={"rule": "explicit_refusal", "source": "in_call",
                         "blocks_all_contact": True, "simulated": True},
            )
        sink(
            event_id=e.event_id, customer_id=e.customer.id, entry_type="outcome",
            failure_type=e.failure_type, intervention=d.intervention,
            reason=f"call ended: {result}" if d.intervention == "voice" else f"{d.intervention}: {result}",
            amount_inr=e.amount_inr, attempt_number=d.attempt_number or 1,
            payload={
                "result": result,
                "duration_s": round(duration, 1),
                "consent_captured": result == "recovered",
                "refusal_captured": result == "refused",
                "retry_link_url": (
                    f"https://pay.example-test.in/retry/{e.event_id}" if result == "recovered" else None
                ),
                "transcript": _TRANSCRIPTS.get(result, []) if d.intervention == "voice" else [],
                "prompt_tokens": ptok,
                "completion_tokens": ctok,
                "transcript_source": "simulated",
                "ended_at": datetime.now(timezone.utc).isoformat(),
            },
        )


# Scripted synthetic customers. Rotated across the voice batch so the
# real conversations cover the range the agent must handle.
PERSONAS = [
    ["Hello?", "Haan main hi bol raha hoon", "Achha, kya hua tha?",
     "Theek hai link bhej do, abhi kar deta hoon", "Thanks"],
    ["Haan boliye", "Abhi thoda busy hoon, thodi der baad karta hoon", "Ok bye"],
    ["Hello", "Mujhe interest nahi hai, aur dobara call mat kijiye", "Bye"],
    ["Haan?", "Kaunsa payment? Mujhe yaad nahi aa raha", "Achha theek hai, link bhej do", "ok"],
    ["Hi", "Maine to already pay kar diya tha kal", "Achha, dobara check karta hoon. Bye"],
    ["Kaun bol raha hai?", "Ye number kisi aur ka tha shayad", "Nahi main wo nahi hoon"],
    ["Haan bataiye", "Amount thoda zyada lag raha hai, sure ho?",
     "Theek hai agar aisa hai to link bhej dijiye", "ok thanks"],
    ["Hello ji", "Card decline kyun hua?", "Achha samajh gaya, link SMS kar do", "ok done"],
]


def run_real_calls(conn, events, routed, n: int | None, *, retries: int = 1) -> set[str]:
    """Run the actual Gemini agent headless for voice events (all, or the
    first n). Failures are logged honestly as result='failed' — never
    silently replaced with a fake success."""
    import asyncio
    import logging

    from livekit.agents import AgentSession
    from livekit.plugins import google

    from voice.flow import RecoveryAgent
    from voice.outcome import write_call_audit

    # LiveKit logs a full traceback for every transient LLM retry even when
    # the retry then succeeds. Silence it; we report our own progress.
    for name in ("livekit", "livekit.agents", "google_genai", "google.genai"):
        logging.getLogger(name).setLevel(logging.CRITICAL)

    voice_events = [e for e in events if routed[e.event_id].intervention == "voice"]
    if n is not None:
        voice_events = voice_events[:n]

    from livekit.agents.metrics import UsageCollector

    async def one(event, persona):
        agent = RecoveryAgent(event, attempt_number=routed[event.event_id].attempt_number or 1,
                              merchant="ChaiPoint")
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
                " ".join(ev.item.content) if isinstance(ev.item.content, list) else str(ev.item.content)
                for ev in r.events
                if type(ev).__name__ == "ChatMessageEvent" and getattr(ev.item, "role", "") == "assistant"
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

    def _write(event, outcome):
        sink = _sink(conn)

        def tagged(**kw):
            if kw["entry_type"] == "outcome":
                kw["payload"]["transcript_source"] = "real"
            return sink(**kw)

        write_call_audit(tagged, event, outcome)

    async def runner():
        done, failed = set(), set()
        total = len(voice_events)
        for i, ev in enumerate(voice_events):
            persona = PERSONAS[i % len(PERSONAS)]
            outcome = None
            for attempt in range(retries + 1):
                try:
                    outcome = await one(ev, persona)
                    break
                except Exception as exc:
                    last = exc
                    if attempt < retries:
                        await asyncio.sleep(2)
            if outcome is None:
                # honest failure — no silent fallback to a fake success
                from voice.outcome import CallOutcome

                outcome = CallOutcome(
                    result="failed",
                    attempt_number=routed[ev.event_id].attempt_number or 1,
                    error=f"{type(last).__name__}: {last}"[:200],
                )
                outcome.transcript_source = "real"
                failed.add(ev.event_id)
            _write(ev, outcome)
            done.add(ev.event_id)
            print(f"  [{i + 1}/{total}] {ev.event_id}: {outcome.result}", flush=True)

            # Bail early if the API is clearly unavailable rather than
            # grinding through dozens of failures and committing garbage.
            if len(failed) >= 5 and len(failed) == len(done):
                raise SystemExit(
                    f"aborting: first {len(failed)} calls all failed "
                    "(Gemini quota / API unavailable). Nothing committed. "
                    "Try again later or use --simulate."
                )
        if failed:
            print(f"  {len(failed)} calls errored and are logged result=failed: {sorted(failed)}")
        return done

    return asyncio.run(runner())


def _voice_events_not_yet_real(conn) -> list[str]:
    """Voice-routed events whose most recent outcome is not a real call.
    Append-only: a later real outcome supersedes an earlier simulated one
    in the reconstructed view."""
    voice = {
        r[0]
        for r in conn.execute(
            "SELECT event_id FROM audit_log WHERE entry_type='decision' AND intervention='voice'"
        ).fetchall()
    }
    real = {
        r[0]
        for r in conn.execute(
            "SELECT event_id FROM audit_log WHERE entry_type='outcome' "
            "AND payload->>'transcript_source' = 'real'"
        ).fetchall()
    }
    return sorted(voice - real)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="TRUNCATE audit_log first")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--real-batch", action="store_true", dest="real_batch",
                    help="run the real Gemini agent for EVERY non-blocked voice event")
    ap.add_argument("--simulate", action="store_true",
                    help="use the seeded probability model instead of real calls")
    ap.add_argument("--real", type=int, default=0,
                    help="run the real agent for N voice events, simulate the rest")
    ap.add_argument("--append-real", type=int, default=0, dest="append_real",
                    help="add real Gemini calls for up to N voice events not yet "
                    "backed by a real call (no reset, no re-run)")
    ap.add_argument("--keep-events", action="store_true", dest="keep_events",
                    help="reuse the existing fixture instead of regenerating a "
                    "fresh random batch on --reset")
    ap.add_argument("--gen-seed", type=int, default=None, dest="gen_seed",
                    help="pin the synthetic-data seed (default: random each run)")
    ap.add_argument("--count", type=int, default=None,
                    help="number of synthetic events (default: random 45-75)")
    ap.add_argument("--now", default="2026-09-02T12:00:00+05:30")
    args = ap.parse_args()

    now = datetime.fromisoformat(args.now)

    # A fresh --reset means a fresh dataset, unless told otherwise.
    if args.reset and not args.keep_events:
        gseed, gcount = regenerate_fixture(args.gen_seed, args.count)
        print(f"generated {gcount} events (gen-seed {gseed})")

    events = load_events()
    by_id = {e.event_id: e for e in events}

    db.init_db()
    with db.get_conn() as conn:
        if args.append_real:
            pending = _voice_events_not_yet_real(conn)
            targets = [by_id[i] for i in pending if i in by_id][: args.append_real]
            routed = {e.event_id: route(e, now=now) for e in targets}
            done = run_real_calls(conn, targets, routed, len(targets))
            print(f"appended {len(done)} real calls: {sorted(done)}")
            return

        if args.reset:
            reset(conn)
        routed = run_decisions(conn, events, now)

        if args.simulate:
            simulate_outcomes(conn, events, routed, args.seed, skip=set())
            print(f"seeded {len(events)} events (all outcomes SIMULATED). reset={args.reset}")
            return

        # default + --real-batch + --real N: real Gemini for voice,
        # nothing fabricated for SMS/link.
        n = args.real or None
        real_ids = run_real_calls(conn, events, routed, n, retries=2)
        if args.real:  # simulate only the voice events we didn't really call
            simulate_outcomes(conn, events, routed, args.seed, skip=real_ids)
        print(f"seeded {len(events)} events — {len(real_ids)} real voice calls; "
              f"SMS/link left unconfirmed{'; rest simulated' if args.real else ''}. "
              f"reset={args.reset}")


if __name__ == "__main__":
    main()
