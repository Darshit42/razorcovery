"""Editable agent prompt: DB-backed override + always-appended guardrails."""
import pytest

from audit import db

pytestmark = pytest.mark.skipif(not db.ping(), reason="Postgres not reachable")


@pytest.fixture(autouse=True)
def _reset_prompt():
    from voice import agent_prompt as store

    store.reset_to_default()
    yield
    store.reset_to_default()


def test_default_when_unset():
    from voice import agent_prompt as store
    from voice.prompt import active_template, DEFAULT_TEMPLATE

    assert store.get_template() is None
    assert store.get_meta() is None
    assert active_template() == DEFAULT_TEMPLATE


def test_set_get_reset_roundtrip():
    from voice import agent_prompt as store

    store.set_template("Tum Aisha ho, {merchant} ki taraf se.", updated_by="t@example.com")
    meta = store.get_meta()
    assert meta["template"].startswith("Tum Aisha")
    assert meta["updated_by"] == "t@example.com"

    store.reset_to_default()
    assert store.get_template() is None


def test_rejects_empty_and_oversized():
    from voice import agent_prompt as store

    with pytest.raises(ValueError):
        store.set_template("   ", updated_by="t@example.com")
    with pytest.raises(ValueError):
        store.set_template("x" * 8001, updated_by="t@example.com")


def test_custom_template_used_and_guardrails_always_appended():
    from data.schemas import Customer, FailureEvent
    from datetime import datetime, timezone
    from voice import agent_prompt as store
    from voice.prompt import build_system_prompt

    store.set_template(
        "Tum Aisha ho, {merchant} ke liye. {customer_name}, aapka {failure_desc}, "
        "amount {amount}. {offer}",
        updated_by="t@example.com",
    )
    event = FailureEvent(
        event_id="e1", created_at=datetime.now(timezone.utc), failure_type="payment_retry",
        customer=Customer(id="c1", name="Ravi", phone="+919800000001", timezone="Asia/Kolkata"),
        amount_inr=2499, reference_id="r1", error_code="card_declined",
    )
    prompt = build_system_prompt(event, merchant="ChaiPoint")
    assert "Aisha" in prompt and "Ravi" in prompt and "2499" in prompt
    assert "CVV" in prompt and "OTP" in prompt  # fixed guardrails, not overridable


def test_unknown_placeholder_in_custom_template_does_not_crash():
    from data.schemas import Customer, FailureEvent
    from datetime import datetime, timezone
    from voice import agent_prompt as store
    from voice.prompt import build_system_prompt

    store.set_template("Hello {not_a_real_placeholder} {merchant}", updated_by="t@example.com")
    event = FailureEvent(
        event_id="e1", created_at=datetime.now(timezone.utc), failure_type="payment_retry",
        customer=Customer(id="c1", name="Ravi", phone="+919800000001", timezone="Asia/Kolkata"),
        amount_inr=2499, reference_id="r1", error_code="card_declined",
    )
    prompt = build_system_prompt(event, merchant="ChaiPoint")
    assert "{not_a_real_placeholder}" in prompt  # left as literal text, no crash
    assert "ChaiPoint" in prompt
