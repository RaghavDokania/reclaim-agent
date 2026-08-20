"""
Replays the committed batch of failed payments under three competing
recovery policies and reports what each one would have recovered.

The agent's recovery number only means something next to a counterfactual:
doing nothing is the floor, retrying everything blindly is what a team
would build in an afternoon, and the agent's policy has to beat both to
justify existing. Everything here runs offline against
data/failed_payments.json using the same simulated completion rates the
act layer uses, with a fixed seed so the numbers reproduce exactly.

Run:
    python compare_policies.py
"""

import json
import os
import random
import statistics
import sys
from datetime import datetime, timedelta, timezone

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "decide"))
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "act"))
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "diagnose"))

from policy import MAX_ATTEMPTS, decide  # noqa: E402
from simulate_outcome import ASSUMED_SUCCESS_RATES  # noqa: E402
from classifier import classify  # noqa: E402

BATCH_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "failed_payments.json")

# The naive policy retries everything, immediately, regardless of cause --
# the strategy a team ships before thinking about root causes.
NAIVE_ACTION = "retry_payment"

# Fixed seed list for the multi-seed summary in main(): a single seed's
# lift is a point estimate that invites "why that seed?" -- this reports
# the median and range across many, so the headline number is defensible.
COMPARISON_SEEDS = list(range(1, 21))


def _parse_created_at(raw: str) -> datetime:
    parsed = datetime.fromisoformat(raw)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _run_do_nothing(payments, seed):
    return {"recovered_count": 0, "recovered_inr": 0.0, "attempts": 0}


def _run_naive_retry_all(payments, seed):
    recovered_count = 0
    recovered_inr = 0.0
    attempts = 0

    for payment in payments:
        # Keyed to (seed, payment_id) rather than drawn from one stream
        # shared across the whole batch -- otherwise how many draws one
        # payment consumes shifts the stream offset for every later
        # payment (and, since the two policies' per-payment draw counts
        # diverge whenever their success thresholds differ, for the other
        # policy's comparison to this one too). Keying per payment means
        # what happens to payment N can never shift payment N+1's draws.
        rng = random.Random(f"{seed}:{payment['payment_id']}")
        # Held to the same lifetime attempt budget the agent is held to by
        # decide()'s attempt_count >= MAX_ATTEMPTS check -- otherwise naive
        # gets a larger budget just for ignoring prior attempts, and the
        # comparison measures budget instead of strategy.
        remaining_budget = max(0, MAX_ATTEMPTS - payment["attempt_count"])
        for _ in range(remaining_budget):
            attempts += 1
            if rng.random() < ASSUMED_SUCCESS_RATES[NAIVE_ACTION]:
                recovered_count += 1
                recovered_inr += payment["amount_inr"]
                break

    return {
        "recovered_count": recovered_count,
        "recovered_inr": round(recovered_inr, 2),
        "attempts": attempts,
    }


def _run_agent_policy(payments, seed):
    recovered_count = 0
    recovered_inr = 0.0
    attempts = 0

    for payment in payments:
        # See _run_naive_retry_all for why this is keyed per payment
        # rather than drawn from one stream shared across the batch.
        rng = random.Random(f"{seed}:{payment['payment_id']}")
        created_at = _parse_created_at(payment["created_at"])
        attempt_count = payment["attempt_count"]
        # Route on the classifier's predicted cause, the same signal the
        # live pipeline acts on -- not payment["root_cause"], which is a
        # synthetic ground-truth label the agent would never see in
        # production. Using the label would credit the agent with
        # diagnoses it sometimes gets wrong, overstating it.
        predicted_root_cause = classify(payment["error_reason"], payment["error_code"]).root_cause
        # Evaluate each payment from the moment it failed, then let the
        # policy's own delays advance the clock -- this is what the live
        # pipeline does across repeated runs, compressed into one pass.
        now = created_at

        while True:
            decision = decide(
                root_cause=predicted_root_cause,
                created_at=created_at,
                attempt_count=attempt_count,
                now=now,
            )
            if decision.status == "exhausted":
                break

            attempts += 1
            if rng.random() < ASSUMED_SUCCESS_RATES[decision.action]:
                recovered_count += 1
                recovered_inr += payment["amount_inr"]
                break

            attempt_count += 1
            now = decision.next_action_at + timedelta(minutes=1)

    return {
        "recovered_count": recovered_count,
        "recovered_inr": round(recovered_inr, 2),
        "attempts": attempts,
    }


POLICIES = {
    "do_nothing": _run_do_nothing,
    "naive_retry_all": _run_naive_retry_all,
    "agent_policy": _run_agent_policy,
}


def run_comparison(payments, seed: int = 42) -> dict:
    results = {}
    for name, runner in POLICIES.items():
        # Each runner keys its own per-payment RNGs off this seed (see
        # _run_naive_retry_all) so every policy faces the same luck for a
        # given payment -- otherwise the comparison measures the seed,
        # not the policy.
        results[name] = runner(payments, seed)
    return results


def multi_seed_summary(payments, seeds=COMPARISON_SEEDS) -> dict:
    """Reports the agent-vs-naive lift across many seeds instead of one.

    A single seed's lift is a point estimate -- quoting it alone invites
    "why that seed?". This runs the full comparison once per seed and
    summarizes the distribution of lifts, so the headline number is
    "median lift across N seeds, ranging X to Y" rather than one draw.
    """
    lift_pcts = []
    for seed in seeds:
        results = run_comparison(payments, seed=seed)
        naive = results["naive_retry_all"]["recovered_inr"]
        agent = results["agent_policy"]["recovered_inr"]
        if naive:
            lift_pcts.append((agent - naive) / naive * 100)

    return {
        "seeds": list(seeds),
        "lift_pcts": lift_pcts,
        "median_lift_pct": round(statistics.median(lift_pcts), 1),
        "min_lift_pct": round(min(lift_pcts), 1),
        "max_lift_pct": round(max(lift_pcts), 1),
    }


def main():
    with open(BATCH_PATH) as f:
        payments = json.load(f)

    results = run_comparison(payments)
    total_at_risk = sum(p["amount_inr"] for p in payments)

    print(f"Batch: {len(payments)} failed payments, Rs {total_at_risk:,.2f} at risk\n")
    print(f"{'policy':<20} {'recovered':>10} {'Rs recovered':>16} {'attempts':>10}")
    print("-" * 60)
    for name, r in results.items():
        print(f"{name:<20} {r['recovered_count']:>10} {r['recovered_inr']:>16,.2f} {r['attempts']:>10}")

    naive = results["naive_retry_all"]["recovered_inr"]
    agent = results["agent_policy"]["recovered_inr"]
    if naive:
        print(f"\nAgent policy vs naive retry-all (seed=42): {(agent - naive) / naive * 100:+.1f}% recovered")

    summary = multi_seed_summary(payments, COMPARISON_SEEDS)
    print(
        f"\nAcross {len(COMPARISON_SEEDS)} seeds ({COMPARISON_SEEDS[0]}-{COMPARISON_SEEDS[-1]}): "
        f"median lift {summary['median_lift_pct']:+.1f}%, "
        f"range {summary['min_lift_pct']:+.1f}% to {summary['max_lift_pct']:+.1f}%"
    )


if __name__ == "__main__":
    main()
