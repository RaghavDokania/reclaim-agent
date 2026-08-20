"""
Simulates whether a customer completes a recovery action.

This is the one deliberately-simulated piece of the pipeline: no backend
agent can force a card charge or a customer to click a payment link --
that requires the customer in the loop, which no PCI-compliant gateway
lets you automate server-side. The rates below are documented, stated
assumptions drawn from typical dunning-recovery benchmarks, not derived
from anything in this codebase. Every result is flagged simulated=True
so nothing here pretends to be a real outcome.
"""

import random
from dataclasses import dataclass
from typing import Callable

# action -> assumed probability the customer completes it
ASSUMED_SUCCESS_RATES = {
    "retry_payment": 0.35,       # typical automated dunning-retry recovery rate
    "send_payment_link": 0.50,   # higher: customer already intends to pay
    "prompt_card_update": 0.45,
}


@dataclass
class OutcomeResult:
    success: bool
    assumed_rate: float
    simulated: bool = True


def simulate_outcome(action: str, rand_func: Callable[[], float] = random.random) -> OutcomeResult:
    if action not in ASSUMED_SUCCESS_RATES:
        raise ValueError(f"no assumed success rate defined for action: {action!r}")

    rate = ASSUMED_SUCCESS_RATES[action]
    success = rand_func() < rate
    return OutcomeResult(success=success, assumed_rate=rate)
