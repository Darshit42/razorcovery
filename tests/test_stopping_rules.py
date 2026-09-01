from datetime import datetime, timezone

import pytest

from decision import config
from decision.stopping_rules import check_stopping_rules, within_call_window

# A fixed UTC "now" that is 12:00 IST (well inside the window) and
# 06:30 UTC.
NOON_IST = datetime(2026, 9, 2, 6, 30, tzinfo=timezone.utc)


def _check(**kw):
    base = dict(attempts=0, refused=False, timezone="Asia/Kolkata",
               now=NOON_IST, intervention="voice")
    base.update(kw)
    return check_stopping_rules(**base)


def test_allows_normal_voice_call():
    assert _check().blocked is False


def test_explicit_refusal_blocks_every_channel():
    for channel in ("voice", "sms", "link_only"):
        d = _check(refused=True, intervention=channel)
        assert d.blocked is True
        assert d.rule == "explicit_refusal"
        assert d.blocks_all_contact is True


def test_max_attempts_blocks_voice_only():
    d = _check(attempts=config.MAX_ATTEMPTS, intervention="voice")
    assert d.blocked is True
    assert d.rule == "max_attempts"
    assert d.blocks_all_contact is False
    # same customer, SMS instead -> allowed
    assert _check(attempts=config.MAX_ATTEMPTS, intervention="sms").blocked is False


def test_one_below_max_attempts_is_allowed():
    assert _check(attempts=config.MAX_ATTEMPTS - 1).blocked is False


# IST = UTC+5:30.
@pytest.mark.parametrize(
    "utc_h, utc_m, ist_label, expected_blocked",
    [
        (2, 0, "07:30 IST", True),    # before window
        (3, 0, "08:30 IST", True),
        (3, 29, "08:59 IST", True),
        (3, 30, "09:00 IST", False),  # window opens (inclusive)
        (10, 0, "15:30 IST", False),
        (13, 0, "18:30 IST", False),  # still inside
        (13, 30, "19:00 IST", True),  # window closes (exclusive)
        (15, 0, "20:30 IST", True),
    ],
)
def test_call_window_boundaries(utc_h, utc_m, ist_label, expected_blocked):
    now = datetime(2026, 9, 2, utc_h, utc_m, tzinfo=timezone.utc)
    d = check_stopping_rules(attempts=0, refused=False, timezone="Asia/Kolkata",
                             now=now, intervention="voice")
    assert d.blocked is expected_blocked
    if expected_blocked:
        assert d.rule == "call_window"
        assert d.reason and "window" in d.reason


def test_call_window_is_timezone_aware():
    # 06:30 UTC is 12:00 IST (ok) but 07:30 in London (also ok);
    # pick a UTC instant that is inside IST window but outside London's.
    # 03:00 UTC -> 08:30 IST (blocked) and 04:00 London (blocked) -- use
    # instead 15:00 UTC -> 20:30 IST (blocked) vs 16:00 London (allowed).
    now = datetime(2026, 9, 2, 15, 0, tzinfo=timezone.utc)
    assert within_call_window(now, "Asia/Kolkata") is False
    assert within_call_window(now, "Europe/London") is True


def test_window_does_not_apply_to_non_voice():
    now = datetime(2026, 9, 2, 22, 0, tzinfo=timezone.utc)  # 03:30 IST
    assert check_stopping_rules(attempts=0, refused=False,
                                timezone="Asia/Kolkata", now=now,
                                intervention="sms").blocked is False


def test_naive_now_is_rejected():
    with pytest.raises(ValueError):
        check_stopping_rules(attempts=0, refused=False, timezone="Asia/Kolkata",
                             now=datetime(2026, 9, 2, 12, 0), intervention="voice")
