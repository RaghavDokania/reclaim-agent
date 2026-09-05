import hashlib
import hmac
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import pytest

from server import create_app
from test_webhook import PAYLOAD, FakeClient

SECRET = "whsec_test_abc123"


def _sign(body):
    return hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def ctx():
    client = FakeClient()
    app = create_app(client, SECRET)
    return client, app.test_client()


def _post(http, body, signature=None):
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-Razorpay-Signature"] = signature
    return http.post("/webhook/razorpay", data=body, headers=headers)


def test_a_signed_failure_event_is_accepted_and_stored(ctx):
    client, http = ctx
    body = json.dumps(PAYLOAD).encode()

    response = _post(http, body, _sign(body))

    assert response.status_code == 200
    assert response.get_json()["status"] == "ingested"
    assert len(client.payments) == 1


def test_an_unsigned_request_is_refused_and_stores_nothing(ctx):
    client, http = ctx
    body = json.dumps(PAYLOAD).encode()

    response = _post(http, body)

    assert response.status_code == 401
    assert client.payments == []


def test_a_forged_signature_is_refused_and_stores_nothing(ctx):
    client, http = ctx
    body = json.dumps(PAYLOAD).encode()

    response = _post(http, body, "0" * 64)

    assert response.status_code == 401
    assert client.payments == []


def test_the_signature_is_checked_against_the_exact_bytes_received(ctx):
    # Signed over a body with whitespace that json.dumps would not
    # reproduce. Verifying a re-serialized body instead of the raw bytes
    # would reject this valid request.
    client, http = ctx
    body = json.dumps(PAYLOAD, indent=2).encode()

    response = _post(http, body, _sign(body))

    assert response.status_code == 200
    assert len(client.payments) == 1


def test_a_redelivered_event_is_acknowledged_without_storing_again(ctx):
    client, http = ctx
    body = json.dumps(PAYLOAD).encode()

    _post(http, body, _sign(body))
    response = _post(http, body, _sign(body))

    assert response.status_code == 200
    assert response.get_json()["status"] == "duplicate"
    assert len(client.payments) == 1


def test_an_unrelated_event_is_acknowledged_so_razorpay_stops_retrying(ctx):
    client, http = ctx
    body = json.dumps({"event": "payment.captured", "payload": {}}).encode()

    response = _post(http, body, _sign(body))

    assert response.status_code == 200
    assert response.get_json()["status"] == "ignored"
    assert client.payments == []


def test_a_malformed_body_is_rejected_as_a_client_error(ctx):
    client, http = ctx
    body = b"this is not json"

    response = _post(http, body, _sign(body))

    assert response.status_code == 400
    assert client.payments == []


def test_a_signed_but_structurally_invalid_event_is_rejected(ctx):
    client, http = ctx
    body = json.dumps({"event": "payment.failed", "payload": {}}).encode()

    response = _post(http, body, _sign(body))

    assert response.status_code == 400
    assert client.payments == []


def test_the_health_endpoint_reports_ready_without_a_signature(ctx):
    _, http = ctx

    response = http.get("/health")

    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"
