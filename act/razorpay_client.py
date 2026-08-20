"""
Thin wrapper around the Razorpay test-mode SDK. Every recovery action
that reaches Razorpay goes through here so the real API artifacts
(order ids, payment link ids/urls) stay consistent no matter which
action created them.

Needs a .env file with:
    RAZORPAY_KEY_ID=rzp_test_xxxx
    RAZORPAY_KEY_SECRET=xxxx
"""

import os

import razorpay
from dotenv import load_dotenv

load_dotenv()


def get_client() -> razorpay.Client:
    key_id = os.environ["RAZORPAY_KEY_ID"]
    key_secret = os.environ["RAZORPAY_KEY_SECRET"]
    return razorpay.Client(auth=(key_id, key_secret))


def create_retry_order(client: razorpay.Client, payment_id: str, amount_inr: float) -> dict:
    """A retry_payment action: a fresh order representing the recovery
    attempt on the original failed payment."""
    order = client.order.create({
        "amount": int(round(amount_inr * 100)),  # paise
        "currency": "INR",
        "notes": {"reclaim_agent_action": "retry_payment", "original_payment_id": payment_id},
    })
    return {"artifact_type": "order", "artifact_id": order["id"]}


def create_recovery_payment_link(client: razorpay.Client, payment_id: str, amount_inr: float, reason: str) -> dict:
    """A send_payment_link or prompt_card_update action: a real payment
    link the customer would be sent to complete or fix their payment."""
    link = client.payment_link.create({
        "amount": int(round(amount_inr * 100)),  # paise
        "currency": "INR",
        "description": f"Recovery link for payment {payment_id}: {reason}",
        "notes": {"reclaim_agent_action": "payment_link", "original_payment_id": payment_id},
    })
    return {"artifact_type": "payment_link", "artifact_id": link["id"], "short_url": link["short_url"]}
