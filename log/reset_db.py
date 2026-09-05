"""
Wipes both Supabase tables so a verification run starts from a clean,
coherent state. Deletes audit_log first, then failed_payments, because
audit_log.payment_id references it.

This is destructive and there is no undo, so it prints what it is about
to destroy and refuses to act without --yes. The pre-reset counts it
prints are worth keeping: they are the evidence of what state a run
started from.

It does NOT reload the batch -- that is ingest/load_batch.py's job, so
that reloading with or without --rebase-timestamps stays an explicit
choice rather than a side effect of resetting.

Run:
    python reset_db.py            # show current state, change nothing
    python reset_db.py --yes      # actually wipe both tables
"""

import argparse
import collections
import sys

from db import get_client


def current_state(client):
    """Returns (payment_count, status_counts, audit_count, event_counts)."""
    payments = client.table("failed_payments").select("status").execute().data
    events = client.table("audit_log").select("event").execute().data
    return (
        len(payments),
        collections.Counter(p["status"] for p in payments),
        len(events),
        collections.Counter(e["event"] for e in events),
    )


def print_state(label, client):
    payments, statuses, events, event_counts = current_state(client)
    print(f"{label}:")
    print(f"  failed_payments rows: {payments}  {dict(statuses)}")
    print(f"  audit_log rows:       {events}  {dict(event_counts)}")
    return payments, events


def wipe(client):
    """Delete every row from both tables, audit_log first (FK order).
    The .neq() filters are a no-op predicate -- PostgREST requires a
    filter on delete, it will not accept an unqualified 'delete all'."""
    client.table("audit_log").delete().neq("id", -1).execute()
    client.table("failed_payments").delete().neq("payment_id", "__none__").execute()


def main():
    parser = argparse.ArgumentParser(description="Wipe the Supabase tables for a clean verification run.")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="actually delete; without it this only reports the current state",
    )
    args = parser.parse_args()

    client = get_client()
    print_state("Before", client)

    if not args.yes:
        print("\nDry run -- nothing deleted. Re-run with --yes to wipe both tables.")
        return

    print("\nDeleting audit_log, then failed_payments ...")
    wipe(client)

    payments, events = print_state("After", client)
    if payments or events:
        print("\n!!! Tables are not empty after the delete -- do NOT reload on top of this.", file=sys.stderr)
        sys.exit(1)

    print("\nClean. Next: cd ../ingest && python load_batch.py --rebase-timestamps")


if __name__ == "__main__":
    main()
