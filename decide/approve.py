"""
Human review queue for payments the decide layer held back.

decide/policy.py refuses to auto-action two kinds of payment: one whose
diagnosis came back low-confidence (status needs_review) and one at or
above the Rs 20,000 value threshold (status needs_approval). Without this
script those statuses are a dead end -- run_decisions.py only picks up
'diagnosed' rows and run_actions.py only 'action_taken' ones, so a held
payment sits there forever and the escalation is nominal rather than real.

Releasing a payment sets its status back to 'diagnosed' and writes a
'human_approved' audit event, so the trail shows a person authorised the
action rather than the agent deciding it was fine after all. The next
run_decisions.py pass then decides it normally.

Run:
    python approve.py --list
    python approve.py --approve pay_XXXXXXXXXXXX
"""

import argparse
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "log"))
from db import get_client, log_event, update_payment  # noqa: E402

HELD_STATUSES = ("needs_review", "needs_approval")


def list_held(client):
    rows = (
        client.table("failed_payments")
        .select("*")
        .in_("status", list(HELD_STATUSES))
        .execute()
        .data
    )
    return rows


def approve(client, payment_id: str):
    """Release one held payment back into the pipeline. Returns the row's
    held status, or None if the payment isn't held (or doesn't exist) --
    the caller reports that rather than silently 'approving' nothing."""
    rows = (
        client.table("failed_payments")
        .select("*")
        .eq("payment_id", payment_id)
        .execute()
        .data
    )
    if not rows:
        return None

    row = rows[0]
    if row["status"] not in HELD_STATUSES:
        return None

    update_payment(client, payment_id, status="diagnosed")
    log_event(
        client,
        payment_id,
        "human_approved",
        {
            "released_from": row["status"],
            "amount_inr": row["amount_inr"],
            "predicted_root_cause": row.get("predicted_root_cause"),
            "diagnosis_confidence": row.get("diagnosis_confidence"),
            "note": "human released this payment for automated action",
        },
    )
    return row["status"]


def main():
    parser = argparse.ArgumentParser(description="Review payments held for a human.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="list every held payment")
    group.add_argument("--approve", metavar="PAYMENT_ID", help="release one held payment")
    args = parser.parse_args()

    client = get_client()

    if args.list:
        rows = list_held(client)
        if not rows:
            print("Nothing held for review or approval.")
            return
        total = sum(r["amount_inr"] for r in rows)
        print(f"{len(rows)} payment(s) held, Rs {total:,.2f} awaiting a human decision:\n")
        print(f"{'payment_id':<24} {'status':<16} {'Rs':>12}  {'root cause':<26} confidence")
        print("-" * 92)
        for row in rows:
            print(
                f"{row['payment_id']:<24} {row['status']:<16} {row['amount_inr']:>12,.2f}  "
                f"{str(row.get('predicted_root_cause')):<26} {row.get('diagnosis_confidence')}"
            )
        print("\nRelease one with: python approve.py --approve <payment_id>")
        return

    released_from = approve(client, args.approve)
    if released_from is None:
        print(f"{args.approve} is not held for review or approval -- nothing to do.")
        return
    print(
        f"Released {args.approve} (was {released_from}) back to 'diagnosed' and logged "
        f"'human_approved'. Run decide/run_decisions.py to action it."
    )


if __name__ == "__main__":
    main()
