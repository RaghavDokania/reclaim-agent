from compare_policies import run_comparison

BATCH = [
    {
        "payment_id": f"pay_{i}",
        "amount_inr": 1000.0,
        "root_cause": "network_timeout",
        "created_at": "2026-08-20T10:00:00+00:00",
        "attempt_count": 1,
    }
    for i in range(50)
]


def test_do_nothing_recovers_nothing():
    results = run_comparison(BATCH, seed=42)
    assert results["do_nothing"]["recovered_count"] == 0
    assert results["do_nothing"]["recovered_inr"] == 0.0


def test_every_policy_is_reported():
    results = run_comparison(BATCH, seed=42)
    assert set(results) == {"do_nothing", "naive_retry_all", "agent_policy"}


def test_results_are_deterministic_for_a_fixed_seed():
    assert run_comparison(BATCH, seed=42) == run_comparison(BATCH, seed=42)


def test_different_seeds_can_produce_different_results():
    a = run_comparison(BATCH, seed=1)
    b = run_comparison(BATCH, seed=999)
    assert a != b


def test_recovered_amount_never_exceeds_amount_at_risk():
    results = run_comparison(BATCH, seed=42)
    total_at_risk = sum(p["amount_inr"] for p in BATCH)
    for policy in results.values():
        assert policy["recovered_inr"] <= total_at_risk


def test_no_policy_exceeds_three_attempts_per_payment():
    results = run_comparison(BATCH, seed=42)
    for policy in results.values():
        assert policy["attempts"] <= len(BATCH) * 3
