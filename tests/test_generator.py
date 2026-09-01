from data.generate_events import generate_events, write_fixture, load_events


def test_generates_50_plus_across_three_types():
    events = generate_events(count=60, seed=42)
    assert len(events) >= 50
    types = {e.failure_type for e in events}
    assert types == {"payment_retry", "checkout_abandonment", "mandate_failure"}


def test_prior_attempt_distribution_is_realistic():
    events = generate_events(count=200, seed=7)
    counts = {0: 0, 1: 0, 2: 0}
    for e in events:
        counts[e.prior_attempts] += 1
    # most fresh, some retried, a few maxed -- all buckets non-empty
    assert counts[0] > counts[1] > 0
    assert counts[2] > 0
    assert counts[0] / len(events) > 0.5


def test_some_customers_have_refused():
    events = generate_events(count=200, seed=7)
    assert any(e.refused for e in events)
    assert not all(e.refused for e in events)


def test_deterministic_for_seed():
    a = generate_events(count=30, seed=1)
    b = generate_events(count=30, seed=1)
    assert [e.model_dump() for e in a] == [e.model_dump() for e in b]


def test_fixture_round_trip(tmp_path):
    events = generate_events(count=20, seed=3)
    path = write_fixture(events, tmp_path / "events.json")
    loaded = load_events(path)
    assert [e.model_dump() for e in loaded] == [e.model_dump() for e in events]
