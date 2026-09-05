"""
HTTP endpoint for Razorpay's payment.failed webhook.

Receives one event, authenticates it, and stores it. Diagnosis and
recovery are left to the existing pipeline stages: the handler's only job
is to get a genuine event into the table quickly and exactly once, then
return, because Razorpay times out a slow endpoint and redelivers.

Run locally:
    python ingest/server.py
    ngrok http 5000          # Razorpay needs a public URL to post to

Then add the https URL from ngrok as a webhook in the Razorpay dashboard
(Settings -> Webhooks), subscribe it to payment.failed, and put the
secret Razorpay gives you in RAZORPAY_WEBHOOK_SECRET.
"""

import os
import sys

from flask import Flask, jsonify, request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from webhook import FAILURE_EVENT, ingest_event, verify_signature  # noqa: E402

SIGNATURE_HEADER = "X-Razorpay-Signature"


def create_app(client, secret):
    app = Flask(__name__)

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    @app.post("/webhook/razorpay")
    def receive():
        # The signature covers the bytes Razorpay sent. Parsing first and
        # re-serializing would change them and reject valid requests, so
        # authenticate the raw body before trusting anything in it.
        body = request.get_data()
        if not verify_signature(body, request.headers.get(SIGNATURE_HEADER), secret):
            return jsonify(status="rejected", reason="bad signature"), 401

        try:
            payload = request.get_json(force=True, silent=False)
        except Exception:
            return jsonify(status="rejected", reason="malformed json"), 400

        if not isinstance(payload, dict):
            return jsonify(status="rejected", reason="malformed json"), 400

        # Acknowledge events we do not act on, otherwise Razorpay keeps
        # redelivering them until it gets a 2xx.
        if payload.get("event") != FAILURE_EVENT:
            return jsonify(status="ignored", event=payload.get("event")), 200

        try:
            result = ingest_event(payload, client)
        except ValueError as exc:
            return jsonify(status="rejected", reason=str(exc)), 400

        return jsonify(status=result), 200

    return app


def main():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    from log.db import get_client

    secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET")
    if not secret:
        raise SystemExit(
            "RAZORPAY_WEBHOOK_SECRET is not set. Refusing to start: without it "
            "every unsigned request would be accepted."
        )

    app = create_app(get_client(), secret)
    app.run(port=int(os.environ.get("PORT", 5000)))


if __name__ == "__main__":
    main()
