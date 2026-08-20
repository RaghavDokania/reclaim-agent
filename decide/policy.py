"""
Decision layer for the reclaim-agent pipeline.

Maps a diagnosed root cause to a bounded recovery action, gated by a
stopping rule so the agent never chases a payment indefinitely. This
module only decides -- it never calls Razorpay or touches Supabase;
that's run_decisions.py's job.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

MAX_ATTEMPTS = 3
STOPPING_WINDOW_HOURS = 72

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
    status: str                    # "action_taken" | "exhausted"
    next_action_at: Optional[datetime]
    reason: str


def decide(root_cause: str, created_at: datetime, attempt_count: int, now: datetime) -> DecisionResult:
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

    action, delay_hours = POLICY[root_cause]
    next_action_at = now + timedelta(hours=delay_hours)
    return DecisionResult(
        action=action, status="action_taken", next_action_at=next_action_at,
        reason=f"{root_cause} -> {action}",
    )
