from metrics import compute

_id = [0]


def row(event_id, entry_type, **kw):
    _id[0] += 1
    base = dict(
        id=_id[0], ts=None, event_id=event_id, customer_id=kw.get("customer_id", "c_" + event_id),
        entry_type=entry_type, failure_type=kw.get("failure_type"),
        intervention=kw.get("intervention"), reason=kw.get("reason"),
        amount_inr=kw.get("amount_inr"), attempt_number=kw.get("attempt_number"),
        payload=kw.get("payload"),
    )
    return base


def recovered_voice(eid, amount, ft="payment_retry", dur=60.0, source="simulated",
                    ptok=1000, ctok=200):
    return [
        row(eid, "event_ingested", failure_type=ft, amount_inr=amount),
        row(eid, "decision", failure_type=ft, intervention="voice",
            reason="amount -> voice", amount_inr=amount, attempt_number=1),
        row(eid, "action", intervention="voice", amount_inr=amount, attempt_number=1),
        row(eid, "outcome", intervention="voice", amount_inr=amount,
            reason="call ended: recovered",
            payload={"result": "recovered", "duration_s": dur,
                     "retry_link_url": "https://x", "transcript": [{"role": "user", "text": "ok"}],
                     "prompt_tokens": ptok, "completion_tokens": ctok,
                     "transcript_source": source}),
    ]


def blocked_refusal(eid, amount, ft="checkout_abandonment"):
    return [
        row(eid, "event_ingested", failure_type=ft, amount_inr=amount),
        row(eid, "stopping_rule_triggered", failure_type=ft, intervention="voice",
            reason="[explicit_refusal] refused", amount_inr=amount,
            payload={"rule": "explicit_refusal", "blocks_all_contact": True}),
        row(eid, "decision", failure_type=ft, intervention="none",
            reason="All contact blocked.", amount_inr=amount),
    ]


def sms_no_recovery(eid, amount, ft="mandate_failure"):
    return [
        row(eid, "event_ingested", failure_type=ft, amount_inr=amount),
        row(eid, "decision", failure_type=ft, intervention="sms",
            reason="mid amount -> sms", amount_inr=amount),
        row(eid, "action", intervention="sms", amount_inr=amount, attempt_number=1),
        row(eid, "outcome", intervention="sms", amount_inr=amount, reason="sms: link_sent_no_commit",
            payload={"result": "link_sent_no_commit", "duration_s": 0}),
    ]


def dataset():
    _id[0] = 0
    rows = []
    rows += recovered_voice("evt_a", 10000, dur=120.0, source="real")
    rows += recovered_voice("evt_b", 5000, dur=60.0)
    rows += blocked_refusal("evt_c", 8000)
    rows += sms_no_recovery("evt_d", 2000)
    return rows


def test_reconstruct_orders_and_fills_lifecycle():
    lcs = compute.reconstruct(dataset())
    a = lcs["evt_a"]
    assert a.failure_type == "payment_retry"
    assert a.decided_intervention == "voice"
    assert a.attempts == 1
    assert a.recovered is True
    assert a.recovered_amount_inr == 10000
    assert a.transcript_source == "real"
    assert [r["entry_type"] for r in a.timeline] == [
        "event_ingested", "decision", "action", "outcome"]


def test_blocked_event_flags():
    lc = compute.reconstruct(dataset())["evt_c"]
    assert lc.blocked is True
    assert lc.recovered is False
    assert lc.stopping_rules[0]["rule"] == "explicit_refusal"


def test_summary_recovery_and_value():
    s = compute.summarise(dataset())
    assert s.total_events == 4
    assert s.recovered == 2
    assert s.amount_recovered_inr == 15000
    assert s.amount_at_risk_inr == 25000
    # contacted = 2 voice + 1 sms = 3 (blocked 'none' not contacted)
    assert s.contacted == 3
    assert s.recovery_rate == round(2 / 3, 4)


def test_recovery_by_failure_type_buckets():
    s = compute.summarise(dataset())
    by_ft = {b.key: b for b in s.by_failure_type}
    assert by_ft["payment_retry"].recovered == 2
    assert by_ft["checkout_abandonment"].recovered == 0
    assert by_ft["mandate_failure"].recovered == 0


def test_effort_per_recovery_math():
    s = compute.summarise(dataset())
    e = s.effort
    assert e.recovered == 2
    assert e.total_attempts == 2
    assert e.total_call_minutes == 3.0        # (120 + 60)/60
    assert e.call_minutes_per_recovery == 1.5
    # two recovered_voice: 2*1000 prompt, 2*200 completion tokens
    assert e.prompt_tokens == 2000
    assert e.completion_tokens == 400
    assert e.tokens_per_recovery == 1200
    # LLM cost = 2000/1e6*0.30 + 400/1e6*2.50 USD, * 88 INR
    from metrics import cost
    assert e.llm_cost_inr == cost.llm_cost_inr(2000, 400)
    assert e.telephony_cost_inr == 0.0        # no provider
    assert e.total_cost_inr == e.llm_cost_inr


