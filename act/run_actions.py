"""
Executes every failed_payments row that decide/ marked action_taken and
whose next_action_at has arrived: makes a real Razorpay test-mode API
call (order for retries, payment link for the other two actions), logs
it, then simulates whether the customer completed it. Success ->
recovered. Failure -> bumps attempt_count and drops status back to
'diagnosed' so decide/run_decisions.py picks it up again next run
(bounded by decide's max-attempts stopping rule).

Run:
    python run_actions.py
"""

import os
import sys
import time
from datetime import datetime, timezone

import razorpay

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "log"))
from db import get_client as get_supabase_client, log_event, update_payment  # noqa: E402

from razorpay_client import (  # noqa: E402
    create_recovery_payment_link,
    create_retry_order,
    get_client as get_razorpay_client,
)
from simulate_outcome import simulate_outcome  # noqa: E402

# Razorpay test mode rate-limits aggressively; a short pause between calls
# keeps a 20-30 row batch from tripping it.
DELAY_BETWEEN_CALLS_SECONDS = 1.5

LINK_REASONS = {
    "send_payment_link": "please retry your payment",
    "prompt_card_update": "please update your card details and retry",
}


def run_eligible_actions(supabase, razorpay_client):
    now = datetime.now(timezone.utc)
    rows = (
        supabase.table("failed_payments")
        .select("*")
        .eq("status", "action_taken")
        .lte("next_action_at", now.isoformat())
        .execute()
        .data
    )

    results = {"recovered": 0, "failed_retry_pending": 0, "api_error_skipped": 0}

    for row in rows:
        action = row["action_taken"]
        payment_id = row["payment_id"]
        amount = row["amount_inr"]

        try:
            if action == "retry_payment":
                artifact = create_retry_order(razorpay_client, payment_id, amount)
            else:
                artifact = create_recovery_payment_link(razorpay_client, payment_id, amount, LINK_REASONS[action])
        except razorpay.errors.BadRequestError as e:
            # Razorpay test-mode rate limit or a bad payload -- leave the
            # row's status untouched so it's picked up and retried on the
            # next run, rather than losing the batch or corrupting state.
            log_event(supabase, payment_id, "action_error", {"action": action, "error": str(e)})
            results["api_error_skipped"] += 1
            time.sleep(DELAY_BETWEEN_CALLS_SECONDS)
            continue

        log_event(supabase, payment_id, "action_executed", {"action": action, **artifact})
        time.sleep(DELAY_BETWEEN_CALLS_SECONDS)

        outcome = simulate_outcome(action)

        if outcome.success:
            update_payment(supabase, payment_id, status="recovered", recovered_amount_inr=amount)
            log_event(supabase, payment_id, "recovered", {
                "simulated": outcome.simulated, "assumed_rate": outcome.assumed_rate,
            })
            results["recovered"] += 1
        else:
            update_payment(supabase, payment_id, status="diagnosed", attempt_count=row["attempt_count"] + 1)
            log_event(supabase, payment_id, "action_failed", {
                "simulated": outcome.simulated, "assumed_rate": outcome.assumed_rate,
            })
            results["failed_retry_pending"] += 1

    return len(rows), results


def main():
    supabase = get_supabase_client()
    razorpay_client = get_razorpay_client()
    total, results = run_eligible_actions(supabase, razorpay_client)
    print(f"Executed {total} eligible actions.")
    print(f"  recovered:             {results['recovered']}")
    print(f"  failed, retry pending: {results['failed_retry_pending']}")
    print(f"  api error, skipped:    {results['api_error_skipped']}")


if __name__ == "__main__":
    main()
