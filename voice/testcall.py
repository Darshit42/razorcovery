"""Place ONE real outbound recovery call — for testing the telephony
path against a number you control.

    # terminal 1: the agent worker (leave running)
    python -m voice.agent start

    # terminal 2: place the call
    python -m voice.testcall --to +9198XXXXXXXX --amount 2499 \
        [--name "Your Name"] [--failure-type payment_retry] [--merchant ChaiPoint]

It creates a room, dispatches the razorcovery agent into it, and the
agent rings --to over the Vobiz trunk. Outcome + transcript land in
audit_log (event id printed) and show on the dashboard.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import secrets
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

from data.schemas import ERROR_CODES, Customer, FailureEvent  # noqa: E402
from voice.dialer import from_env  # noqa: E402


async def _run(args) -> None:
    dialer = from_env()
    if not getattr(dialer, "configured", False):
        raise SystemExit(
            "Telephony not configured. Set LIVEKIT_* and SIP_OUTBOUND_TRUNK_ID "
            "in .env (run `python -m voice.setup_trunk ...` first)."
        )

    event = FailureEvent(
        event_id="testcall_" + secrets.token_hex(4),
        created_at=datetime.now(timezone.utc),
        failure_type=args.failure_type,
        customer=Customer(id="testcall_cust", name=args.name, phone=args.to,
                          timezone=args.timezone),
        amount_inr=args.amount,
        reference_id="testcall",
        error_code=ERROR_CODES[args.failure_type][0],
        prior_attempts=0,
        refused=False,
    )
    meta = json.loads(event.model_dump_json())
    meta["_call"] = {"attempt_number": 1, "merchant": args.merchant, "dial": True}

    room = f"recovery-{event.event_id}"
    print(f"dispatching agent to room {room} → dialing {args.to} …")
    call_id = await dialer.place_call(room_name=room, phone=args.to,
                                      metadata=json.dumps(meta))
    print(f"dispatch id: {call_id}")
    print(f"event id   : {event.event_id}")
    print("Watch the worker logs. Outcome will be written to audit_log; "
          f"see it at /event/{event.event_id} on the dashboard.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", required=True, help="callee number in E.164, e.g. +919812345678")
    ap.add_argument("--amount", type=int, default=2499)
    ap.add_argument("--name", default="Test Customer")
    ap.add_argument("--failure-type", default="payment_retry",
                    choices=["payment_retry", "checkout_abandonment", "mandate_failure"])
    ap.add_argument("--merchant", default="ChaiPoint")
    ap.add_argument("--timezone", default="Asia/Kolkata")
    asyncio.run(_run(ap.parse_args()))


if __name__ == "__main__":
    main()
