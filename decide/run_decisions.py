"""
Runs the decision policy over every failed_payments row still in
'diagnosed', writes the decision back to Supabase, and logs one audit
event per row -- 'decided' when an action was authorised, 'exhausted'
when a stopping rule fired, and 'held_for_review' / 'held_for_approval'
when the confidence or value gate handed the payment to a human instead.
Every one of those details records the confidence the decision was made
on and whether that confidence was defaulted rather than diagnosed.

A held payment leaves this stage in needs_review / needs_approval and is
not picked up again here; decide/approve.py is how a human releases it.
Never calls Razorpay -- that's the act layer's job, using next_action_at
to know when it's allowed to run.

Run:
    python run_decisions.py
"""

import os
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "log"))
from db import get_client, log_event, update_payment  # noqa: E402

from policy import decide


def decide_all(client):
    rows = (
        client.table("failed_payments")
        .select("*")
        .eq("status", "diagnosed")
        .execute()
        .data
    )

    now = datetime.now(timezone.utc)
    counts = Counter()

    for row in rows:
        created_at = datetime.fromisoformat(row["created_at"])
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        # Rows that predate the diagnosis_confidence column come back NULL.
        # Defaulting them to "high" keeps them moving, but that default is
        # what authorises a money action, so it is recorded in the audit
        # detail below rather than being invisible -- an auditor must be
        # able to tell "diagnosed high-confidence" from "no confidence on
        # record, assumed high".
        recorded_confidence = row.get("diagnosis_confidence")
        confidence = recorded_confidence or "high"
        confidence_defaulted = not recorded_confidence

        result = decide(
            root_cause=row["predicted_root_cause"],
            created_at=created_at,
            attempt_count=row["attempt_count"],
            now=now,
            confidence=confidence,
            amount_inr=row["amount_inr"],
            # Set by decide/approve.py when a person released this payment.
            # Without it the gate that held the row re-fires here and the
            # release is a no-op loop -- needs_approval -> diagnosed ->
            # needs_approval, forever.
            human_approved=bool(row.get("human_approved_at")),
        )

        update_payment(
            client,
            row["payment_id"],
            action_taken=result.action,
            status=result.status,
            next_action_at=result.next_action_at.isoformat() if result.next_action_at else None,
        )

        event = {
            "action_taken": "decided",
            "exhausted": "exhausted",
            "needs_review": "held_for_review",
            "needs_approval": "held_for_approval",
        }[result.status]
        log_event(
            client,
            row["payment_id"],
            event,
            {
                "action": result.action,
                "next_action_at": result.next_action_at.isoformat() if result.next_action_at else None,
                "reason": result.reason,
                "confidence": confidence,
                "confidence_defaulted": confidence_defaulted,
            },
        )
        counts[result.status] += 1

    return len(rows), counts


def main():
    client = get_client()
    total, counts = decide_all(client)
    print(f"Decided on {total} payments.")
    for status, n in counts.items():
        print(f"  {status:14s} {n}")


if __name__ == "__main__":
    main()
