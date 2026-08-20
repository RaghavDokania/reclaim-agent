"""
Decision layer for the reclaim-agent pipeline.

Maps a diagnosed root cause to a bounded recovery action, subject to
three controls applied in this order:

1. Stopping rules -- at most MAX_ATTEMPTS lifetime attempts, and nothing
   older than STOPPING_WINDOW_HOURS gets chased, so the agent never
   pursues a payment indefinitely.
2. Confidence gate -- a low-confidence diagnosis is routed to human
   review rather than acted on, so money never moves on a guess.
3. Value gate -- anything at or above HIGH_VALUE_THRESHOLD_INR needs
   human approval rather than auto-executing.

The stopping rules come first on purpose: a payment that is already
finished stays finished rather than landing in a human queue. The two
gates return no action; decide/approve.py is how a human releases one.

This module only decides -- it never calls Razorpay or touches Supabase;
that's run_decisions.py's job.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

MAX_ATTEMPTS = 3
STOPPING_WINDOW_HOURS = 72
HIGH_VALUE_THRESHOLD_INR = 20000.0

# root_cause -> (action, delay_hours before the action is eligible to run)
POLICY = {
    "insufficient_funds": ("retry_payment", 6),
    "network_timeout": ("retry_payment", 0),
    "auth_failure": ("send_payment_link", 0),
    "card_declined_by_issuer": ("send_payment_link", 0),
    "expired_card": ("prompt_card_update", 0),
}


@dataclass
class DecisionResult:
    action: Optional[str]
    status: str                    # "action_taken" | "exhausted" | "needs_review" | "needs_approval"
    next_action_at: Optional[datetime]
    reason: str


def decide(
    root_cause: str,
    created_at: datetime,
    attempt_count: int,
    now: datetime,
    confidence: str = "high",
    amount_inr: float = 0.0,
) -> DecisionResult:
    if attempt_count >= MAX_ATTEMPTS:
        return DecisionResult(
            action=None, status="exhausted", next_action_at=None,
            reason=f"hit max attempt limit ({MAX_ATTEMPTS})",
        )

    age_hours = (now - created_at).total_seconds() / 3600
    if age_hours > STOPPING_WINDOW_HOURS:
        return DecisionResult(
            action=None, status="exhausted", next_action_at=None,
            reason=f"older than {STOPPING_WINDOW_HOURS}h stopping window ({age_hours:.1f}h old)",
        )

    # Gates below hold a payment back from an automatic money action. They
    # come after the stopping rules on purpose: a payment that is already
    # finished stays finished rather than landing in a human queue.
    if confidence == "low":
        return DecisionResult(
            action=None, status="needs_review", next_action_at=None,
            reason="low confidence diagnosis -- not acting on a guess, routing to human review",
        )

    if amount_inr >= HIGH_VALUE_THRESHOLD_INR:
        return DecisionResult(
            action=None, status="needs_approval", next_action_at=None,
            reason=f"amount Rs {amount_inr:,.2f} is at or above the Rs {HIGH_VALUE_THRESHOLD_INR:,.0f} auto-action threshold",
        )

    action, delay_hours = POLICY[root_cause]
    next_action_at = now + timedelta(hours=delay_hours)
    return DecisionResult(
        action=action, status="action_taken", next_action_at=next_action_at,
        reason=f"{root_cause} -> {action}",
    )
