"""Telephony seam: turn a decision into a real outbound phone call.

`LiveKitSipDialer` is the real implementation — it creates a LiveKit room,
dispatches the recovery agent worker into it, and (the agent, on connect)
brings the callee in over an outbound SIP trunk. It needs, in .env:

    LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET   (a LiveKit server)
    SIP_OUTBOUND_TRUNK_ID                              (trunk -> Twilio/Plivo/Exotel)
    SIP_CALLER_ID (optional)                           (the number shown to the callee)

Until those are set, `UnconfiguredDialer` is used and refuses to place a
call — nothing dials a real number by accident.
"""
from __future__ import annotations

import os
from typing import Protocol

# SIP request tuning (mirrors production defaults).
RINGING_TIMEOUT_S = 30
DISPATCH_RETRIES = 2


class Dialer(Protocol):
    async def place_call(self, *, room_name: str, phone: str, metadata: str) -> str:
        """Start the outbound call; return a dispatch/call id."""
        ...


class UnconfiguredDialer:
    """Default. No telephony provider wired -> no call placed."""

    configured = False

    async def place_call(self, *, room_name: str, phone: str, metadata: str) -> str:
        raise RuntimeError(
            "No telephony configured. Set LIVEKIT_URL / LIVEKIT_API_KEY / "
            "LIVEKIT_API_SECRET / SIP_OUTBOUND_TRUNK_ID in .env (trunk pointed "
            "at Twilio/Plivo/Exotel), then the batch runner dials for real."
        )


class LiveKitSipDialer:
    """Real outbound calling via LiveKit + an outbound SIP trunk."""

    configured = True

    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        api_secret: str,
        outbound_trunk_id: str,
        agent_name: str = "razorcovery-agent",
        caller_id: str | None = None,
    ) -> None:
        self.url = url
        self.api_key = api_key
        self.api_secret = api_secret
        self.outbound_trunk_id = outbound_trunk_id
        self.agent_name = agent_name
        self.caller_id = caller_id

    async def place_call(self, *, room_name: str, phone: str, metadata: str) -> str:
        from livekit import api

        lk = api.LiveKitAPI(url=self.url, api_key=self.api_key, api_secret=self.api_secret)
        try:
            await lk.room.create_room(api.CreateRoomRequest(name=room_name))
            dispatch = await lk.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(
                    room=room_name, agent_name=self.agent_name, metadata=metadata,
                )
            )
            return getattr(dispatch, "id", room_name)
        finally:
            await lk.aclose()


async def dial_sip_participant(ctx, *, phone: str, trunk_id: str,
                               caller_id: str | None = None) -> bool:
    """Called from inside the agent job: ring the callee into the room.
    Returns True once answered, False if it could not connect."""
    from livekit import api
    from livekit.protocol.sip import CreateSIPParticipantRequest

    req = CreateSIPParticipantRequest(
        room_name=ctx.room.name,
        sip_trunk_id=trunk_id,
        sip_call_to=phone,
        participant_identity=phone,
        wait_until_answered=True,
    )
    if caller_id:
        req.sip_number = caller_id

    last_err: Exception | None = None
    for attempt in range(1, DISPATCH_RETRIES + 2):
        try:
            await ctx.api.sip.create_sip_participant(req)
            return True
        except Exception as exc:  # TwirpError etc.
            last_err = exc
    if last_err:
        raise last_err
    return False


def from_env() -> Dialer:
    url = os.environ.get("LIVEKIT_URL")
    key = os.environ.get("LIVEKIT_API_KEY")
    secret = os.environ.get("LIVEKIT_API_SECRET")
    trunk = os.environ.get("SIP_OUTBOUND_TRUNK_ID")
    if url and key and secret and trunk:
        return LiveKitSipDialer(
            url=url, api_key=key, api_secret=secret, outbound_trunk_id=trunk,
            agent_name=os.environ.get("LIVEKIT_AGENT_NAME", "razorcovery-agent"),
            caller_id=os.environ.get("SIP_CALLER_ID") or None,
        )
    return UnconfiguredDialer()
