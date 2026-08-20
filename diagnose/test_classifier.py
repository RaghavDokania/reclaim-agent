from classifier import classify


def test_classifies_insufficient_funds_from_clear_keyword():
    result = classify("Insufficient balance in customer's account", "BAD_REQUEST_ERROR")
    assert result.root_cause == "insufficient_funds"
    assert result.confidence == "high"
    assert result.method == "keyword"


def test_classifies_expired_card_from_clear_keyword():
    result = classify("Card has expired", "BAD_REQUEST_ERROR")
    assert result.root_cause == "expired_card"
    assert result.confidence == "high"
    assert result.method == "keyword"


def test_classifies_network_timeout_from_clear_keyword():
    result = classify("Gateway timeout while contacting bank", "SERVER_ERROR")
    assert result.root_cause == "network_timeout"
    assert result.confidence == "high"
    assert result.method == "keyword"


def test_classifies_auth_failure_from_clear_keyword():
    result = classify("Customer did not complete two-factor authentication in time", "GATEWAY_ERROR")
    assert result.root_cause == "auth_failure"
    assert result.confidence == "high"
    assert result.method == "keyword"


def test_reason_mentioning_both_otp_and_timeout_is_genuinely_ambiguous():
    # "timed out" here describes the OTP step timing out, not a gateway
    # timeout -- a keyword matcher can't tell these apart, so it should
    # defer to the fallback rather than confidently guess either way.
    result = classify("OTP/3DS authentication failed or timed out", "GATEWAY_ERROR")
    assert result.method == "code_fallback"
    assert result.confidence == "low"


def test_classifies_card_declined_by_issuer_from_clear_keyword():
    result = classify("Card declined by issuing bank", "GATEWAY_ERROR")
    assert result.root_cause == "card_declined_by_issuer"
    assert result.confidence == "high"
    assert result.method == "keyword"


def test_ambiguous_reason_falls_back_to_error_code():
    # "Transaction declined by bank" mentions neither "issuer"/"issuing"
    # nor any auth-specific term, so no keyword rule should confidently
    # claim it -- this is the case that must NOT be 100% accurate.
    result = classify("Transaction declined by bank", "GATEWAY_ERROR")
    assert result.method == "code_fallback"
    assert result.confidence == "low"


def test_reason_matching_multiple_categories_falls_back_to_error_code():
    result = classify("Card declined due to insufficient funds and expired card check", "BAD_REQUEST_ERROR")
    assert result.method == "code_fallback"
    assert result.confidence == "low"


def test_missing_reason_falls_back_to_error_code():
    result = classify("", "SERVER_ERROR")
    assert result.root_cause == "network_timeout"
    assert result.method == "code_fallback"
    assert result.confidence == "low"


def test_unknown_error_code_and_reason_still_returns_a_root_cause():
    result = classify("something completely unrecognized happened", "UNKNOWN_CODE")
    assert result.root_cause in {
        "insufficient_funds", "card_declined_by_issuer", "auth_failure",
        "network_timeout", "expired_card",
    }
    assert result.method == "code_fallback"
