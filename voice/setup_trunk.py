"""One-time: register the Vobiz (or any) SIP provider as a LiveKit
outbound trunk, and print the SIP_OUTBOUND_TRUNK_ID to put in .env.

    python -m voice.setup_trunk \
        --address 20d4b737.sip.vobiz.ai \
        --number +918065354620 \
        --user razorcovery --password 'razorcovery#01' \
        [--transport auto|udp|tcp|tls]

Reads LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET from .env.
Idempotent-ish: pass --replace to delete existing trunks with the same
name first.
"""
from __future__ import annotations

import argparse
import asyncio

from dotenv import load_dotenv

load_dotenv()

_TRANSPORTS = {
    "auto": "SIP_TRANSPORT_AUTO",
    "udp": "SIP_TRANSPORT_UDP",
    "tcp": "SIP_TRANSPORT_TCP",
    "tls": "SIP_TRANSPORT_TLS",
}


async def _run(args) -> None:
    from livekit import api
    from livekit.protocol.sip import (
        CreateSIPOutboundTrunkRequest,
        ListSIPOutboundTrunkRequest,
        SIPOutboundTrunkInfo,
        SIPTransport,
    )

    lk = api.LiveKitAPI()
    try:
        if args.replace:
            existing = await lk.sip.list_sip_outbound_trunk(ListSIPOutboundTrunkRequest())
            for t in existing.items:
                if t.name == args.name:
                    await lk.sip.delete_sip_trunk(
                        api.DeleteSIPTrunkRequest(sip_trunk_id=t.sip_trunk_id)
                    )
                    print(f"deleted existing trunk {t.sip_trunk_id}")

        info = await lk.sip.create_sip_outbound_trunk(
            CreateSIPOutboundTrunkRequest(
                trunk=SIPOutboundTrunkInfo(
                    name=args.name,
                    address=args.address,
                    numbers=[args.number],
                    auth_username=args.user,
                    auth_password=args.password,
                    transport=SIPTransport.Value(_TRANSPORTS[args.transport]),
                )
            )
        )
    finally:
        await lk.aclose()

    print("\nOutbound trunk created.")
    print(f"  id       : {info.sip_trunk_id}")
    print(f"  address  : {info.address}")
    print(f"  numbers  : {list(info.numbers)}")
    print(f"  transport: {SIPTransport.Name(info.transport)}")
    print("\nAdd to .env:")
    print(f"  SIP_OUTBOUND_TRUNK_ID={info.sip_trunk_id}")
    print(f"  SIP_CALLER_ID={args.number}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="vobiz-outbound")
    ap.add_argument("--address", required=True, help="provider SIP host, e.g. 20d4b737.sip.vobiz.ai")
    ap.add_argument("--number", required=True, help="caller-ID DID in E.164, e.g. +918065354620")
    ap.add_argument("--user", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--transport", choices=list(_TRANSPORTS), default="auto")
    ap.add_argument("--replace", action="store_true")
    asyncio.run(_run(ap.parse_args()))


if __name__ == "__main__":
    main()
