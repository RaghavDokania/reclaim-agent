import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import pytest

from webhook import ingest_event, normalize_event, verify_signature

# Shape Razorpay actually posts for payment.failed. Amount is in paise and
# created_at is a unix epoch -- both have to be converted, and getting
# either wrong silently corrupts every downstream money decision.
PAYLOAD = {
    "event": "payment.failed",
    "payload": {
        "payment": {
            "entity": {
                "id": "pay_NcvZ9k2mF1sQxA",
                "order_id": "order_NcvZ8jK1lPqRtB",
                "amount": 1419592,
                "currency": "INR",
                "error_code": "BAD_REQUEST_ERROR",
                "error_description": "Payment failed due to insufficient balance",
                "email": "customer@example.com",
                "contact": "+919876543210",
                "created_at": 1757030400,
            }
        }
    },
}


def test_paise_are_converted_to_rupees():
    row = normalize_event(PAYLOAD)

    assert row["amount_inr"] == 14195.92


def test_the_unix_timestamp_becomes_an_iso_string():
    row = normalize_event(PAYLOAD)

    assert row["created_at"] == "2025-09-05T00:00:00+00:00"


def test_identifiers_and_contact_details_are_carried_across():
    row = normalize_event(PAYLOAD)

    assert row["payment_id"] == "pay_NcvZ9k2mF1sQxA"
    assert row["order_id"] == "order_NcvZ8jK1lPqRtB"
    assert row["customer_email"] == "customer@example.com"
    assert row["customer_phone"] == "+919876543210"


def test_the_error_description_becomes_the_reason_the_classifier_reads():
    row = normalize_event(PAYLOAD)

    assert row["error_reason"] == "Payment failed due to insufficient balance"
    assert row["error_code"] == "BAD_REQUEST_ERROR"


def test_a_real_event_enters_the_pipeline_undiagnosed():
    row = normalize_event(PAYLOAD)

    assert row["status"] == "needs_diagnosis"
    assert row["attempt_count"] == 1


def test_a_real_event_carries_no_ground_truth_root_cause():
    # root_cause is a synthetic-data label. Inventing one for a real
    # payment would corrupt the accuracy figure with a fabricated answer.
    row = normalize_event(PAYLOAD)

    assert "root_cause" not in row


def test_a_missing_error_description_still_produces_a_usable_row():
    payload = {
        "event": "payment.failed",
        "payload": {"payment": {"entity": {
            "id": "pay_x", "order_id": "order_x", "amount": 100,
            "currency": "INR", "error_code": "GATEWAY_ERROR",
            "created_at": 1757030400,
        }}},
    }

    row = normalize_event(payload)

    assert row["error_reason"] == ""
    assert row["error_code"] == "GATEWAY_ERROR"


def test_an_event_that_is_not_a_payment_failure_is_rejected():
    payload = {"event": "payment.captured", "payload": {"payment": {"entity": {"id": "pay_x"}}}}

    with pytest.raises(ValueError, match="payment.failed"):
        normalize_event(payload)


def test_a_payload_missing_the_payment_entity_is_rejected():
    with pytest.raises(ValueError):
        normalize_event({"event": "payment.failed", "payload": {}})


SECRET = "whsec_test_abc123"
BODY = b'{"event":"payment.failed","payload":{}}'


def _sign(body, secret):
    import hashlib
    import hmac as _hmac

    return _hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_a_correctly_signed_body_is_accepted():
    assert verify_signature(BODY, _sign(BODY, SECRET), SECRET) is True


def test_a_body_altered_after_signing_is_rejected():
    signature = _sign(BODY, SECRET)
    tampered = BODY.replace(b"payment.failed", b"payment.captured")

    assert verify_signature(tampered, signature, SECRET) is False


def test_a_signature_from_a_different_secret_is_rejected():
    assert verify_signature(BODY, _sign(BODY, "whsec_attacker"), SECRET) is False


def test_an_absent_signature_is_rejected_rather_than_treated_as_optional():
    assert verify_signature(BODY, None, SECRET) is False
    assert verify_signature(BODY, "", SECRET) is False


def test_a_malformed_signature_is_rejected_without_raising():
    assert verify_signature(BODY, "not-hex-at-all", SECRET) is False


def test_verification_cannot_be_disabled_by_an_empty_secret():
    # An unset RAZORPAY_WEBHOOK_SECRET must fail closed, not accept everything.
    with pytest.raises(ValueError, match="secret"):
        verify_signature(BODY, _sign(BODY, ""), "")


class FakeTable:
    """In-memory stand-in for one Supabase table. Tests assert on the rows
    it ends up holding, not on which methods were called."""

    def __init__(self, store):
        self.store = store
        self._filter = None

    def select(self, *_):
        return self

    def insert(self, record):
        self.store.append(record)
        self._pending = record
        return self

    def eq(self, column, value):
        self._filter = (column, value)
        return self

    def execute(self):
        if self._filter:
            column, value = self._filter
            return type("R", (), {"data": [r for r in self.store if r.get(column) == value]})()
        return type("R", (), {"data": list(self.store)})()


class FakeClient:
    def __init__(self):
        self.payments = []
        self.audit = []

    def table(self, name):
        return FakeTable(self.payments if name == "failed_payments" else self.audit)


def test_a_new_event_is_stored_and_logged():
    client = FakeClient()

    result = ingest_event(PAYLOAD, client)

    assert result == "ingested"
    assert [r["payment_id"] for r in client.payments] == ["pay_NcvZ9k2mF1sQxA"]
    assert client.audit[0]["event"] == "ingested"


def test_a_redelivered_event_is_not_stored_twice():
    client = FakeClient()

    ingest_event(PAYLOAD, client)
    result = ingest_event(PAYLOAD, client)

    assert result == "duplicate"
    assert len(client.payments) == 1


def test_a_redelivered_event_does_not_append_a_second_audit_entry():
    client = FakeClient()

    ingest_event(PAYLOAD, client)
    ingest_event(PAYLOAD, client)

    assert len(client.audit) == 1


def test_the_audit_entry_records_that_the_row_arrived_by_webhook():
    client = FakeClient()

    ingest_event(PAYLOAD, client)

    assert client.audit[0]["detail"]["source"] == "webhook"