def test_exceptions_lists_every_non_recovered_with_reason():
    lcs = list(compute.reconstruct(dataset()).values())
    exc = compute.exceptions(lcs)
    ids = {x["event_id"] for x in exc}
    assert ids == {"evt_c", "evt_d"}
    by_id = {x["event_id"]: x for x in exc}
    assert by_id["evt_c"]["blocked"] is True
    assert "refused" in by_id["evt_c"]["why"]
    assert by_id["evt_d"]["blocked"] is False
    assert by_id["evt_d"]["why"]
    # sorted by amount desc
    assert [x["event_id"] for x in exc] == ["evt_c", "evt_d"]


def test_stopping_rule_counts():
    s = compute.summarise(dataset())
    assert s.stopping_rule_counts == {"explicit_refusal": 1}


# --- filters --------------------------------------------------------------

def _lcs():
    return list(compute.reconstruct(dataset()).values())


def test_filter_by_failure_type():
    got = compute.filter_lifecycles(_lcs(), failure_type="payment_retry")
    assert {lc.event_id for lc in got} == {"evt_a", "evt_b"}


def test_filter_by_intervention():
    got = compute.filter_lifecycles(_lcs(), intervention="sms")
    assert {lc.event_id for lc in got} == {"evt_d"}


def test_filter_by_status():
    lcs = _lcs()
    assert {lc.event_id for lc in compute.filter_lifecycles(lcs, status="recovered")} == {"evt_a", "evt_b"}
    assert {lc.event_id for lc in compute.filter_lifecycles(lcs, status="blocked")} == {"evt_c"}
    assert {lc.event_id for lc in compute.filter_lifecycles(lcs, status="open")} == {"evt_d"}


def test_filters_compose():
    got = compute.filter_lifecycles(_lcs(), failure_type="payment_retry", status="recovered")
    assert {lc.event_id for lc in got} == {"evt_a", "evt_b"}


def test_summarise_lifecycles_matches_summarise_on_subset():
    lcs = compute.filter_lifecycles(_lcs(), failure_type="payment_retry")
    s = compute.summarise_lifecycles(lcs)
    assert s.total_events == 2
    assert s.recovered == 2
    assert s.amount_recovered_inr == 15000


# --- manual recovery-status override --------------------------------------

def _manual_status_row(eid, status):
    return row(eid, "manual_status", payload={"manual_status": status})


def test_manual_status_paid_overrides_a_non_recovered_result():
    rows = blocked_refusal("evt_c", 8000) + [_manual_status_row("evt_c", "paid")]
    lc = compute.reconstruct(rows)["evt_c"]
    assert lc.manual_status == "paid"
    assert lc.recovered is True
    assert lc.recovered_amount_inr == 8000


def test_manual_status_failed_overrides_a_recovered_result():
    rows = recovered_voice("evt_a", 10000) + [_manual_status_row("evt_a", "failed")]
    lc = compute.reconstruct(rows)["evt_a"]
    assert lc.manual_status == "failed"
    assert lc.recovered is False
    assert lc.recovered_amount_inr == 0


def test_manual_status_disputed_overrides_a_recovered_result():
    rows = recovered_voice("evt_a", 10000) + [_manual_status_row("evt_a", "disputed")]
    lc = compute.reconstruct(rows)["evt_a"]
    assert lc.recovered is False


def test_manual_status_pending_and_partial_do_not_force_recovered():
    for status in ("pending", "partial"):
        rows = blocked_refusal("evt_c", 8000) + [_manual_status_row("evt_c", status)]
        lc = compute.reconstruct(rows)["evt_c"]
        assert lc.manual_status == status
        assert lc.recovered is False


def test_manual_status_unset_clears_override_back_to_automatic():
    rows = (
        recovered_voice("evt_a", 10000)
        + [_manual_status_row("evt_a", "failed")]
        + [_manual_status_row("evt_a", "unset")]
    )
    lc = compute.reconstruct(rows)["evt_a"]
    assert lc.manual_status is None
    assert lc.recovered is True
    assert lc.recovered_amount_inr == 10000


def test_manual_status_latest_row_wins():
    rows = (
        blocked_refusal("evt_c", 8000)
        + [_manual_status_row("evt_c", "paid")]
        + [_manual_status_row("evt_c", "failed")]
    )
    lc = compute.reconstruct(rows)["evt_c"]
    assert lc.manual_status == "failed"
    assert lc.recovered is False
