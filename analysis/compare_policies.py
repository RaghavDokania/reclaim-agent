"""
Replays the committed batch of failed payments under four competing
recovery policies and reports what each one would have recovered.

The agent's recovery number only means something next to a counterfactual:
doing nothing is the floor, retrying everything blindly is what a team
would build in an afternoon, and the agent has to be measured against
both. The four policies are:

    do_nothing              recover nothing -- the floor.
    naive_retry_all         retry every payment up to its remaining
                            lifetime attempt budget, regardless of cause.
    agent_routing_ungated   the agent's cause-aware routing and timing
                            with decide()'s confidence and value gates
                            TURNED OFF. This is NOT what the agent ships.
                            It exists to isolate one question -- does
                            routing on a diagnosed cause beat retrying
                            blindly? -- by holding the compliance controls
                            constant across both arms. Any lift it reports
                            is a lift for the routing strategy alone and
                            must always be quoted as such.
    agent_gated             what the live pipeline actually runs:
                            decide() called with the diagnosis confidence
                            and the payment amount, so low-confidence and
                            high-value payments are held for a human
                            instead of auto-actioned. Its result is two
                            numbers -- recovered automatically and held
                            for human sign-off -- not a single lift
                            figure, because withholding a high-value
                            payment is the gate working, not a loss.

Everything here runs offline against data/failed_payments.json using the
same simulated completion rates the act layer uses. Randomness is keyed
per (seed, payment_id) so every policy faces identical luck on a given
payment and the numbers reproduce exactly across processes.

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

from policy import HIGH_VALUE_THRESHOLD_INR, MAX_ATTEMPTS, decide  # noqa: E402
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


# Statuses decide() can return that hand the payment to a human instead of
# acting on it. A held payment is neither recovered nor an attempt -- it is
# a third outcome, reported on its own.
HELD_STATUSES = {"needs_review", "needs_approval"}


def _run_do_nothing(payments, seed):
    return {
        "recovered_count": 0, "recovered_inr": 0.0, "attempts": 0,
        "held_count": 0, "held_inr": 0.0,
    }


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
        # Naive never withholds anything from a human -- that is the whole
        # point of it. Reported as zero so all four rows share columns.
        "held_count": 0,
        "held_inr": 0.0,
    }


def _run_agent(payments, seed, gated: bool):
    """Shared agent runner. `gated=False` reproduces the routing-only
    variant (decide() called without the gate kwargs); `gated=True` calls
    decide() exactly the way decide/run_decisions.py does in production."""
    recovered_count = 0
    recovered_inr = 0.0
    attempts = 0
    held_count = 0
    held_inr = 0.0

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
        #
        # Rules-only classify(), never classify_with_llm() -- this harness
        # must stay offline and deterministic, and excluding the LLM layer
        # makes it understate rather than overstate the agent.
        diagnosis = classify(payment["error_reason"], payment["error_code"])
        predicted_root_cause = diagnosis.root_cause
        # The gated variant passes the same two signals run_decisions.py
        # passes: the diagnosis's own confidence and the payment amount.
        gate_kwargs = (
            {"confidence": diagnosis.confidence, "amount_inr": payment["amount_inr"]}
            if gated else {}
        )
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
                **gate_kwargs,
            )
            if decision.status in HELD_STATUSES:
                held_count += 1
                held_inr += payment["amount_inr"]
                break
            # Every non-acting status is terminal for this payment --
            # "exhausted" today, and anything decide() grows later. Falling
            # through on one would index ASSUMED_SUCCESS_RATES[None].
            if decision.status != "action_taken":
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
        "held_count": held_count,
        "held_inr": round(held_inr, 2),
    }


def _run_agent_routing_ungated(payments, seed):
    return _run_agent(payments, seed, gated=False)


def _run_agent_gated(payments, seed):
    return _run_agent(payments, seed, gated=True)


POLICIES = {
    "do_nothing": _run_do_nothing,
    "naive_retry_all": _run_naive_retry_all,
    "agent_routing_ungated": _run_agent_routing_ungated,
    "agent_gated": _run_agent_gated,
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
    """Reports the routing-only-vs-naive lift across many seeds, not one.

    A single seed's lift is a point estimate -- quoting it alone invites
    "why that seed?". This runs the full comparison once per seed and
    summarizes the distribution of lifts, so the headline number is
    "median lift across N seeds, ranging X to Y" rather than one draw.

    The lift compares `agent_routing_ungated` against `naive_retry_all`:
    routing strategy with the compliance gates off on BOTH arms. It is not
    a lift figure for the shipped agent -- see the module docstring and
    `agent_gated`, whose result is reported as recovered-vs-held instead.
    """
    lift_pcts = []
    for seed in seeds:
        results = run_comparison(payments, seed=seed)
        naive = results["naive_retry_all"]["recovered_inr"]
        agent = results["agent_routing_ungated"]["recovered_inr"]
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

    print(f"Batch: {len(payments)} failed payments, Rs {total_at_risk:,.2f} at risk")
    print("Simulated completion rates from act/simulate_outcome.py; seed=42 unless stated.\n")

    header = f"{'policy':<22} {'recovered':>10} {'Rs recovered':>16} {'attempts':>9} {'held':>6} {'Rs held':>14}"
    print(header)
    print("-" * len(header))
    for name, r in results.items():
        print(
            f"{name:<22} {r['recovered_count']:>10} {r['recovered_inr']:>16,.2f} "
            f"{r['attempts']:>9} {r['held_count']:>6} {r['held_inr']:>14,.2f}"
        )

    print("\nagent_routing_ungated is NOT the shipped agent: it is the agent's")
    print("cause-aware routing with the confidence and value gates switched off,")
    print("so routing can be compared against naive_retry_all with the compliance")
    print("controls held constant on both arms. agent_gated is what ships.")

    naive = results["naive_retry_all"]["recovered_inr"]
    ungated = results["agent_routing_ungated"]["recovered_inr"]
    if naive:
        print(
            f"\nRouting-only lift (gates OFF on both arms), seed=42: "
            f"{(ungated - naive) / naive * 100:+.1f}% recovered vs naive retry-all"
        )

    summary = multi_seed_summary(payments, COMPARISON_SEEDS)
    print(
        f"Routing-only lift (gates OFF on both arms) across {len(COMPARISON_SEEDS)} seeds "
        f"({COMPARISON_SEEDS[0]}-{COMPARISON_SEEDS[-1]}): "
        f"median {summary['median_lift_pct']:+.1f}%, "
        f"range {summary['min_lift_pct']:+.1f}% to {summary['max_lift_pct']:+.1f}%"
    )

    gated = results["agent_gated"]
    print("\nagent_gated -- what the live pipeline does with this batch (seed=42).")
    print("Reported as two separate figures, not one lift number: money the agent")
    print("recovered on its own authority, and money it deliberately withheld for a")
    print("human. Held money is not lost, and it is not counted as recovered.")
    print(
        f"  recovered automatically: {gated['recovered_count']:>3} payments, "
        f"Rs {gated['recovered_inr']:,.2f}  ({gated['attempts']} attempts)"
    )
    print(
        f"  held for human sign-off: {gated['held_count']:>3} payments, "
        f"Rs {gated['held_inr']:,.2f}  (low confidence or at/above the "
        f"Rs {HIGH_VALUE_THRESHOLD_INR:,.0f} value gate)"
    )


if __name__ == "__main__":
    main()
