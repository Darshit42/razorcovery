from data.schemas import Customer, FailureEvent
from datetime import datetime, timezone
from voice.prompt import build_system_prompt, recovery_offer


def _ev(**kw):
    d = dict(
        event_id="evt_1", created_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        failure_type="payment_retry",
        customer=Customer(id="c1", name="Rohit Sharma", phone="+919900000000",
                          timezone="Asia/Kolkata"),
        amount_inr=2499, reference_id="pay_1", error_code="card_declined",
        prior_attempts=0, refused=False,
    )
    d.update(kw)
    return FailureEvent(**d)


def test_prompt_grounds_in_the_event():
    p = build_system_prompt(_ev(), merchant="ChaiPoint")
    assert "ChaiPoint" in p
    assert "Rohit Sharma" in p
    assert "2499" in p


def test_prompt_forbids_collecting_secrets():
    p = build_system_prompt(_ev())
    assert "CVV" in p and "OTP" in p
    assert "don't call me again" in p or "dobara call mat karna" in p


def test_offer_differs_by_failure_type():
    a = recovery_offer(_ev(failure_type="payment_retry", error_code="GATEWAY_ERROR"))
    b = recovery_offer(_ev(failure_type="checkout_abandonment", error_code="checkout_closed"))
    c = recovery_offer(_ev(failure_type="mandate_failure", error_code="mandate_bank_declined"))
    assert a != b != c


def test_revoked_mandate_offer_is_reauthorization():
    offer = recovery_offer(_ev(failure_type="mandate_failure", error_code="mandate_revoked"))
    assert "authorize" in offer.lower() or "mandate" in offer.lower()
