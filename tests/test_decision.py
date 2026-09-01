from datetime import datetime, timezone

from data.schemas import Customer, FailureEvent
from decision import config
from decision.rules import decide_by_type, route

NOON_IST = datetime(2026, 9, 2, 6, 30, tzinfo=timezone.utc)
NIGHT_IST = datetime(2026, 9, 2, 22, 0, tzinfo=timezone.utc)  # 03:30 IST


def make_event(**kw):
    defaults = dict(
        event_id="evt_x",
        created_at=NOON_IST,
        failure_type="payment_retry",
        customer=Customer(id="cust_x", name="Test", phone="+919900000000",
                          timezone="Asia/Kolkata"),
        amount_inr=5000,
        reference_id="pay_abc",
        error_code="card_declined",
        prior_attempts=0,
        refused=False,
    )
    defaults.update(kw)
    return FailureEvent(**defaults)


def test_high_value_payment_retry_wants_voice():
    d = decide_by_type(make_event(amount_inr=config.VOICE_MIN_AMOUNT_INR))
    assert d.intervention == "voice"
    assert d.attempt_number == 1
    assert d.reason


def test_low_value_payment_retry_is_link_only():
    d = decide_by_type(make_event(amount_inr=100))
    assert d.intervention == "link_only"


def test_checkout_abandonment_has_higher_voice_bar():
    # amount that would be 'voice' for payment_retry but not for abandonment
    amt = config.VOICE_MIN_AMOUNT_INR
    assert decide_by_type(make_event(failure_type="payment_retry", amount_inr=amt,
                                     error_code="GATEWAY_ERROR")).intervention == "voice"
    assert decide_by_type(make_event(failure_type="checkout_abandonment", amount_inr=amt,
                                     error_code="checkout_closed")).intervention != "voice"


def test_revoked_mandate_is_always_link_only():
    d = decide_by_type(make_event(failure_type="mandate_failure", amount_inr=9999,
                                  error_code="mandate_revoked"))
    assert d.intervention == "link_only"


def test_route_blocks_all_contact_on_refusal():
    d = route(make_event(refused=True, amount_inr=5000), now=NOON_IST)
    assert d.intervention == "none"
    assert d.stop.rule == "explicit_refusal"
    assert d.reason


def test_route_downgrades_voice_to_sms_when_max_attempts_hit():
    d = route(make_event(amount_inr=5000, prior_attempts=config.MAX_ATTEMPTS),
              now=NOON_IST)
    assert d.desired == "voice"
    assert d.intervention == "sms"
    assert d.stop.rule == "max_attempts"


def test_route_downgrades_voice_to_sms_outside_call_window():
    d = route(make_event(amount_inr=5000), now=NIGHT_IST)
    assert d.desired == "voice"
    assert d.intervention == "sms"
    assert d.stop.rule == "call_window"


def test_route_passes_through_when_nothing_fires():
    d = route(make_event(amount_inr=5000), now=NOON_IST)
    assert d.intervention == "voice"
    assert d.stop is None


def test_route_respects_attempts_override():
    d = route(make_event(amount_inr=5000, prior_attempts=0), now=NOON_IST,
              attempts_override=config.MAX_ATTEMPTS)
    assert d.intervention == "sms"
    assert d.stop.rule == "max_attempts"
