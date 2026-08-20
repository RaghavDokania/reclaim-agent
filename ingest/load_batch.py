"""
Loads data/failed_payments.json into Supabase and logs the initial
'ingested' event for every record. Run this once after you've generated
your batch and set up your Supabase table.

Run:
    python load_batch.py
"""

import json
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "log"))
from db import get_client, insert_batch, log_event  # noqa: E402


def main():
    with open("../data/failed_payments.json") as f:
        batch = json.load(f)

    # Strip fields the failed_payments table doesn't have (audit_log lives
    # in its own table now, not as a column here)
    records = []
    for p in batch:
        record = {k: v for k, v in p.items() if k != "audit_log"}
        records.append(record)

    client = get_client()
    insert_batch(client, records)

    for p in batch:
        log_event(client, p["payment_id"], "ingested",
                   {"source": "synthetic_batch", "error_code": p["error_code"]})

    print(f"Loaded {len(records)} payments into Supabase and logged 'ingested' event for each.")


if __name__ == "__main__":
    main()
