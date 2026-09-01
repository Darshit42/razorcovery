from datetime import datetime, timezone

import pytest

from data.schemas import Customer, FailureEvent
from decision.rules import route
from voice.dispatch import build_job_metadata, dispatch_call

NOON_IST = datetime(2026, 9, 2, 6, 30, tzinfo=timezone.utc)
NIGHT_IST = datetime(2026, 9, 2, 22, 0, tzinfo=timezone.utc)


def _ev(**kw):
    d = dict(
        event_id="evt_1", created_at=NOON_IST, failure_type="payment_retry",
        customer=Customer(id="c1", name="A", phone="+919900000000",
                          timezone="Asia/Kolkata"),
        amount_inr=6000, reference_id="pay_1", error_code="card_declined",
        prior_attempts=0, refused=False,
    )
    d.update(kw)
    return FailureEvent(**d)


class SpyDialer:
    def __init__(self):
        self.calls = []

    def place_call(self, *, room_name, phone, metadata):
        self.calls.append({"room_name": room_name, "phone": phone, "metadata": metadata})
        return "call_123"


class ListSink:
    def __init__(self):
        self.rows = []

    def __call__(self, **kw):
        self.rows.append(kw)


def test_places_call_when_nothing_blocks():
    ev = _ev()
    d = route(ev, now=NOON_IST)
    assert d.intervention == "voice"
    dialer, sink = SpyDialer(), ListSink()
    res = dispatch_call(ev, d, now=NOON_IST, sink=sink, dialer=dialer)
    assert res["placed"] is True
    assert dialer.calls[0]["phone"] == ev.customer.phone
    assert dialer.calls[0]["room_name"] == "recovery-evt_1"


def test_refusal_blocks_dispatch_and_is_logged():
    ev = _ev(refused=True)
    d = route(ev, now=NOON_IST)  # -> intervention 'none'
    dialer, sink = SpyDialer(), ListSink()
    # force the voice path to prove dispatch itself guards, not just routing
    d_voice = d.__class__(intervention="voice", reason="forced", attempt_number=1,
                          desired="voice", stop=None)
    res = dispatch_call(ev, d_voice, now=NOON_IST, sink=sink, dialer=dialer)
    assert res["placed"] is False
    assert res["reason"] == "explicit_refusal"
    assert dialer.calls == []
    assert sink.rows[0]["entry_type"] == "stopping_rule_triggered"


def test_max_attempts_blocks_dispatch():
    ev = _ev(prior_attempts=2)
    from decision.rules import RoutedDecision
    d_voice = RoutedDecision(intervention="voice", reason="forced", attempt_number=3,
                             desired="voice", stop=None)
    dialer, sink = SpyDialer(), ListSink()
    res = dispatch_call(ev, d_voice, now=NOON_IST, sink=sink, dialer=dialer)
    assert res["placed"] is False
    assert res["reason"] == "max_attempts"
    assert dialer.calls == []


def test_call_window_blocks_dispatch():
    ev = _ev()
    from decision.rules import RoutedDecision
    d_voice = RoutedDecision(intervention="voice", reason="forced", attempt_number=1,
                             desired="voice", stop=None)
    dialer, sink = SpyDialer(), ListSink()
    res = dispatch_call(ev, d_voice, now=NIGHT_IST, sink=sink, dialer=dialer)
    assert res["placed"] is False
    assert res["reason"] == "call_window"
    assert dialer.calls == []


def test_non_voice_decision_is_a_noop():
    ev = _ev(amount_inr=50)
    d = route(ev, now=NOON_IST)
    assert d.intervention != "voice"
    res = dispatch_call(ev, d, now=NOON_IST, sink=ListSink(), dialer=SpyDialer())
    assert res["placed"] is False


def test_job_metadata_round_trips_the_event():
    import json
    ev = _ev()
    meta = json.loads(build_job_metadata(ev, attempt_number=2, merchant="ChaiPoint"))
    assert meta["_call"] == {"attempt_number": 2, "merchant": "ChaiPoint"}
    assert meta["event_id"] == "evt_1"


def test_default_dialer_refuses_to_place_a_call():
    ev = _ev()
    from decision.rules import RoutedDecision
    d_voice = RoutedDecision(intervention="voice", reason="ok", attempt_number=1,
                             desired="voice", stop=None)
    with pytest.raises(RuntimeError, match="No telephony provider"):
        dispatch_call(ev, d_voice, now=NOON_IST, sink=ListSink())
