"""
Shared schema for the reclaim-agent pipeline.
Every failed payment, whether it comes from a synthetic batch or a real
Razorpay test-mode webhook, gets normalized into this shape before it
moves through diagnose -> decide -> act -> log.
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
from datetime import datetime


class RootCause(str, Enum):
    INSUFFICIENT_FUNDS = "insufficient_funds"
    CARD_DECLINED_BY_ISSUER = "card_declined_by_issuer"
    AUTH_FAILURE = "auth_failure"          # OTP / 3DS failed or expired
    NETWORK_TIMEOUT = "network_timeout"    # gateway/bank connectivity issue
    EXPIRED_CARD = "expired_card"


class ErrorSource(str, Enum):
    CUSTOMER = "customer"   # e.g. wrong OTP, expired card
    BANK = "bank"            # e.g. insufficient funds, issuer decline
    GATEWAY = "gateway"      # e.g. network timeout


class Status(str, Enum):
    NEEDS_DIAGNOSIS = "needs_diagnosis"
    DIAGNOSED = "diagnosed"
    ACTION_TAKEN = "action_taken"
    RECOVERED = "recovered"
    EXHAUSTED = "exhausted"   # hit stopping rule, gave up
    NEEDS_REVIEW = "needs_review"        # low-confidence diagnosis, held for a human
    NEEDS_APPROVAL = "needs_approval"    # high-value, held for explicit sign-off


# Maps each root cause to who/what it stems from -- your diagnosis layer
# will use this to decide the intervention (customer-side issues need a
# nudge to the customer, gateway issues just need a retry, etc.)
ROOT_CAUSE_SOURCE = {
    RootCause.INSUFFICIENT_FUNDS: ErrorSource.BANK,
    RootCause.CARD_DECLINED_BY_ISSUER: ErrorSource.BANK,
    RootCause.AUTH_FAILURE: ErrorSource.CUSTOMER,
    RootCause.NETWORK_TIMEOUT: ErrorSource.GATEWAY,
    RootCause.EXPIRED_CARD: ErrorSource.CUSTOMER,
}


@dataclass
class FailedPayment:
    payment_id: str
    order_id: str
    amount_inr: float
    currency: str
    error_code: str            # raw code, as it would arrive from Razorpay
    error_reason: str          # human-readable reason string
    root_cause: RootCause      # ground-truth label (only exists because this is synthetic data)
    created_at: str            # ISO timestamp of the failure
    customer_email: str
    customer_phone: str
    attempt_count: int = 1
    status: Status = Status.NEEDS_DIAGNOSIS
    audit_log: list = field(default_factory=list)

    def to_dict(self):
        d = asdict(self)
        d["root_cause"] = self.root_cause.value
        d["status"] = self.status.value
        return d
