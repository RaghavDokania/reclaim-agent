### Task 2: Policy comparison harness

Produces the pitch's headline number. Without a counterfactual, "we recovered ₹1.8L" means nothing; with one, it becomes "a N% lift over naive retry-everything." Runs entirely offline against the committed JSON batch — no Supabase, no Razorpay, no LLM — so a judge can reproduce it with `python analysis/compare_policies.py` and nothing else.

**Files:**
- Create: `analysis/compare_policies.py`
- Create: `analysis/test_compare_policies.py`

**Interfaces:**
- Consumes: `decide(root_cause, created_at, attempt_count, now) -> DecisionResult` from `decide/policy.py` (fields: `action`, `status`, `next_action_at`, `reason`); `ASSUMED_SUCCESS_RATES` from `act/simulate_outcome.py`; the committed `data/failed_payments.json`.
- Produces: `run_comparison(payments, seed=42) -> dict` mapping policy name to `{"recovered_count": int, "recovered_inr": float, "attempts": int}`. Nothing downstream consumes this — it is a leaf analysis tool.

- [ ] **Step 1: Write the failing tests**

Create `analysis/test_compare_policies.py`:

```python
from compare_policies import run_comparison

BATCH = [
    {
        "payment_id": f"pay_{i}",
        "amount_inr": 1000.0,
        "root_cause": "network_timeout",
        "created_at": "2026-08-20T10:00:00+00:00",
        "attempt_count": 1,
    }
    for i in range(50)
]


def test_do_nothing_recovers_nothing():
    results = run_comparison(BATCH, seed=42)
    assert results["do_nothing"]["recovered_count"] == 0
    assert results["do_nothing"]["recovered_inr"] == 0.0


def test_every_policy_is_reported():
    results = run_comparison(BATCH, seed=42)
    assert set(results) == {"do_nothing", "naive_retry_all", "agent_policy"}


def test_results_are_deterministic_for_a_fixed_seed():
    assert run_comparison(BATCH, seed=42) == run_comparison(BATCH, seed=42)


def test_different_seeds_can_produce_different_results():
    a = run_comparison(BATCH, seed=1)
    b = run_comparison(BATCH, seed=999)
    assert a != b


def test_recovered_amount_never_exceeds_amount_at_risk():
    results = run_comparison(BATCH, seed=42)
    total_at_risk = sum(p["amount_inr"] for p in BATCH)
    for policy in results.values():
        assert policy["recovered_inr"] <= total_at_risk


def test_no_policy_exceeds_three_attempts_per_payment():
    results = run_comparison(BATCH, seed=42)
    for policy in results.values():
        assert policy["attempts"] <= len(BATCH) * 3
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd analysis && python -m pytest test_compare_policies.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'compare_policies'`.

- [ ] **Step 3: Write `analysis/compare_policies.py`**

Note the import path juggling — `analysis/` needs both `decide/` and `act/` on `sys.path`.

```python
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
import sys
from datetime import datetime, timedelta, timezone

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "decide"))
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "act"))

from policy import MAX_ATTEMPTS, decide  # noqa: E402
from simulate_outcome import ASSUMED_SUCCESS_RATES  # noqa: E402

BATCH_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "failed_payments.json")

# The naive policy retries everything, immediately, regardless of cause --
# the strategy a team ships before thinking about root causes.
NAIVE_ACTION = "retry_payment"


def _parse_created_at(raw: str) -> datetime:
    parsed = datetime.fromisoformat(raw)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _run_do_nothing(payments, rng):
    return {"recovered_count": 0, "recovered_inr": 0.0, "attempts": 0}


def _run_naive_retry_all(payments, rng):
    recovered_count = 0
    recovered_inr = 0.0
    attempts = 0

    for payment in payments:
        for _ in range(MAX_ATTEMPTS):
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


def _run_agent_policy(payments, rng):
    recovered_count = 0
    recovered_inr = 0.0
    attempts = 0

    for payment in payments:
        created_at = _parse_created_at(payment["created_at"])
        attempt_count = payment["attempt_count"]
        # Evaluate each payment from the moment it failed, then let the
        # policy's own delays advance the clock -- this is what the live
        # pipeline does across repeated runs, compressed into one pass.
        now = created_at

        while True:
            decision = decide(
                root_cause=payment["root_cause"],
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
        # Each policy gets its own identically-seeded RNG so they face the
        # same luck -- otherwise the comparison measures the seed, not the policy.
        results[name] = runner(payments, random.Random(seed))
    return results


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
        print(f"\nAgent policy vs naive retry-all: {(agent - naive) / naive * 100:+.1f}% recovered")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd analysis && python -m pytest test_compare_policies.py -v`
Expected: 6 passed.

If `test_different_seeds_can_produce_different_results` fails, that means the policies are not consuming randomness — investigate rather than weakening the test.

- [ ] **Step 5: Run the harness against the real batch**

Run: `cd analysis && python compare_policies.py`
Expected: a three-row table. Record the exact output in the task report — the controller needs these numbers for the README and the pitch.

- [ ] **Step 6: Commit**

```bash
git add analysis/
git commit -m "feat: add offline policy comparison harness with naive baseline"
```

---

