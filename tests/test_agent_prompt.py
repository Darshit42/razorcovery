"""Editable agent prompt: DB-backed override + always-appended guardrails."""
import json

import pytest

from audit import db

pytestmark = pytest.mark.skipif(not db.ping(), reason="Postgres not reachable")


@pytest.fixture(autouse=True)
def _reset_prompt():
    from voice import agent_prompt as store

    store.init_schema()
    with db.get_conn() as conn:
        before = conn.execute("SELECT COALESCE(MAX(id), 0) FROM agent_prompt_version").fetchone()[0]
    store.reset_to_default()
    yield
    store.reset_to_default()
    # precise cleanup: remove only version rows this test created, by id
    # (this history table has no append-only trigger -- unlike audit_log,
    # rolling back test noise here is fine and keeps the shared table small).
    with db.get_conn() as conn:
        conn.execute("DELETE FROM agent_prompt_version WHERE id > %s", (before,))


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


def test_rejects_empty_only():
    from voice import agent_prompt as store

    with pytest.raises(ValueError):
        store.set_template("   ", updated_by="t@example.com")
    # no length cap: a very long prompt is accepted
    store.set_template("x" * 50_000, updated_by="t@example.com")
    assert store.get_template() == "x" * 50_000


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


# --- diff engine (pure, no DB) ---------------------------------------------

def test_make_diff_and_apply_diff_roundtrip():
    from voice import agent_prompt as store

    old = "line one\nline two\nline three\n"
    new = "line one\nline TWO changed\nline three\nline four\n"
    ops = store.make_diff(old, new)
    assert store.apply_diff(old, ops) == new


def test_make_diff_is_empty_for_identical_text():
    from voice import agent_prompt as store

    text = "same\ntext\n"
    ops = store.make_diff(text, text)
    assert all(op["tag"] == "equal" for op in ops)
    assert store.apply_diff(text, ops) == text


def test_diff_stores_only_the_changed_lines_not_a_full_copy():
    from voice import agent_prompt as store

    base = "\n".join(f"line {i}" for i in range(8000)) + "\n"
    changed = base.replace("line 4000", "line 4000 EDITED")
    ops = store.make_diff(base, changed)
    added, removed = store._diff_stats(ops)
    assert added == 1 and removed == 1
    # the serialized diff is tiny compared to the 8000-line text it encodes
    assert len(json.dumps(ops)) < len(base) / 10


# --- version history + rollback (DB) ---------------------------------------

def test_set_template_writes_a_base_then_edit_version():
    from voice import agent_prompt as store

    v1 = store.set_template("First custom prompt.", updated_by="t@example.com")
    versions = store.list_versions()
    assert versions[0]["id"] == v1
    assert versions[0]["kind"] == "base"

    v2 = store.set_template("First custom prompt, tweaked.", updated_by="t@example.com")
    versions = store.list_versions()
    assert versions[0]["id"] == v2
    assert versions[0]["kind"] == "edit"
    assert versions[0]["lines_added"] >= 1


def test_set_template_same_text_is_a_no_op():
    from voice import agent_prompt as store

    v1 = store.set_template("Unchanged text.", updated_by="t@example.com")
    v2 = store.set_template("Unchanged text.", updated_by="t@example.com")
    assert v2 is None
    assert len(store.list_versions()) == 1
    assert store.list_versions()[0]["id"] == v1


def test_rollback_restores_a_past_version_as_a_new_forward_entry():
    from voice import agent_prompt as store

    v1 = store.set_template("Version one text.", updated_by="a@example.com")
    store.set_template("Version two text.", updated_by="a@example.com")
    assert store.get_template() == "Version two text."

    new_id = store.rollback_to(v1, updated_by="b@example.com")
    assert store.get_template() == "Version one text."

    versions = store.list_versions()
    assert versions[0]["id"] == new_id
    assert versions[0]["kind"] == "rollback"
    assert versions[0]["updated_by"] == "b@example.com"
    # the original version row is untouched, not rewritten
    assert any(v["id"] == v1 and v["kind"] == "base" for v in versions)


def test_rollback_unknown_version_raises():
    from voice import agent_prompt as store

    with pytest.raises(ValueError):
        store.rollback_to(999_999_999, updated_by="t@example.com")


def test_reset_to_default_logs_a_version_when_customised():
    from voice import agent_prompt as store
    from voice.prompt import DEFAULT_TEMPLATE

    store.set_template("Something custom.", updated_by="t@example.com")
    vid = store.reset_to_default(updated_by="t@example.com")
    assert vid is not None
    assert store.get_template() is None
    assert store.get_version_text(vid) == DEFAULT_TEMPLATE


def test_reset_to_default_is_a_no_op_when_already_default():
    from voice import agent_prompt as store

    vid = store.reset_to_default(updated_by="t@example.com")
    assert vid is None
