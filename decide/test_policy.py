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
