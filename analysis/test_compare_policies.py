from compare_policies import COMPARISON_SEEDS, multi_seed_summary, run_comparison

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


def test_a_payments_result_is_independent_of_other_payments_in_the_batch():
    # Randomness must be keyed per (seed, payment_id), not drawn from one
    # shared stream per policy -- otherwise how many draws one payment
    # consumes shifts every later payment's draws, and a batch's result
    # stops being the sum of each payment's own, isolated result. This is
    # the additivity that per-payment-keyed randomness guarantees and a
    # single shared stream does not. (A 2-payment batch isn't enough to
    # expose this reliably -- the first payment in any batch always sits
    # at draw 0 of a shared stream, so it takes several payments before
    # a shared stream's cumulative drift shows up.)
    sub_batch = BATCH[:10]
    combined = run_comparison(sub_batch, seed=42)
    isolated_sum = {
        policy: {
            "recovered_count": sum(
                run_comparison([p], seed=42)[policy]["recovered_count"] for p in sub_batch
            ),
            "attempts": sum(
                run_comparison([p], seed=42)[policy]["attempts"] for p in sub_batch
            ),
        }
        for policy in ("naive_retry_all", "agent_policy")
    }

    for policy in ("naive_retry_all", "agent_policy"):
        assert combined[policy]["recovered_count"] == isolated_sum[policy]["recovered_count"]
        assert combined[policy]["attempts"] == isolated_sum[policy]["attempts"]


def test_reordering_the_batch_does_not_change_the_result():
    # A corollary of per-payment-keyed randomness: the outcome for the
    # batch as a whole cannot depend on what order the payments are
    # processed in. A batch of content-identical payments can't expose an
    # ordering bug (swapping two identical items is a no-op), so this uses
    # a heterogeneous batch mixing several root causes / amounts.
    reasons_and_codes = [
        ("the request timed out while contacting the bank", "SERVER_ERROR"),
        ("authentication failed via otp", "GATEWAY_ERROR"),
        ("card declined by the issuing bank", "GATEWAY_ERROR"),
        ("the card has expired", "BAD_REQUEST_ERROR"),
        ("insufficient balance in account", "BAD_REQUEST_ERROR"),
    ]
    mixed_batch = [
        {
            "payment_id": f"pay_{i}",
            "amount_inr": 1000.0 + i,
            "root_cause": "unused",
            "error_reason": reasons_and_codes[i % len(reasons_and_codes)][0],
            "error_code": reasons_and_codes[i % len(reasons_and_codes)][1],
            "created_at": "2026-08-20T10:00:00+00:00",
            "attempt_count": 1,
        }
        for i in range(15)
    ]

    forward = run_comparison(mixed_batch, seed=42)
    backward = run_comparison(list(reversed(mixed_batch)), seed=42)
    assert forward == backward


def test_comparison_seeds_is_a_fixed_list_of_at_least_twenty_seeds():
    assert isinstance(COMPARISON_SEEDS, list)
    assert len(COMPARISON_SEEDS) >= 20
    assert len(set(COMPARISON_SEEDS)) == len(COMPARISON_SEEDS)  # no duplicates


def test_multi_seed_summary_is_deterministic():
    assert multi_seed_summary(BATCH, COMPARISON_SEEDS) == multi_seed_summary(BATCH, COMPARISON_SEEDS)


def test_multi_seed_summary_reports_median_and_range_consistently():
    summary = multi_seed_summary(BATCH, COMPARISON_SEEDS)
    assert summary["min_lift_pct"] <= summary["median_lift_pct"] <= summary["max_lift_pct"]
    assert len(summary["lift_pcts"]) == len(COMPARISON_SEEDS)
