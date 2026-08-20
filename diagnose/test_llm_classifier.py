import pytest

from llm_classifier import classify_with_llm

VALID = {
    "insufficient_funds", "card_declined_by_issuer", "auth_failure",
    "network_timeout", "expired_card",
}


def test_clear_keyword_match_never_calls_the_llm():
    def exploding_llm(prompt):
        raise AssertionError("LLM must not be called for a clear keyword match")

    result = classify_with_llm("Card has expired", "BAD_REQUEST_ERROR", llm_func=exploding_llm)
    assert result.root_cause == "expired_card"
    assert result.method == "keyword"
    assert result.confidence == "high"


def test_ambiguous_reason_escalates_to_the_llm():
    def fake_llm(prompt):
        return '{"root_cause": "card_declined_by_issuer", "reasoning": "issuer-side decline"}'

    result = classify_with_llm("Transaction declined by bank", "GATEWAY_ERROR", llm_func=fake_llm)
    assert result.root_cause == "card_declined_by_issuer"
    assert result.method == "llm"
    assert result.confidence == "high"
    assert result.reasoning == "issuer-side decline"


def test_llm_prompt_contains_the_error_reason_and_code():
    captured = {}

    def fake_llm(prompt):
        captured["prompt"] = prompt
        return '{"root_cause": "auth_failure", "reasoning": "ok"}'

    classify_with_llm("Transaction declined by bank", "GATEWAY_ERROR", llm_func=fake_llm)
    assert "Transaction declined by bank" in captured["prompt"]
    assert "GATEWAY_ERROR" in captured["prompt"]


def test_llm_returning_an_invalid_root_cause_falls_back_to_error_code():
    def fake_llm(prompt):
        return '{"root_cause": "customer_changed_their_mind", "reasoning": "made up"}'

    result = classify_with_llm("Transaction declined by bank", "GATEWAY_ERROR", llm_func=fake_llm)
    assert result.root_cause in VALID
    assert result.method == "code_fallback"
    assert result.confidence == "low"


def test_llm_returning_unparseable_text_falls_back_to_error_code():
    def fake_llm(prompt):
        return "I'm not sure, could be a few things honestly"

    result = classify_with_llm("Transaction declined by bank", "GATEWAY_ERROR", llm_func=fake_llm)
    assert result.method == "code_fallback"
    assert result.confidence == "low"


def test_llm_raising_an_exception_falls_back_to_error_code():
    def exploding_llm(prompt):
        raise RuntimeError("groq is down")

    result = classify_with_llm("Transaction declined by bank", "GATEWAY_ERROR", llm_func=exploding_llm)
    assert result.method == "code_fallback"
    assert result.confidence == "low"
    assert result.root_cause in VALID


def test_llm_response_wrapped_in_markdown_fences_is_parsed():
    def fake_llm(prompt):
        return '```json\n{"root_cause": "network_timeout", "reasoning": "gateway side"}\n```'

    result = classify_with_llm("Transaction declined by bank", "SERVER_ERROR", llm_func=fake_llm)
    assert result.root_cause == "network_timeout"
    assert result.method == "llm"
