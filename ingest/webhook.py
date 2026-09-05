"""
Turns a live Razorpay payment.failed webhook into a pipeline row.

The batch loader reads a committed JSON snapshot; this is the same shape
arriving one event at a time from Razorpay instead. Two conversions carry
real risk and are covered by tests: Razorpay sends amounts in paise, and
timestamps as unix epochs.

A real event has no root_cause. That column is the synthetic batch's
ground-truth label, and fabricating one here would poison the accuracy
figure with an answer nobody actually knows.
"""

import hashlib
import hmac
from datetime import datetime, timezone

FAILURE_EVENT = "payment.failed"
PAISE_PER_RUPEE = 100


def normalize_event(payload):
    event = payload.get("event")
    if event != FAILURE_EVENT:
        raise ValueError(f"expected {FAILURE_EVENT}, got {event!r}")

    entity = (payload.get("payload") or {}).get("payment", {}).get("entity")
    if not entity:
        raise ValueError("payload.payment.entity missing")

    created = datetime.fromtimestamp(entity["created_at"], tz=timezone.utc)

    return {
        "payment_id": entity["id"],
        "order_id": entity.get("order_id"),
        "amount_inr": entity["amount"] / PAISE_PER_RUPEE,
        "currency": entity.get("currency", "INR"),
        "error_code": entity.get("error_code", ""),
        "error_reason": entity.get("error_description") or "",
        "created_at": created.isoformat(),
        "customer_email": entity.get("email"),
        "customer_phone": entity.get("contact"),
        "attempt_count": 1,
        "status": "needs_diagnosis",
    }


def verify_signature(body, signature, secret):
    """True only if `body` was signed with `secret`.

    An unset secret raises rather than returning False: a deployment that
    forgot to configure one must refuse to start, not silently accept
    every unsigned request that arrives.
    """
    if not secret:
        raise ValueError("webhook secret is not configured")
    if not signature:
        return False

    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    # compare_digest, not ==, so a forger cannot recover the signature
    # byte by byte from how long the comparison takes.
    return hmac.compare_digest(expected, signature)


def ingest_event(payload, client):
    """Store one webhook event, exactly once.

    Razorpay redelivers an event until it gets a 2xx, so the same
    payment_id arrives repeatedly in normal operation. A duplicate is a
    no-op rather than an error: re-inserting would reset attempt_count
    and hand a payment a fresh budget of retries it already spent.
    """
    row = normalize_event(payload)

    existing = (
        client.table("failed_payments")
        .select("payment_id")
        .eq("payment_id", row["payment_id"])
        .execute()
        .data
    )
    if existing:
        return "duplicate"

    client.table("failed_payments").insert(row).execute()
    client.table("audit_log").insert({
        "payment_id": row["payment_id"],
        "event": "ingested",
        "detail": {"source": "webhook", "error_code": row["error_code"]},
    }).execute()
    return "ingested"
