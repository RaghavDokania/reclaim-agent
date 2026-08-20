"""
LLM escalation layer for root-cause diagnosis.

The rule-based classifier in classifier.py handles unambiguous failure
reasons deterministically and for free. Only reasons it cannot resolve --
text that matches no category, or more than one -- escalate here, where a
model reads the reason and picks a cause. Measured on the committed batch
in data/failed_payments.json, 15 of 75 failures (one in five) reach this
path, so the LLM cost stays proportional to the genuinely hard cases
rather than the whole batch.

The model is asked for its own confidence, not just a cause. That answer
is what decide()'s confidence gate acts on, so a model that cannot tell
two causes apart routes the payment to a human instead of to a money
action. An absent or unrecognised confidence is read as "low" -- an
unknown is not evidence of certainty.

Any LLM failure (unreachable, malformed output, a made-up category)
degrades to the same error_code fallback the rules layer already used, so
a diagnosis is always produced.
"""

import json
import os
import re
from typing import Callable, Optional

from classifier import CODE_FALLBACK, DEFAULT_FALLBACK, ClassificationResult, classify

VALID_ROOT_CAUSES = {
    "insufficient_funds",
    "card_declined_by_issuer",
    "auth_failure",
    "network_timeout",
    "expired_card",
}

GROQ_MODEL = "openai/gpt-oss-120b"

PROMPT_TEMPLATE = """You are triaging a failed payment for an Indian payment gateway.

Classify the failure into exactly one of these root causes:
- insufficient_funds: the customer's account did not have enough money
- card_declined_by_issuer: the issuing bank refused the transaction (not an authentication problem)
- auth_failure: OTP, 3D Secure, or two-factor authentication was wrong, incomplete, or abandoned
- network_timeout: a gateway or bank connectivity problem, not a customer problem
- expired_card: the card's expiry date has passed

Also report how sure you are. Apply this test before you answer: read the
reason text and ask whether it is consistent with more than one of the
five causes above.
- "high": ONLY if the reason text names evidence that rules out every
  cause but one. The text must distinguish, not merely suggest.
- "low": if the reason text is consistent with two or more causes. You
  MUST still name your single best guess in root_cause -- "low" describes
  how sure you are, it is not permission to refuse or to hedge the cause
  field. Naming a best guess does NOT make your confidence high.

You MUST answer "low" whenever the text is consistent with more than one
cause, even if one feels more likely. Worked example, which is the most
common case you will see:

  Error reason: "Transaction declined by bank"
  This says the bank refused the transaction but not WHY. A refusal after
  a failed OTP or 3D Secure step reads exactly the same way as an
  issuer-side refusal, so the text is consistent with BOTH
  card_declined_by_issuer AND auth_failure. It does not distinguish them.
  Correct answer: root_cause "card_declined_by_issuer" (the best guess),
  confidence "low".

A "low" answer is routed to a human for review, so it costs nothing to be
honest. A wrong "high" causes money to move on a guess. When in doubt
between the two, answer "low".

Error code: {error_code}
Error reason: {error_reason}

Respond with JSON only, no prose:
{{"root_cause": "<one of the five above>", "confidence": "<high or low>", "reasoning": "<one short sentence>"}}"""

VALID_CONFIDENCES = {"high", "low"}


def _default_llm(prompt: str) -> str:
    """Real Groq call. Imported lazily so tests never need the dependency."""
    from langchain_groq import ChatGroq

    model = ChatGroq(model=GROQ_MODEL, temperature=0, api_key=os.environ["GROQ_API_KEY"])
    return model.invoke(prompt).content


def _parse(raw: str) -> Optional[dict]:
    """Pull the JSON object out of a model response, tolerating markdown fences."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def classify_with_llm(
    error_reason: str,
    error_code: str,
    llm_func: Optional[Callable[[str], str]] = None,
) -> ClassificationResult:
    rules_result = classify(error_reason, error_code)
    if rules_result.method == "keyword":
        return rules_result

    llm = llm_func or _default_llm
    prompt = PROMPT_TEMPLATE.format(error_code=error_code, error_reason=error_reason)

    try:
        parsed = _parse(llm(prompt))
    except Exception as e:  # noqa: BLE001 -- any LLM failure degrades to the rules fallback
        return ClassificationResult(
            root_cause=CODE_FALLBACK.get(error_code, DEFAULT_FALLBACK),
            confidence="low",
            method="code_fallback",
            reasoning=f"llm unavailable: {e}",
        )

    if not parsed or parsed.get("root_cause") not in VALID_ROOT_CAUSES:
        return ClassificationResult(
            root_cause=CODE_FALLBACK.get(error_code, DEFAULT_FALLBACK),
            confidence="low",
            method="code_fallback",
            reasoning="llm returned an unusable response",
        )

    # A missing or unrecognised confidence means we do not know how sure the
    # model was. Default to "low" so decide() routes it to human review --
    # the safe direction for an unknown is a person, not a money action.
    confidence = parsed.get("confidence")
    confidence = confidence if confidence in VALID_CONFIDENCES else "low"

    return ClassificationResult(
        root_cause=parsed["root_cause"],
        confidence=confidence,
        method="llm",
        reasoning=str(parsed.get("reasoning", ""))[:300],
    )
