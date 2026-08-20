"""
v1 root-cause classifier for failed payments.

Primary signal is a keyword match against the free-text error_reason.
Real payment gateways only guarantee a coarse, shared error_code (e.g.
GATEWAY_ERROR covers both card declines and failed authentication) --
the code alone can't disambiguate, so it's only used as a fallback when
the reason text is empty, ambiguous, or matches more than one category.
"""

from dataclasses import dataclass


@dataclass
class ClassificationResult:
    root_cause: str
    confidence: str   # "high" | "low"
    method: str        # "keyword" | "code_fallback"


KEYWORD_RULES = {
    "insufficient_funds": [
        "insufficient", "low balance", "sufficient funds", "balance is less",
    ],
    "expired_card": [
        "expired", "expiry date has passed", "non-expired",
    ],
    "network_timeout": [
        "timeout", "timed out", "unable to reach", "did not respond",
        "connection to the bank server",
    ],
    "auth_failure": [
        "otp", "3ds", "3d secure", "authentication", "two-factor",
        "not authorized",
    ],
    "card_declined_by_issuer": [
        "issuing bank", "issuer declined", "card issuer",
        "declined by your card issuer", "declined by the issuer",
    ],
}

# Fallback when the reason text doesn't confidently resolve: pick the
# most common root cause historically associated with that error_code.
CODE_FALLBACK = {
    "BAD_REQUEST_ERROR": "insufficient_funds",
    "GATEWAY_ERROR": "auth_failure",
    "SERVER_ERROR": "network_timeout",
}

DEFAULT_FALLBACK = "auth_failure"


def classify(error_reason: str, error_code: str) -> ClassificationResult:
    reason = (error_reason or "").lower()

    matches = [
        root_cause for root_cause, keywords in KEYWORD_RULES.items()
        if any(keyword in reason for keyword in keywords)
    ]

    if len(matches) == 1:
        return ClassificationResult(root_cause=matches[0], confidence="high", method="keyword")

    fallback = CODE_FALLBACK.get(error_code, DEFAULT_FALLBACK)
    return ClassificationResult(root_cause=fallback, confidence="low", method="code_fallback")
