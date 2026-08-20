"""
Generates a synthetic batch of failed payments to build and demo the
recovery agent against, without needing to wait on real Razorpay
test-mode traffic. Skewed toward realistic real-world proportions
(insufficient funds and auth failures are the most common causes of
payment failure in practice).

Run:
    python generate_synthetic_batch.py --count 75
"""

import argparse
import json
import random
import uuid
from datetime import datetime, timedelta

from schema import FailedPayment, RootCause

# (root_cause, error_code, relative weight) -- error_reason is drawn at
# random from ERROR_REASON_POOL below, since real gateways don't emit one
# canonical string per failure type. A couple of the pooled phrasings are
# deliberately ambiguous (e.g. "Transaction declined by bank" could read as
# either an issuer decline or an auth failure) so the diagnosis layer's
# accuracy isn't a trivial 100% -- see diagnose/classifier.py.
FAILURE_PROFILES = [
    (RootCause.INSUFFICIENT_FUNDS, "BAD_REQUEST_ERROR", 30),
    (RootCause.AUTH_FAILURE, "GATEWAY_ERROR", 25),
    (RootCause.CARD_DECLINED_BY_ISSUER, "GATEWAY_ERROR", 20),
    (RootCause.NETWORK_TIMEOUT, "SERVER_ERROR", 15),
    (RootCause.EXPIRED_CARD, "BAD_REQUEST_ERROR", 10),
]

ERROR_REASON_POOL = {
    RootCause.INSUFFICIENT_FUNDS: [
        "Insufficient balance in customer's account",
        "Account does not have sufficient funds to complete this transaction",
        "Payment declined due to low balance",
        "Available balance is less than the transaction amount",
        "Your bank has declined this transaction due to insufficient funds",
    ],
    RootCause.AUTH_FAILURE: [
        "Customer did not complete two-factor authentication in time",
        "Incorrect OTP entered, transaction not authorized",
        "3D Secure verification failed",
        "OTP/3DS authentication failed or timed out",
        "Transaction declined by bank",  # ambiguous, shared with issuer decline
    ],
    RootCause.CARD_DECLINED_BY_ISSUER: [
        "Card declined by issuing bank",
        "Issuer declined the transaction",
        "Your card issuer has declined this payment",
        "Bank declined the transaction, please contact your bank",
        "Transaction declined by bank",  # ambiguous, shared with auth failure
    ],
    RootCause.NETWORK_TIMEOUT: [
        "Gateway timeout while contacting bank",
        "Request timed out, please try again",
        "Unable to reach payment gateway",
        "Connection to the bank server was interrupted",
        "Bank server did not respond in time",
    ],
    RootCause.EXPIRED_CARD: [
        "Card has expired",
        "The card used for this payment has expired",
        "Card expiry date has passed",
        "Please use a valid, non-expired card",
    ],
}

FIRST_NAMES = ["Aarav", "Vivaan", "Aditi", "Isha", "Kabir", "Meera", "Rohan",
               "Sanya", "Aryan", "Diya", "Karan", "Neha", "Yash", "Priya"]
LAST_NAMES = ["Sharma", "Verma", "Iyer", "Nair", "Gupta", "Reddy", "Rao",
              "Khan", "Joshi", "Patel"]


def weighted_choice(profiles):
    total = sum(p[2] for p in profiles)
    r = random.uniform(0, total)
    upto = 0
    for profile in profiles:
        upto += profile[2]
        if r <= upto:
            return profile
    return profiles[-1]


def make_payment(now: datetime) -> FailedPayment:
    root_cause, error_code, _ = weighted_choice(FAILURE_PROFILES)
    error_reason = random.choice(ERROR_REASON_POOL[root_cause])
    first, last = random.choice(FIRST_NAMES), random.choice(LAST_NAMES)
    created_at = now - timedelta(
        days=random.randint(0, 6),
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
    )
    return FailedPayment(
        payment_id=f"pay_{uuid.uuid4().hex[:14]}",
        order_id=f"order_{uuid.uuid4().hex[:14]}",
        amount_inr=round(random.uniform(199, 24999), 2),
        currency="INR",
        error_code=error_code,
        error_reason=error_reason,
        root_cause=root_cause,
        created_at=created_at.isoformat(),
        customer_email=f"{first.lower()}.{last.lower()}@example.com",
        customer_phone=f"+91{random.randint(7000000000, 9999999999)}",
        attempt_count=1,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=75,
                         help="Number of failed payments to generate")
    parser.add_argument("--out", type=str, default="../data/failed_payments.json")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)
    now = datetime.now()

    batch = [make_payment(now) for _ in range(args.count)]

    with open(args.out, "w") as f:
        json.dump([p.to_dict() for p in batch], f, indent=2)

    # Quick summary so you can eyeball the distribution
    counts = {}
    total_at_risk = 0.0
    for p in batch:
        counts[p.root_cause.value] = counts.get(p.root_cause.value, 0) + 1
        total_at_risk += p.amount_inr

    print(f"Generated {len(batch)} failed payments -> {args.out}")
    print(f"Total amount at risk: Rs {total_at_risk:,.2f}\n")
    print("Root cause breakdown:")
    for cause, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {cause:28s} {n:3d}  ({n/len(batch)*100:.0f}%)")


if __name__ == "__main__":
    main()
