from datetime import datetime, timezone

from data.schemas import Customer, FailureEvent
from decision import config
from decision.pipeline import process_event

NOON_IST = datetime(2026, 9, 2, 6, 30, tzinfo=timezone.utc)
NIGHT_IST = datetime(2026, 9, 2, 22, 0, tzinfo=timezone.utc)


class ListSink:
    """Stand-in for audit.log.append_event bound to a connection."""

    def __init__(self):
        self.rows = []

    def __call__(self, **kw):
        # mirror the real reason-required guard
        if kw["entry_type"] in ("decision", "stopping_rule_triggered"):
            assert kw.get("reason") and kw["reason"].strip(), kw
        self.rows.append(kw)
        return {"id": len(self.rows), "ts": "now"}


def make_event(**kw):
    defaults = dict(
        event_id="evt_1", created_at=NOON_IST, failure_type="payment_retry",
        customer=Customer(id="cust_1", name="A", phone="+919900000000",
                          timezone="Asia/Kolkata"),
        amount_inr=5000, reference_id="pay_1", error_code="card_declined",
        prior_attempts=0, refused=False,
    )
    defaults.update(kw)
    return FailureEvent(**defaults)


def test_happy_path_logs_ingest_then_decision():
    sink = ListSink()
    process_event(make_event(), now=NOON_IST, sink=sink)
    kinds = [r["entry_type"] for r in sink.rows]
    assert kinds == ["event_ingested", "decision"]
    assert sink.rows[-1]["intervention"] == "voice"


def test_blocked_call_emits_stopping_rule_row_and_no_voice():
    sink = ListSink()
    decision = process_event(make_event(prior_attempts=config.MAX_ATTEMPTS),
                             now=NOON_IST, sink=sink)
    kinds = [r["entry_type"] for r in sink.rows]
    assert kinds == ["event_ingested", "stopping_rule_triggered", "decision"]
    assert decision.intervention != "voice"
    stop_row = sink.rows[1]
    assert "max_attempts" in stop_row["reason"]
    assert sink.rows[-1]["intervention"] == "sms"


def test_refusal_blocks_all_and_is_logged():
    sink = ListSink()
    process_event(make_event(refused=True), now=NOON_IST, sink=sink)
    assert sink.rows[1]["entry_type"] == "stopping_rule_triggered"
    assert sink.rows[-1]["intervention"] == "none"


def test_out_of_window_call_is_logged_and_downgraded():
    sink = ListSink()
    process_event(make_event(), now=NIGHT_IST, sink=sink)
    assert sink.rows[1]["payload"]["rule"] == "call_window"
    assert sink.rows[-1]["intervention"] == "sms"


def test_every_decision_row_has_a_reason():
    sink = ListSink()
    for ft, ec in [("payment_retry", "card_declined"),
                   ("checkout_abandonment", "checkout_closed"),
                   ("mandate_failure", "mandate_revoked")]:
        process_event(make_event(failure_type=ft, error_code=ec, event_id=f"e_{ft}"),
                      now=NOON_IST, sink=sink)
    for r in sink.rows:
        if r["entry_type"] in ("decision", "stopping_rule_triggered"):
            assert r["reason"]
