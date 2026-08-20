"""
Runs the decision policy over every failed_payments row still in
'diagnosed', writes the decision back to Supabase, and logs a
'decided' or 'exhausted' audit event per row. Never calls Razorpay --
that's the act layer's job, using next_action_at to know when it's
allowed to run.

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

        result = decide(
            root_cause=row["predicted_root_cause"],
            created_at=created_at,
            attempt_count=row["attempt_count"],
            now=now,
        )

        update_payment(
            client,
            row["payment_id"],
            action_taken=result.action,
            status=result.status,
            next_action_at=result.next_action_at.isoformat() if result.next_action_at else None,
        )

        event = "decided" if result.status == "action_taken" else "exhausted"
        log_event(
            client,
            row["payment_id"],
            event,
            {
                "action": result.action,
                "next_action_at": result.next_action_at.isoformat() if result.next_action_at else None,
                "reason": result.reason,
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
