import asyncio

import pytest

from voice import dialer


def test_unconfigured_dialer_refuses(monkeypatch):
    for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "SIP_OUTBOUND_TRUNK_ID"):
        monkeypatch.delenv(k, raising=False)
    d = dialer.from_env()
    assert isinstance(d, dialer.UnconfiguredDialer)
    assert d.configured is False


def test_unconfigured_place_call_raises():
    d = dialer.UnconfiguredDialer()
    with pytest.raises(RuntimeError, match="No telephony configured"):
        asyncio.new_event_loop().run_until_complete(
            d.place_call(room_name="r", phone="+91999", metadata="{}")
        )


def test_from_env_builds_sip_dialer_when_configured(monkeypatch):
    monkeypatch.setenv("LIVEKIT_URL", "wss://x.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "k")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "s")
    monkeypatch.setenv("SIP_OUTBOUND_TRUNK_ID", "ST_abc")
    monkeypatch.setenv("SIP_CALLER_ID", "+911234567890")
    d = dialer.from_env()
    assert isinstance(d, dialer.LiveKitSipDialer)
    assert d.configured is True
    assert d.outbound_trunk_id == "ST_abc"
    assert d.caller_id == "+911234567890"
