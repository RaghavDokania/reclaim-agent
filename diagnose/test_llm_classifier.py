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
    # The stub now reports its own confidence, because the model is asked
    # for one. A response that omits the field no longer means "high" -- it
    # means unknown, which maps to "low"; see the omitted-field test below.
    def fake_llm(prompt):
        return ('{"root_cause": "card_declined_by_issuer", "confidence": "high", '
                '"reasoning": "issuer-side decline"}')

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


def test_llm_reporting_low_confidence_is_recorded_as_low_confidence():
    # The confidence gate exists to stop the agent acting on a guess. If the
    # model says the reason text does not distinguish two causes, that has
    # to reach decide() as "low" rather than being overwritten with "high".
    def fake_llm(prompt):
        return ('{"root_cause": "card_declined_by_issuer", "confidence": "low", '
                '"reasoning": "could equally be an auth failure"}')

    result = classify_with_llm("Transaction declined by bank", "GATEWAY_ERROR", llm_func=fake_llm)
    assert result.confidence == "low"
    assert result.root_cause == "card_declined_by_issuer"
    assert result.method == "llm"


def test_llm_reporting_high_confidence_is_recorded_as_high_confidence():
    def fake_llm(prompt):
        return ('{"root_cause": "expired_card", "confidence": "high", '
                '"reasoning": "the reason names an expiry date"}')

    result = classify_with_llm("Transaction declined by bank", "GATEWAY_ERROR", llm_func=fake_llm)
    assert result.confidence == "high"
    assert result.method == "llm"


def test_llm_omitting_the_confidence_field_is_treated_as_low_confidence():
    # A missing confidence means we do not know how sure the model was, and
    # the safe direction for an unknown is human review, not a money action.
    def fake_llm(prompt):
        return '{"root_cause": "auth_failure", "reasoning": "otp not entered"}'

    result = classify_with_llm("Transaction declined by bank", "GATEWAY_ERROR", llm_func=fake_llm)
    assert result.confidence == "low"
    assert result.root_cause == "auth_failure"
    assert result.method == "llm"


def test_llm_returning_an_unrecognised_confidence_value_is_treated_as_low():
    def fake_llm(prompt):
        return ('{"root_cause": "auth_failure", "confidence": "pretty sure tbh", '
                '"reasoning": "otp not entered"}')

    result = classify_with_llm("Transaction declined by bank", "GATEWAY_ERROR", llm_func=fake_llm)
    assert result.confidence == "low"
    assert result.method == "llm"


def test_the_prompt_asks_the_model_for_its_own_confidence():
    captured = {}

    def fake_llm(prompt):
        captured["prompt"] = prompt
        return '{"root_cause": "auth_failure", "confidence": "high", "reasoning": "ok"}'

    classify_with_llm("Transaction declined by bank", "GATEWAY_ERROR", llm_func=fake_llm)
    assert "confidence" in captured["prompt"]


def test_the_prompt_gives_the_model_the_concrete_ambiguous_case_as_an_example():
    # Measured live, the model returned "high" on 14 of 15 escalations and
    # on all 5 of the diagnoses it got wrong -- every one of them on
    # "Transaction declined by bank", the exact phrase the escalation layer
    # exists for. An abstract instruction to be honest was not enough, so
    # the prompt names that case and the two causes it straddles.
    captured = {}

    def fake_llm(prompt):
        captured["prompt"] = prompt
        return '{"root_cause": "auth_failure", "confidence": "low", "reasoning": "ok"}'

    classify_with_llm("Transaction declined by bank", "GATEWAY_ERROR", llm_func=fake_llm)
    prompt = captured["prompt"]
    assert "Transaction declined by bank" in prompt
    assert "card_declined_by_issuer" in prompt and "auth_failure" in prompt
    # The rule must be stated as an obligation, not a suggestion.
    assert "must" in prompt.lower()


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
