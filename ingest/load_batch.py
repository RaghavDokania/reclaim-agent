"""
Loads data/failed_payments.json into Supabase and logs the initial
'ingested' event for every record. Run this once after you've generated
your batch and set up your Supabase table.

data/failed_payments.json holds absolute created_at timestamps from the
day it was generated, and decide()'s 72-hour stopping rule is evaluated
against wall-clock now. So the older the snapshot gets, the more of the
batch exhausts the moment it is decided, and every published figure
decays -- through no fault of the agent. --rebase-timestamps shifts the
whole batch forward by ONE constant offset (the one that moves the newest
record to now), so relative ages and spacing are preserved exactly while
the window becomes current. It rebases in memory only; the committed
snapshot on disk is never modified, because the 89.3% accuracy figure is
measured against those exact records.

Run:
    python load_batch.py
    python load_batch.py --rebase-timestamps
"""

import argparse
import json
import sys
import os
from datetime import datetime, timezone

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "log"))
from db import get_client, insert_batch, log_event  # noqa: E402


def rebase_timestamps(records, now):
    """Shift every created_at forward by the single offset that puts the
    newest record at `now`. Returns (new_records, offset) and never
    mutates the records it was given."""
    if not records:
        return [], now - now

    parsed = [datetime.fromisoformat(r["created_at"]) for r in records]
    newest = max(parsed)

    # The snapshot's timestamps are naive; match `now` to whatever the
    # data uses rather than assuming, so the subtraction is always valid.
    if newest.tzinfo is None and now.tzinfo is not None:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)
    elif newest.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    offset = now - newest
    rebased = [
        {**record, "created_at": (original + offset).isoformat()}
        for record, original in zip(records, parsed)
    ]
    return rebased, offset


def build_records(batch, rebase=False, now=None):
    """Turn the raw JSON batch into failed_payments rows. Strips fields
    the table doesn't have (audit_log lives in its own table now, not as
    a column here). Returns (records, applied_offset_or_None)."""
    records = [{k: v for k, v in p.items() if k != "audit_log"} for p in batch]
    if not rebase:
        return records, None
    records, offset = rebase_timestamps(records, now or datetime.now(timezone.utc))
    return records, offset


def main():
    parser = argparse.ArgumentParser(description="Load the failed-payment batch into Supabase.")
    parser.add_argument(
        "--rebase-timestamps",
        action="store_true",
        help="shift the batch forward so its newest record is 'now', keeping "
             "relative ages intact (the file on disk is not modified)",
    )
    args = parser.parse_args()

    with open("../data/failed_payments.json") as f:
        batch = json.load(f)

    records, offset = build_records(batch, rebase=args.rebase_timestamps)
    if offset is not None:
        # Print it so a run describes itself: anyone reading the output can
        # see exactly how far the batch was moved and reproduce the ages.
        print(f"Rebased created_at forward by {offset} (in memory; data/failed_payments.json untouched).")

    client = get_client()
    insert_batch(client, records)

    for p in batch:
        log_event(client, p["payment_id"], "ingested",
                   {"source": "synthetic_batch", "error_code": p["error_code"]})

    print(f"Loaded {len(records)} payments into Supabase and logged 'ingested' event for each.")


if __name__ == "__main__":
    main()
