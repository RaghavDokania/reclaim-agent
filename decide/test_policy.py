from datetime import datetime, timedelta

from policy import decide

NOW = datetime(2026, 8, 20, 12, 0, 0)


def test_insufficient_funds_retries_after_six_hour_delay():
    result = decide("insufficient_funds", NOW, attempt_count=1, now=NOW)
    assert result.action == "retry_payment"
    assert result.status == "action_taken"
    assert result.next_action_at == NOW + timedelta(hours=6)


def test_network_timeout_retries_immediately():
    result = decide("network_timeout", NOW, attempt_count=1, now=NOW)
    assert result.action == "retry_payment"
    assert result.status == "action_taken"
    assert result.next_action_at == NOW


def test_auth_failure_sends_payment_link_immediately():
    result = decide("auth_failure", NOW, attempt_count=1, now=NOW)
    assert result.action == "send_payment_link"
    assert result.status == "action_taken"
    assert result.next_action_at == NOW


def test_card_declined_by_issuer_sends_payment_link_immediately():
    result = decide("card_declined_by_issuer", NOW, attempt_count=1, now=NOW)
    assert result.action == "send_payment_link"
    assert result.status == "action_taken"
    assert result.next_action_at == NOW


def test_expired_card_prompts_card_update_immediately():
    result = decide("expired_card", NOW, attempt_count=1, now=NOW)
    assert result.action == "prompt_card_update"
    assert result.status == "action_taken"
    assert result.next_action_at == NOW


def test_exhausted_when_max_attempts_reached():
    result = decide("insufficient_funds", NOW, attempt_count=3, now=NOW)
    assert result.action is None
    assert result.status == "exhausted"
    assert result.next_action_at is None
    assert "attempt" in result.reason.lower()


def test_exhausted_when_older_than_72_hours():
    created_at = NOW - timedelta(hours=73)
    result = decide("insufficient_funds", created_at, attempt_count=1, now=NOW)
    assert result.action is None
    assert result.status == "exhausted"
    assert result.next_action_at is None
    assert "72" in result.reason or "hour" in result.reason.lower()


def test_still_eligible_just_under_72_hours():
    created_at = NOW - timedelta(hours=71)
    result = decide("network_timeout", created_at, attempt_count=1, now=NOW)
    assert result.status == "action_taken"


def test_low_confidence_diagnosis_routes_to_review_instead_of_acting():
    result = decide("auth_failure", NOW, attempt_count=1, now=NOW, confidence="low")
    assert result.status == "needs_review"
    assert result.action is None
    assert result.next_action_at is None
    assert "confidence" in result.reason.lower()


def test_high_value_payment_routes_to_approval_instead_of_acting():
    result = decide("auth_failure", NOW, attempt_count=1, now=NOW, amount_inr=25000.0)
    assert result.status == "needs_approval"
    assert result.action is None
    assert result.next_action_at is None
    assert "20,000" in result.reason or "20000" in result.reason


def test_payment_exactly_at_the_high_value_threshold_needs_approval():
    # The threshold is inclusive: Rs 20,000 exactly is the boundary the
    # policy claims to hold, so the boundary itself must be held. Tested
    # explicitly because "at or above" is exactly the claim a reviewer
    # checks, and an off-by-one here auto-actions a payment nobody
    # authorised.
    result = decide("auth_failure", NOW, attempt_count=1, now=NOW, amount_inr=20000.0)
    assert result.status == "needs_approval"
    assert result.action is None
    assert result.next_action_at is None


def test_payment_just_under_the_high_value_threshold_still_acts():
    result = decide("auth_failure", NOW, attempt_count=1, now=NOW, amount_inr=19999.0)
    assert result.status == "action_taken"


def test_stopping_rules_take_precedence_over_the_new_gates():
    # An exhausted payment is finished -- it must not be resurrected into a
    # review queue by a low-confidence diagnosis.
    result = decide("auth_failure", NOW, attempt_count=3, now=NOW, confidence="low", amount_inr=25000.0)
    assert result.status == "exhausted"


def test_age_exhaustion_takes_precedence_over_both_gates():
    # The attempt-exhaustion equivalent is covered above; this pins the
    # other stopping rule against the same combination. A payment past the
    # 72h window is finished and must not be resurrected into a human
    # queue by either gate.
    created_at = NOW - timedelta(hours=73)
    result = decide(
        "auth_failure", created_at, attempt_count=1, now=NOW,
        confidence="low", amount_inr=25000.0,
    )
    assert result.status == "exhausted"
    assert result.action is None


def test_confidence_gate_takes_precedence_over_the_value_gate():
    result = decide("auth_failure", NOW, attempt_count=1, now=NOW, confidence="low", amount_inr=25000.0)
    assert result.status == "needs_review"


def test_defaults_preserve_the_original_behaviour():
    result = decide("network_timeout", NOW, attempt_count=1, now=NOW)
    assert result.status == "action_taken"
    assert result.action == "retry_payment"
