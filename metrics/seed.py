"""Make the audit trail demo-ready.

The decision layer only logs event -> decision. Real call outcomes come
from the voice agent (and, on Day 3, the real pilot). Until then this
seeds *simulated* action/outcome rows so the metrics view has something
to show. Every simulated outcome is marked `transcript_source:
"simulated"` and the metrics page carries a banner saying so.

    python -m metrics.seed --reset            # wipe, re-run batch, simulate all
    python -m metrics.seed --reset --real 6   # ...and run real Gemini for 6 calls

`--real N` produces genuine transcripts for N voice-routed events.
"""
from __future__ import annotations

import argparse
import random
from datetime import datetime, timezone

from audit import db
from audit.log import append_event
from data.generate_events import load_events
from decision.pipeline import bind_sink, process_event
from decision.rules import route

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
        if d.intervention == "voice":
            result = _pick(rng, VOICE_OUTCOMES)
            duration = 0.0 if result == "no_answer" else rng.uniform(35, 175)
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
                "transcript_source": "simulated",
                "ended_at": datetime.now(timezone.utc).isoformat(),
            },
        )


def run_real_calls(conn, events, routed, n: int) -> set[str]:
    """Run the actual Gemini agent headless for up to n voice events."""
    import asyncio

    from livekit.agents import AgentSession
    from livekit.plugins import google

    from voice.flow import RecoveryAgent
    from voice.outcome import write_call_audit

    voice_events = [e for e in events if routed[e.event_id].intervention == "voice"][:n]
    scripts = [
        ["Hello?", "Haan main hi bol raha hoon", "Achha, kya karna hoga?",
         "Theek hai link bhej do", "Thanks"],
        ["Haan boliye", "Abhi thoda busy hoon, baad mein karta hoon", "Ok bye"],
        ["Hello", "Mujhe interest nahi, dobara call mat karna", "Bye"],
        ["Haan?", "Kaunsa payment? Mujhe yaad nahi", "Achha theek hai link bhejo", "ok"],
    ]

    async def one(event, script):
        agent = RecoveryAgent(event, attempt_number=routed[event.event_id].attempt_number or 1,
                              merchant="ChaiPoint")
        session = AgentSession(llm=google.LLM(model="gemini-2.5-flash"))
        await session.start(agent=agent)
        for line in script:
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
        agent.outcome.transcript_source = "real"
        # tag the outcome payload source through write_call_audit
        import functools
        sink = _sink(conn)

        def tagged(**kw):
            if kw["entry_type"] == "outcome":
                kw["payload"]["transcript_source"] = "real"
            return sink(**kw)

        write_call_audit(tagged, event, agent.outcome)
        return event.event_id

    async def runner():
        done = set()
        for i, ev in enumerate(voice_events):
            try:
                done.add(await one(ev, scripts[i % len(scripts)]))
            except Exception as exc:  # Gemini can return empty completions
                print(f"  real call {ev.event_id} failed ({type(exc).__name__}); "
                      "falling back to simulated for this event")
        return done

    return asyncio.run(runner())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="TRUNCATE audit_log first")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--real", type=int, default=0, help="run N real Gemini calls")
    ap.add_argument("--now", default="2026-09-02T12:00:00+05:30")
    args = ap.parse_args()

    now = datetime.fromisoformat(args.now)
    events = load_events()

    db.init_db()
    with db.get_conn() as conn:
        if args.reset:
            reset(conn)
        routed = run_decisions(conn, events, now)
        real_ids: set[str] = set()
        if args.real:
            real_ids = run_real_calls(conn, events, routed, args.real)
        simulate_outcomes(conn, events, routed, args.seed, skip=real_ids)

    print(f"seeded {len(events)} events "
          f"({len(real_ids)} real calls, rest simulated). "
          f"reset={args.reset}")


if __name__ == "__main__":
    main()
