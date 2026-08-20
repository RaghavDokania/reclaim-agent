import pytest

from simulate_outcome import simulate_outcome


def test_retry_payment_succeeds_when_roll_is_under_the_assumed_rate():
    result = simulate_outcome("retry_payment", rand_func=lambda: 0.30)
    assert result.success is True
    assert result.assumed_rate == 0.35
    assert result.simulated is True


def test_retry_payment_fails_when_roll_is_over_the_assumed_rate():
    result = simulate_outcome("retry_payment", rand_func=lambda: 0.40)
    assert result.success is False


def test_send_payment_link_uses_its_own_assumed_rate():
    result = simulate_outcome("send_payment_link", rand_func=lambda: 0.45)
    assert result.success is True
    assert result.assumed_rate == 0.50


def test_prompt_card_update_uses_its_own_assumed_rate():
    result = simulate_outcome("prompt_card_update", rand_func=lambda: 0.40)
    assert result.success is True
    assert result.assumed_rate == 0.45


def test_unknown_action_raises_value_error():
    with pytest.raises(ValueError):
        simulate_outcome("do_something_unsupported", rand_func=lambda: 0.0)
