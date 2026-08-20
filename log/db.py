"""
Thin wrapper around the Supabase client for the reclaim-agent pipeline.
Every other module (diagnose, decide, act) should go through these
functions rather than talking to Supabase directly -- keeps the audit
trail consistent no matter which layer is writing to it.

Needs a .env file (same folder or project root) with:
    SUPABASE_URL=https://xxxx.supabase.co
    SUPABASE_KEY=your-anon-or-service-key
"""

import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()


def get_client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]
    return create_client(url, key)


def insert_batch(client: Client, records: list[dict]):
    """Insert a batch of failed-payment records. Expects each dict to
    already match the failed_payments table columns (no audit_log key)."""
    result = client.table("failed_payments").insert(records).execute()
    return result.data


def log_event(client: Client, payment_id: str, event: str, detail: dict | None = None):
    """Append one row to the audit trail for a given payment."""
    return client.table("audit_log").insert({
        "payment_id": payment_id,
        "event": event,
        "detail": detail or {},
    }).execute()


def update_payment(client: Client, payment_id: str, **fields):
    """Update any subset of columns on a failed_payments row (status,
    predicted_root_cause, action_taken, recovered_amount_inr, ...)."""
    return client.table("failed_payments").update(fields).eq("payment_id", payment_id).execute()


def get_metrics(client: Client) -> dict:
    """The number you lead your pitch with: batch size, recovery rate,
    and rupees recovered, computed straight from the table."""
    rows = client.table("failed_payments").select("*").execute().data

    total = len(rows)
    recovered = [r for r in rows if r["status"] == "recovered"]
    exhausted = [r for r in rows if r["status"] == "exhausted"]

    total_at_risk = sum(r["amount_inr"] for r in rows)
    total_recovered = sum(r["recovered_amount_inr"] or 0 for r in recovered)

    return {
        "total_batch_size": total,
        "recovered_count": len(recovered),
        "exhausted_count": len(exhausted),
        "still_in_progress": total - len(recovered) - len(exhausted),
        "recovery_rate_pct": round(len(recovered) / total * 100, 1) if total else 0,
        "total_amount_at_risk_inr": round(total_at_risk, 2),
        "total_amount_recovered_inr": round(total_recovered, 2),
    }
