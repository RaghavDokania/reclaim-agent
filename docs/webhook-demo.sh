#!/usr/bin/env bash
# Drives the webhook endpoint through its three interesting cases so a
# genuine 200, an idempotent redelivery, and a rejected forgery land back
# to back on camera.
#
#   RAZORPAY_WEBHOOK_SECRET=whsec_demo python ingest/server.py
#   bash docs/webhook-demo.sh
#
# Override WEBHOOK_SECRET / WEBHOOK_URL if you started the server
# differently.

set -uo pipefail

URL="${WEBHOOK_URL:-http://127.0.0.1:5000/webhook/razorpay}"
SECRET="${WEBHOOK_SECRET:-whsec_demo}"
FORGED="0000000000000000000000000000000000000000000000000000000000000000"

BODY='{"event":"payment.failed","payload":{"payment":{"entity":{"id":"pay_DEMO0000000001","order_id":"order_DEMO000001","amount":1419592,"currency":"INR","error_code":"BAD_REQUEST_ERROR","error_description":"Payment failed due to insufficient balance","email":"demo@example.com","contact":"+919876543210","created_at":1757030400}}}}'

SIGNATURE=$(BODY="$BODY" SECRET="$SECRET" python -c '
import hashlib, hmac, os
print(hmac.new(os.environ["SECRET"].encode(), os.environ["BODY"].encode(), hashlib.sha256).hexdigest())
')

send() {
  curl -s -w "\n  -> HTTP %{http_code}\n" \
    -X POST "$URL" \
    -H "Content-Type: application/json" \
    -H "X-Razorpay-Signature: $1" \
    --data-raw "$BODY"
}

echo "== 1. correctly signed payment.failed =="
send "$SIGNATURE"

echo
echo "== 2. same event redelivered (Razorpay retries until it gets a 2xx) =="
send "$SIGNATURE"

echo
echo "== 3. forged signature =="
send "$FORGED"
