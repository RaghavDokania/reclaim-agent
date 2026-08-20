from compare_policies import run_comparison

BATCH = [
    {
        "payment_id": f"pay_{i}",
        "amount_inr": 1000.0,
        "root_cause": "network_timeout",
        "error_reason": "the request timed out while contacting the bank",
        "error_code": "SERVER_ERROR",
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


def test_naive_retry_respects_same_lifetime_attempt_budget_as_agent_policy():
    # With attempt_count already at 2, MAX_ATTEMPTS (3) leaves exactly one
    # lifetime attempt for either policy -- decide() exhausts the agent
    # after that one attempt regardless of outcome, and naive must be held
    # to the same remaining budget rather than a fresh MAX_ATTEMPTS.
    batch = [{**payment, "attempt_count": 2} for payment in BATCH]
    results = run_comparison(batch, seed=42)
    assert results["naive_retry_all"]["attempts"] == len(batch)
    assert results["agent_policy"]["attempts"] == len(batch)


def test_naive_retry_gets_zero_attempts_when_lifetime_budget_exhausted():
    # attempt_count already at MAX_ATTEMPTS -- decide() reports "exhausted"
    # immediately, so naive's remaining budget must also be zero, not a
    # fresh MAX_ATTEMPTS handed out regardless of prior attempts.
    batch = [{**payment, "attempt_count": 3} for payment in BATCH]
    results = run_comparison(batch, seed=42)
    assert results["naive_retry_all"]["attempts"] == 0
    assert results["agent_policy"]["attempts"] == 0


def test_agent_policy_routes_on_classified_cause_not_the_root_cause_label():
    # The live pipeline routes on classify()'s predicted cause, not the
    # synthetic ground-truth root_cause label -- the label must not leak
    # into the agent's decision. Two batches that agree on error_reason /
    # error_code (what classify() sees) but disagree on the label must
    # produce identical agent_policy results.
    base = {
        "amount_inr": 1000.0,
        "error_reason": "the request timed out while contacting the bank",
        "error_code": "SERVER_ERROR",
        "created_at": "2026-08-20T10:00:00+00:00",
        "attempt_count": 1,
    }
    contradictory_batch = [
        {**base, "payment_id": f"pay_{i}", "root_cause": "expired_card"}
        for i in range(30)
    ]
    matching_batch = [
        {**base, "payment_id": f"pay_{i}", "root_cause": "network_timeout"}
        for i in range(30)
    ]

    contradictory_results = run_comparison(contradictory_batch, seed=42)
    matching_results = run_comparison(matching_batch, seed=42)

    assert contradictory_results["agent_policy"] == matching_results["agent_policy"]
