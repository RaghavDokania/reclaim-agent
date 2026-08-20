"""
LLM escalation layer for root-cause diagnosis.

The rule-based classifier in classifier.py handles unambiguous failure
reasons deterministically and for free. Only reasons it cannot resolve --
text that matches no category, or more than one -- escalate here, where a
model reads the reason and picks a cause. Roughly one failure in eight
reaches this path, so the LLM cost stays proportional to the genuinely
hard cases rather than the whole batch.

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

GROQ_MODEL = "llama-3.3-70b-versatile"

PROMPT_TEMPLATE = """You are triaging a failed payment for an Indian payment gateway.

Classify the failure into exactly one of these root causes:
- insufficient_funds: the customer's account did not have enough money
- card_declined_by_issuer: the issuing bank refused the transaction (not an authentication problem)
- auth_failure: OTP, 3D Secure, or two-factor authentication was wrong, incomplete, or abandoned
- network_timeout: a gateway or bank connectivity problem, not a customer problem
- expired_card: the card's expiry date has passed

Error code: {error_code}
Error reason: {error_reason}

Respond with JSON only, no prose:
{{"root_cause": "<one of the five above>", "reasoning": "<one short sentence>"}}"""


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

    return ClassificationResult(
        root_cause=parsed["root_cause"],
        confidence="high",
        method="llm",
        reasoning=str(parsed.get("reasoning", ""))[:300],
    )
