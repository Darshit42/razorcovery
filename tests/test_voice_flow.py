import asyncio
from datetime import datetime, timezone

import pytest

from data.schemas import Customer, FailureEvent
from voice.flow import RecoveryAgent
from voice.outcome import CallOutcome, recovered_amount, write_call_audit


def _ev(**kw):
    d = dict(
        event_id="evt_1", created_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        failure_type="payment_retry",
        customer=Customer(id="c1", name="A", phone="+919900000000",
                          timezone="Asia/Kolkata"),
        amount_inr=5000, reference_id="pay_1", error_code="card_declined",
        prior_attempts=0, refused=False,
    )
    d.update(kw)
    return FailureEvent(**d)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class ListSink:
    def __init__(self):
        self.rows = []

    def __call__(self, **kw):
        if kw["entry_type"] in ("decision", "stopping_rule_triggered"):
            assert kw.get("reason", "").strip()
        self.rows.append(kw)


def test_send_retry_link_marks_recovered_and_sets_link():
    a = RecoveryAgent(_ev(), attempt_number=1)
    run(a.send_retry_link(None))
    assert a.outcome.result == "recovered"
    assert a.outcome.consent_captured is True
    assert a.outcome.retry_link_url.startswith("https://")


def test_refusal_tool_captures_hard_stop():
    a = RecoveryAgent(_ev(), attempt_number=1)
    run(a.mark_do_not_contact(None))
    assert a.outcome.result == "refused"
    assert a.outcome.refusal_captured is True


def test_transcript_capture_trims_and_skips_empty():
    a = RecoveryAgent(_ev(), attempt_number=1)
    a.record_turn("assistant", "  Namaste ji  ")
    a.record_turn("user", "")
    a.record_turn("user", "haan boliye")
    assert a.outcome.transcript == [
        {"role": "assistant", "text": "Namaste ji"},
        {"role": "user", "text": "haan boliye"},
    ]


def test_write_call_audit_emits_action_then_outcome():
    sink = ListSink()
    ev = _ev()
    a = RecoveryAgent(ev, attempt_number=2)
    run(a.send_retry_link(None))
    write_call_audit(sink, ev, a.outcome)
    kinds = [r["entry_type"] for r in sink.rows]
    assert kinds == ["action", "outcome"]
    assert sink.rows[0]["attempt_number"] == 2
    assert sink.rows[1]["payload"]["result"] == "recovered"
    assert sink.rows[1]["payload"]["retry_link_url"]


def test_in_call_refusal_writes_stopping_rule_row():
    sink = ListSink()
    ev = _ev()
    a = RecoveryAgent(ev, attempt_number=1)
    run(a.mark_do_not_contact(None))
    write_call_audit(sink, ev, a.outcome)
    kinds = [r["entry_type"] for r in sink.rows]
    assert kinds == ["action", "stopping_rule_triggered", "outcome"]
    assert "explicit_refusal" in sink.rows[1]["reason"]
    assert sink.rows[1]["payload"]["blocks_all_contact"] is True


def test_recovered_amount_only_counts_full_recovery():
    ev = _ev(amount_inr=3200)
    assert recovered_amount(ev, CallOutcome(result="recovered", attempt_number=1)) == 3200
    assert recovered_amount(ev, CallOutcome(result="declined", attempt_number=1)) == 0
    assert recovered_amount(ev, CallOutcome(result="link_sent_no_commit", attempt_number=1)) == 0


def test_offer_declined_stores_note_without_faking_a_transcript_turn():
    a = RecoveryAgent(_ev(), attempt_number=1)
    run(a.offer_declined(None, note="Customer will retry by tomorrow evening"))
    assert a.outcome.result == "declined"
    assert a.outcome.decline_note == "Customer will retry by tomorrow evening"
    assert a.outcome.transcript == []  # not injected as a fake chat turn


def test_write_call_audit_includes_decline_note_in_reason_and_payload():
    sink = ListSink()
    ev = _ev()
    a = RecoveryAgent(ev, attempt_number=1)
    run(a.offer_declined(None, note="will retry tomorrow"))
    write_call_audit(sink, ev, a.outcome)
    outcome_row = sink.rows[-1]
    assert "will retry tomorrow" in outcome_row["reason"]
    assert outcome_row["payload"]["decline_note"] == "will retry tomorrow"


def test_end_call_without_other_tool_resolves_a_result():
    a = RecoveryAgent(_ev(), attempt_number=1)
    msg = run(a.end_call(None))
    assert msg == "__END_CALL__"
    assert a.outcome.result in ("declined", "link_sent_no_commit")


def test_end_call_flags_the_call_as_ended():
    # regression: end_call used to return a sentinel string nothing ever
    # read, so a plain "declined" call (not recovered/refused/wrong_number)
    # never tripped the entrypoint's wait loop and sat until the 4-minute
    # max-call-duration timeout instead of hanging up right away.
    a = RecoveryAgent(_ev(), attempt_number=1)
    assert a.call_ended_by_agent is False
    run(a.end_call(None))
    assert a.call_ended_by_agent is True
