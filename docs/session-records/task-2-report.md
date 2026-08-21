# Task 2 Report: Policy comparison harness

## What was implemented

Created `analysis/compare_policies.py` and `analysis/test_compare_policies.py`, exactly as specified in the task brief (code used verbatim, no deviations). The harness:

- Loads `data/failed_payments.json` (the committed 75-record snapshot) with no other external dependencies (no Supabase, no Razorpay, no LLM).
- Runs three policies over a batch of payments:
  - `do_nothing` — recovers nothing, the floor.
  - `naive_retry_all` — retries every payment up to `MAX_ATTEMPTS` (3) times using `retry_payment`'s assumed success rate, regardless of root cause.
  - `agent_policy` — replays `decide()` from `decide/policy.py` for each payment, advancing a simulated clock by each decision's `next_action_at` until the policy reports `status == "exhausted"`.
- Uses `ASSUMED_SUCCESS_RATES` from `act/simulate_outcome.py` to simulate whether each attempt succeeds, via a per-policy `random.Random(seed)` instance so every policy faces identical luck for a given seed.
- `run_comparison(payments, seed=42) -> dict` returns `{policy_name: {"recovered_count", "recovered_inr", "attempts"}}`.
- `main()` prints a formatted three-row comparison table plus a percentage-lift line, and is the entry point for `python compare_policies.py`.

Neither `decide/policy.py` nor `act/simulate_outcome.py` was modified. `decide()` is called with exactly the four original keyword arguments (`root_cause`, `created_at`, `attempt_count`, `now`) — the harness does not pass `confidence` or `amount_inr`, per the controller's explicit instruction that the comparison measures routing-and-timing strategy with those gates off, even though a parallel task is adding those two keyword args (with defaults) to `decide()`.

## TDD process

### Step 1: Failing test written

`analysis/test_compare_policies.py` created verbatim from the brief — 6 tests covering: do-nothing recovers zero, all three policies are reported, determinism for a fixed seed, different seeds diverge, recovered amount never exceeds amount at risk, and attempts never exceed `3 * len(payments)`.

### Step 2: RED — verified the test fails for the expected reason

Command:
```
cd analysis && python -m pytest test_compare_policies.py -v
```

Output:
```
============================= test session starts =============================
platform win32 -- Python 3.14.3, pytest-9.1.1, pluggy-1.6.0 -- ...python.exe
cachedir: .pytest_cache
rootdir: D:\RazorPay\reclaim-agent\analysis
plugins: anyio-4.13.0, langsmith-0.11.1
collecting ... collected 0 items / 1 error

=================================== ERRORS ====================================
__________________ ERROR collecting test_compare_policies.py __________________
ImportError while importing test module 'D:\RazorPay\reclaim-agent\analysis\test_compare_policies.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
...
test_compare_policies.py:1: in <module>
    from compare_policies import run_comparison
E   ModuleNotFoundError: No module named 'compare_policies'
=========================== short test summary info ===========================
ERROR test_compare_policies.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
============================== 1 error in 0.32s ===============================
```

Matches the brief's expected failure exactly: `ModuleNotFoundError: No module named 'compare_policies'`.

### Step 3: Implementation written

`analysis/compare_policies.py` created verbatim from the brief.

### Step 4: GREEN — verified all tests pass

Command:
```
cd analysis && python -m pytest test_compare_policies.py -v
```

Output:
```
============================= test session starts =============================
platform win32 -- Python 3.14.3, pytest-9.1.1, pluggy-1.6.0 -- ...python.exe
cachedir: .pytest_cache
rootdir: D:\RazorPay\reclaim-agent\analysis
plugins: anyio-4.13.0, langsmith-0.11.1
collecting ... collected 6 items

test_compare_policies.py::test_do_nothing_recovers_nothing PASSED        [ 16%]
test_compare_policies.py::test_every_policy_is_reported PASSED           [ 33%]
test_compare_policies.py::test_results_are_deterministic_for_a_fixed_seed PASSED [ 50%]
test_compare_policies.py::test_different_seeds_can_produce_different_results PASSED [ 66%]
test_compare_policies.py::test_recovered_amount_never_exceeds_amount_at_risk PASSED [ 83%]
test_compare_policies.py::test_no_policy_exceeds_three_attempts_per_payment PASSED [100%]

============================== 6 passed in 0.03s ==============================
```

All 6 tests passed on the first implementation attempt; no iteration was needed. `test_different_seeds_can_produce_different_results` passed cleanly (policies do consume randomness distinctly), so no investigation of that failure mode was required.

### Step 5: Ran the harness against the real batch

Command:
```
cd analysis && python compare_policies.py
```

Output (exact):
```
Batch: 75 failed payments, Rs 1,040,061.78 at risk

policy                recovered     Rs recovered   attempts
------------------------------------------------------------
do_nothing                    0             0.00          0
naive_retry_all              58       781,709.77        149
agent_policy                 51       731,772.81        114

Agent policy vs naive retry-all: -6.4% recovered
```

Comparison table (for the README/pitch):

| policy | recovered_count | recovered_inr | attempts |
|---|---|---|---|
| do_nothing | 0 | 0.00 | 0 |
| naive_retry_all | 58 | 781,709.77 | 149 |
| agent_policy | 51 | 731,772.81 | 114 |

### Step 6: Commit

```
git add analysis/
git commit -m "feat: add offline policy comparison harness with naive baseline"
```

Commit: `486de13` — "feat: add offline policy comparison harness with naive baseline" (2 files changed, 185 insertions).

## Verification of constraints

- No file outside `analysis/` was modified: confirmed via `git status --short --untracked-files=all` and `git diff --stat` before committing — only `analysis/compare_policies.py` and `analysis/test_compare_policies.py` were staged/added. (An unrelated untracked `docs/superpowers/plans/...md` file exists in the working tree from outside this task and was not touched or staged.)
- `decide/policy.py` and `act/simulate_outcome.py` imported cleanly and their `decide()` signature is still the original 4-parameter form (`root_cause, created_at, attempt_count, now`) — confirmed via `inspect.signature`.
- The harness makes no network/DB imports — only `json`, `os`, `random`, `sys`, `datetime`, plus the local `policy` and `simulate_outcome` modules.
- `.env` was never touched or staged.

## Surprises / notes

- **`agent_policy` recovered less than `naive_retry_all` on the real batch (-6.4%)**, which is a genuinely interesting/surprising result rather than a bug. I verified this is not a defect in the harness:
  - Root-cause routing in `decide/policy.py` gives `auth_failure`, `card_declined_by_issuer` → `send_payment_link` (0.50 success rate) and `expired_card` → `prompt_card_update` (0.45), both higher than naive's flat `retry_payment` (0.35) — so per-attempt the agent's actions are individually *better* odds than naive's blind retry.
  - ~~The gap comes from two structural effects working against the agent policy in this batch: (a) the 72-hour stopping window (`STOPPING_WINDOW_HOURS`) causes the agent policy to abandon older payments before exhausting 3 attempts, while `naive_retry_all` has no age gate and always takes all 3 shots; and (b) the `insufficient_funds` cause carries a 6-hour delay before the first retry is even eligible, which compounds with the stopping window on payments already close to 72 hours old. Both effects reduce `agent_policy`'s attempt count (114 vs. naive's 149) and, on this particular batch/seed, its total recovery.~~
  - **CORRECTION (see Fix Round 1 below): this diagnosis was wrong.** The controller's fix-round review caught the real cause: `naive_retry_all` was ignoring `payment["attempt_count"]` and handing itself a fresh `MAX_ATTEMPTS` (3) budget, while `_run_agent_policy` correctly started from the payment's actual `attempt_count` (1) and was capped at `MAX_ATTEMPTS - attempt_count` = 2 by `decide()`'s own exhaustion check — a 50%-larger budget for naive, not a genuine strategy difference. I independently verified the 72h stopping window never actually fires for this batch (see Fix Round 1, Finding 1 verification) — my original claim about it was incorrect and is retracted. See the Fix Round 1 section below for the corrected root cause, the fix, and the corrected numbers.
- No deviations from the brief in the original implementation. All code blocks were used verbatim. (Fix round 1 below required deviating from the brief's exact code to correct two fairness defects the controller identified — see that section for what changed and why.)

## Commands run (full list)

1. `mkdir -p analysis`
2. Wrote `analysis/test_compare_policies.py`
3. `cd analysis && python -m pytest test_compare_policies.py -v` (RED, shown above)
4. Wrote `analysis/compare_policies.py`
5. `cd analysis && python -m pytest test_compare_policies.py -v` (GREEN, shown above)
6. `cd analysis && python compare_policies.py` (Step 5 output, shown above)
7. `git status --short` / `git diff --stat` / `git status --short --untracked-files=all` (scope verification)
8. `python -c "..."` import/signature check of `decide.policy` and `act.simulate_outcome` (unmodified, correct signature)
9. `git add analysis/`
10. `git commit -m "feat: add offline policy comparison harness with naive baseline"` → commit `486de13`

---

## Fix Round 1

The controller reviewed the initial submission and identified two fairness defects that invalidated the headline comparison. Both are fixed below, with covering regression tests written TDD-first (RED confirmed before implementing).

### Finding 1 (Critical): unequal attempt budgets between naive and agent policies

`_run_naive_retry_all` looped `for _ in range(MAX_ATTEMPTS)` — a fresh 3-attempt budget per payment, ignoring `payment["attempt_count"]`. `_run_agent_policy` starts from the payment's real `attempt_count` (1 in the real batch) and `decide()` exhausts once `attempt_count >= MAX_ATTEMPTS`, giving it at most 2 real attempts. Naive was getting a 50% larger budget purely from ignoring prior attempts, not from a better strategy.

**Fix:** `_run_naive_retry_all` now computes `remaining_budget = max(0, MAX_ATTEMPTS - payment["attempt_count"])` and loops that many times instead of a flat `MAX_ATTEMPTS`.

The controller also stated that my original claim — that the 72-hour stopping window was the cause of the agent's lower attempt count — was wrong, and asked me to verify independently rather than take it on faith. I did:

Command:
```
cd analysis && python -c "
import json, sys, os
sys.path.append(os.path.join('..', 'decide'))
sys.path.append(os.path.join('..', 'diagnose'))
from policy import decide, MAX_ATTEMPTS
from classifier import classify
from datetime import timedelta
from compare_policies import _parse_created_at

payments = json.load(open(os.path.join('..', 'data', 'failed_payments.json')))

age_window_hits = 0
attempt_count_hits = 0
max_age_seen = 0.0

for payment in payments:
    created_at = _parse_created_at(payment['created_at'])
    attempt_count = payment['attempt_count']
    predicted = classify(payment['error_reason'], payment['error_code']).root_cause
    now = created_at
    while True:
        d = decide(root_cause=predicted, created_at=created_at, attempt_count=attempt_count, now=now)
        age_hours = (now - created_at).total_seconds() / 3600
        max_age_seen = max(max_age_seen, age_hours)
        if d.status == 'exhausted':
            if 'stopping window' in d.reason:
                age_window_hits += 1
            else:
                attempt_count_hits += 1
            break
        attempt_count += 1
        now = d.next_action_at + timedelta(minutes=1)

print('exhausted via 72h stopping window:', age_window_hits)
print('exhausted via attempt_count limit:', attempt_count_hits)
print('max age (hours) ever seen at any decide() call:', round(max_age_seen, 2))
"
```

Output:
```
exhausted via 72h stopping window: 0
exhausted via attempt_count limit: 75
max age (hours) ever seen at any decide() call: 12.03
```

Confirmed: the 72h stopping window never fires anywhere in this harness — all 75 payments exhaust via the `attempt_count >= MAX_ATTEMPTS` check, and the maximum simulated age ever reached is ~12 hours (two `insufficient_funds` cycles at 6h delay each), far under the 72h threshold. My original diagnosis in the "Surprises / notes" section above was incorrect and has been struck through and corrected there. The real cause of the attempt-count gap (149 vs. 114 in the original run) was purely the unequal budget described above.

### Finding 2 (Important): agent policy routed on the ground-truth label instead of a predicted diagnosis

`_run_agent_policy` called `decide(root_cause=payment["root_cause"], ...)` — `root_cause` is the batch's synthetic ground-truth label, not something the live pipeline has access to. The live pipeline routes on `classify(error_reason, error_code).root_cause`, a rules-based classifier that is only ~89% accurate. Routing the offline harness on the ground-truth label credited the agent with diagnoses it would sometimes get wrong in production, overstating its performance.

**Fix:** `_run_agent_policy` now derives `predicted_root_cause = classify(payment["error_reason"], payment["error_code"]).root_cause` and passes that to `decide()` instead of `payment["root_cause"]`. Imported `classify` from `diagnose/classifier.py` (the rules-only classifier — `classify_with_llm` was deliberately not used, since it requires network access and would break offline reproducibility; per the controller's instruction, leaving out the LLM path's accuracy lift understates the agent rather than overstates it, which is the safe direction to publish). Added `sys.path.append(...diagnose)` alongside the existing `decide`/`act` path additions. `data/failed_payments.json` already carries `error_reason` and `error_code` on every record, so no data changes were needed.

### Covering tests (written TDD-first)

Added to `analysis/test_compare_policies.py`:

```python
def test_naive_retry_respects_same_lifetime_attempt_budget_as_agent_policy():
    # With attempt_count already at 2, MAX_ATTEMPTS (3) leaves exactly one
    # lifetime attempt for either policy -- decide() exhausts the agent
    # after that one attempt regardless of outcome, and naive must be held
    # to the same remaining budget rather than a fresh MAX_ATTEMPTS.
    batch = [{**payment, "attempt_count": 2} for payment in BATCH]
    results = run_comparison(batch, seed=42)
    assert results["naive_retry_all"]["attempts"] == len(batch)
    assert results["agent_policy"]["attempts"] == len(batch)


def test_naive_retry_gets_zero_attempts_when_lifetime_budget_exhausted():
    # attempt_count already at MAX_ATTEMPTS -- decide() reports "exhausted"
    # immediately, so naive's remaining budget must also be zero, not a
    # fresh MAX_ATTEMPTS handed out regardless of prior attempts.
    batch = [{**payment, "attempt_count": 3} for payment in BATCH]
    results = run_comparison(batch, seed=42)
    assert results["naive_retry_all"]["attempts"] == 0
    assert results["agent_policy"]["attempts"] == 0


def test_agent_policy_routes_on_classified_cause_not_the_root_cause_label():
    # The live pipeline routes on classify()'s predicted cause, not the
    # synthetic ground-truth root_cause label -- the label must not leak
    # into the agent's decision. Two batches that agree on error_reason /
    # error_code (what classify() sees) but disagree on the label must
    # produce identical agent_policy results.
    base = {
        "amount_inr": 1000.0,
        "error_reason": "the request timed out while contacting the bank",
        "error_code": "SERVER_ERROR",
        "created_at": "2026-08-20T10:00:00+00:00",
        "attempt_count": 1,
    }
    contradictory_batch = [
        {**base, "payment_id": f"pay_{i}", "root_cause": "expired_card"}
        for i in range(30)
    ]
    matching_batch = [
        {**base, "payment_id": f"pay_{i}", "root_cause": "network_timeout"}
        for i in range(30)
    ]

    contradictory_results = run_comparison(contradictory_batch, seed=42)
    matching_results = run_comparison(matching_batch, seed=42)

    assert contradictory_results["agent_policy"] == matching_results["agent_policy"]
```

The third test's design choice: rather than asserting on an internal helper, it builds two batches that agree on `error_reason`/`error_code` (what `classify()` sees) but disagree on `root_cause` (a deliberately contradictory ground-truth label — `expired_card` on a payment whose `error_reason` text unambiguously classifies as `network_timeout`). If the agent policy reads `root_cause`, the two batches produce different results (different action → different success rate/attempts); if it reads the classifier's output, results are identical because both batches feed `classify()` the same input. This is deterministic (not a statistical/flaky assertion) because `run_comparison` is fully deterministic for a fixed seed.

I also updated the shared `BATCH` fixture at the top of the test file to add `error_reason`/`error_code` fields (`"the request timed out while contacting the bank"` / `"SERVER_ERROR"`) consistent with its existing `root_cause: "network_timeout"`, since the agent policy now requires those keys and the pre-existing tests would otherwise KeyError.

### RED — new tests fail for the expected reasons

Command:
```
cd analysis && python -m pytest test_compare_policies.py -v
```

Relevant output (full run, 9 tests collected — 6 pre-existing + 3 new):
```
test_compare_policies.py::test_do_nothing_recovers_nothing PASSED        [ 11%]
test_compare_policies.py::test_every_policy_is_reported PASSED           [ 22%]
test_compare_policies.py::test_results_are_deterministic_for_a_fixed_seed PASSED [ 33%]
test_compare_policies.py::test_different_seeds_can_produce_different_results PASSED [ 44%]
test_compare_policies.py::test_recovered_amount_never_exceeds_amount_at_risk PASSED [ 55%]
test_compare_policies.py::test_no_policy_exceeds_three_attempts_per_payment PASSED [ 66%]
test_compare_policies.py::test_naive_retry_respects_same_lifetime_attempt_budget_as_agent_policy FAILED [ 77%]
test_compare_policies.py::test_naive_retry_gets_zero_attempts_when_lifetime_budget_exhausted FAILED [ 88%]
test_compare_policies.py::test_agent_policy_routes_on_classified_cause_not_the_root_cause_label FAILED [100%]

FAILED test_naive_retry_respects_same_lifetime_attempt_budget_as_agent_policy
  AssertionError: assert 91 == 50   (naive took 91 attempts, not the budget-capped 50)

FAILED test_naive_retry_gets_zero_attempts_when_lifetime_budget_exhausted
  AssertionError: assert 91 == 0   (naive still took attempts despite zero remaining budget)

FAILED test_agent_policy_routes_on_classified_cause_not_the_root_cause_label
  AssertionError: assert {..., 'attempts': 44} == {..., 'attempts': 46}
  (agent_policy differed between the contradictory-label batch and the correctly-labeled batch, proving root_cause was leaking into the decision)

3 failed, 6 passed in 0.16s
```

All three new tests failed for exactly the reasons the fixes address; the 6 pre-existing tests still passed against the updated fixture.

### GREEN — full suite passes after the fix

Command:
```
cd analysis && python -m pytest test_compare_policies.py -v
```

Output:
```
============================= test session starts =============================
platform win32 -- Python 3.14.3, pytest-9.1.1, pluggy-1.6.0 -- ...python.exe
cachedir: .pytest_cache
rootdir: D:\RazorPay\reclaim-agent\analysis
plugins: anyio-4.13.0, langsmith-0.11.1
collecting ... collected 9 items

test_compare_policies.py::test_do_nothing_recovers_nothing PASSED        [ 11%]
test_compare_policies.py::test_every_policy_is_reported PASSED           [ 22%]
test_compare_policies.py::test_results_are_deterministic_for_a_fixed_seed PASSED [ 33%]
test_compare_policies.py::test_different_seeds_can_produce_different_results PASSED [ 44%]
test_compare_policies.py::test_recovered_amount_never_exceeds_amount_at_risk PASSED [ 55%]
test_compare_policies.py::test_no_policy_exceeds_three_attempts_per_payment PASSED [ 66%]
test_compare_policies.py::test_naive_retry_respects_same_lifetime_attempt_budget_as_agent_policy PASSED [ 77%]
test_compare_policies.py::test_naive_retry_gets_zero_attempts_when_lifetime_budget_exhausted PASSED [ 88%]
test_compare_policies.py::test_agent_policy_routes_on_classified_cause_not_the_root_cause_label PASSED [100%]

============================== 9 passed in 0.03s ==============================
```

### Harness re-run against the real batch (corrected numbers)

Command:
```
cd analysis && python compare_policies.py
```

Output (exact):
```
Batch: 75 failed payments, Rs 1,040,061.78 at risk

policy                recovered     Rs recovered   attempts
------------------------------------------------------------
do_nothing                    0             0.00          0
naive_retry_all              47       651,382.95        120
agent_policy                 51       731,772.81        114

Agent policy vs naive retry-all: +12.3% recovered
```

**Corrected comparison table (for the README/pitch — supersedes the table in the original report above):**

| policy | recovered_count | recovered_inr | attempts |
|---|---|---|---|
| do_nothing | 0 | 0.00 | 0 |
| naive_retry_all | 47 | 651,382.95 | 120 |
| agent_policy | 51 | 731,772.81 | 114 |

**Agent policy vs naive retry-all: +12.3% recovered**, using equal lifetime attempt budgets (2 remaining attempts per payment for both policies, since every real-batch record has `attempt_count == 1`) and the agent routed on the rules-based classifier's prediction rather than the ground-truth label. The agent recovers more (Rs 731,772.81 vs. Rs 651,382.95) using fewer total attempts (114 vs. 120) — consistent with its routed actions (`send_payment_link` at 0.50, `prompt_card_update` at 0.45) having genuinely higher assumed success rates than naive's flat `retry_payment` at 0.35, even after accounting for the classifier's ~89% accuracy and equal budgets. This did not require tuning anything toward a win — it is the number that fell out of the fairness fixes. Per the controller's instruction, I would have reported a loss plainly if that's what resulted; it did not.

### Scope verification for the fix

```
git diff -- analysis/
```
confirmed changes touched only `analysis/compare_policies.py` (added the `diagnose` sys.path entry and `classify` import, the budget fix in `_run_naive_retry_all`, and the classifier-routing fix in `_run_agent_policy`) and `analysis/test_compare_policies.py` (fixture update + 3 new tests). No file outside `analysis/` was touched. `decide/policy.py` and `act/simulate_outcome.py` remain unmodified — only newly imported from (`diagnose/classifier.py`, read-only).

### Commit

```
git add analysis/
git commit -m "fix: equalize attempt budgets and route agent on classified cause" (full message includes rationale for both findings)
```

Commit: `efa414d` — "fix: equalize attempt budgets and route agent on classified cause" (2 files changed, 66 insertions, 2 deletions).

### Deviation from the original brief

The original brief's code block (Step 3) is no longer what's in `analysis/compare_policies.py` verbatim — Fix Round 1 changed `_run_naive_retry_all`'s budget loop and `_run_agent_policy`'s root-cause source, per the controller's explicit fix-round instructions. This is an intentional, controller-directed deviation, not an oversight; the brief's original code had the two fairness defects described above.

---

## Fix Round 2

An adversarial review found a real fairness defect in how the two fix-round-1 policies' randomness was paired, plus flagged that a single-seed lift number was being presented as "the" result. Both are addressed below, with covering regression tests written TDD-first (RED confirmed before implementing).

### Finding 1 (Critical): per-policy shared RNG stream broke luck pairing after divergence

Each policy got its own `random.Random(seed)` instance and consumed draws from it sequentially across the whole batch. The comment claimed this made policies "face the same luck," but that only holds up to the point where the two policies' per-payment draw counts first diverge. Since naive always checks against 0.35 (`retry_payment`) and the agent checks against whatever its routed action's rate is (0.45 `prompt_card_update` or 0.50 `send_payment_link` for most causes), a draw landing between the two thresholds makes one policy break out after 1 draw and the other consume a 2nd — after which every later payment's "same seed, same position" alignment between the two streams is gone; from that point the two policies are effectively facing independent randomness, not paired luck. The controller reported this starts at payment 22/75 (29% of the real batch affected).

**Fix:** each policy runner now creates a **fresh RNG per payment**, keyed to `f"{seed}:{payment['payment_id']}"`, rather than drawing from one shared stream for the whole batch:

```python
rng = random.Random(f"{seed}:{payment['payment_id']}")
```

This was applied identically in `_run_naive_retry_all` and `_run_agent_policy`. Because both policies key off the same `(seed, payment_id)` for a given payment, their first draw for that payment is guaranteed identical (same seed, same string, same starting position) regardless of what either policy did for any other payment — draws for payment N can never shift payment N+1's draws, for either policy. `_run_do_nothing` takes a `seed` parameter for signature consistency but doesn't use it (recovers nothing, consumes no randomness). `run_comparison` now passes the raw `seed` int to each runner instead of constructing a shared `random.Random(seed)` instance upfront.

Per the controller's explicit instruction, I did **not** use `hash((seed, payment_id))` — Python salts string/tuple hashing per process by default (`PYTHONHASHSEED` is randomized unless pinned), which would silently break cross-process reproducibility, the one property this harness cannot lose. `random.Random(str)` is explicitly documented as accepting an arbitrary hashable seed via a stable internal digest, not the built-in `hash()`, so it doesn't have this problem. I verified this directly (see the two-process check below) rather than taking it on faith.

`run_comparison`'s signature (`run_comparison(payments, seed: int = 42) -> dict`) and its return shape are unchanged.

### Finding 2 (Important): single-seed lift presented without a robustness signal

`main()` only ever computed and printed the seed-42 lift. Fix round 1's own report exhibited exactly the failure mode the controller warned about: a single, cherry-picked-looking number ("+12.3%", later "+24.5%" after the pairing fix) with no indication of how sensitive it is to the seed.

**Fix:** added a module-level `COMPARISON_SEEDS = list(range(1, 21))` (20 fixed seeds, visibly a constant rather than ad hoc) and a new `multi_seed_summary(payments, seeds=COMPARISON_SEEDS) -> dict` function that runs `run_comparison` once per seed, computes the agent-vs-naive lift percentage for each (skipping any seed where naive recovers nothing, to avoid division by zero — did not occur in practice for either the real batch or the 20 seeds), and returns:

```python
{
    "seeds": [...],
    "lift_pcts": [...],
    "median_lift_pct": ...,   # statistics.median, rounded to 1dp
    "min_lift_pct": ...,
    "max_lift_pct": ...,
}
```

`main()` now prints the existing seed-42 single-seed table (still needed — it's where the per-policy detail rows, `recovered_count`/`recovered_inr`/`attempts`, come from) followed by a second line reporting the 20-seed median and min–max range.

### Covering tests (written TDD-first)

Added to `analysis/test_compare_policies.py`:

```python
def test_a_payments_result_is_independent_of_other_payments_in_the_batch():
    # Randomness must be keyed per (seed, payment_id), not drawn from one
    # shared stream per policy -- otherwise how many draws one payment
    # consumes shifts every later payment's draws, and a batch's result
    # stops being the sum of each payment's own, isolated result. This is
    # the additivity that per-payment-keyed randomness guarantees and a
    # single shared stream does not. (A 2-payment batch isn't enough to
    # expose this reliably -- the first payment in any batch always sits
    # at draw 0 of a shared stream, so it takes several payments before
    # a shared stream's cumulative drift shows up.)
    sub_batch = BATCH[:10]
    combined = run_comparison(sub_batch, seed=42)
    isolated_sum = {
        policy: {
            "recovered_count": sum(
                run_comparison([p], seed=42)[policy]["recovered_count"] for p in sub_batch
            ),
            "attempts": sum(
                run_comparison([p], seed=42)[policy]["attempts"] for p in sub_batch
            ),
        }
        for policy in ("naive_retry_all", "agent_policy")
    }

    for policy in ("naive_retry_all", "agent_policy"):
        assert combined[policy]["recovered_count"] == isolated_sum[policy]["recovered_count"]
        assert combined[policy]["attempts"] == isolated_sum[policy]["attempts"]


def test_reordering_the_batch_does_not_change_the_result():
    # A corollary of per-payment-keyed randomness: the outcome for the
    # batch as a whole cannot depend on what order the payments are
    # processed in. A batch of content-identical payments can't expose an
    # ordering bug (swapping two identical items is a no-op), so this uses
    # a heterogeneous batch mixing several root causes / amounts.
    reasons_and_codes = [
        ("the request timed out while contacting the bank", "SERVER_ERROR"),
        ("authentication failed via otp", "GATEWAY_ERROR"),
        ("card declined by the issuing bank", "GATEWAY_ERROR"),
        ("the card has expired", "BAD_REQUEST_ERROR"),
        ("insufficient balance in account", "BAD_REQUEST_ERROR"),
    ]
    mixed_batch = [
        {
            "payment_id": f"pay_{i}",
            "amount_inr": 1000.0 + i,
            "root_cause": "unused",
            "error_reason": reasons_and_codes[i % len(reasons_and_codes)][0],
            "error_code": reasons_and_codes[i % len(reasons_and_codes)][1],
            "created_at": "2026-08-20T10:00:00+00:00",
            "attempt_count": 1,
        }
        for i in range(15)
    ]

    forward = run_comparison(mixed_batch, seed=42)
    backward = run_comparison(list(reversed(mixed_batch)), seed=42)
    assert forward == backward


def test_comparison_seeds_is_a_fixed_list_of_at_least_twenty_seeds():
    assert isinstance(COMPARISON_SEEDS, list)
    assert len(COMPARISON_SEEDS) >= 20
    assert len(set(COMPARISON_SEEDS)) == len(COMPARISON_SEEDS)  # no duplicates


def test_multi_seed_summary_is_deterministic():
    assert multi_seed_summary(BATCH, COMPARISON_SEEDS) == multi_seed_summary(BATCH, COMPARISON_SEEDS)


def test_multi_seed_summary_reports_median_and_range_consistently():
    summary = multi_seed_summary(BATCH, COMPARISON_SEEDS)
    assert summary["min_lift_pct"] <= summary["median_lift_pct"] <= summary["max_lift_pct"]
    assert len(summary["lift_pcts"]) == len(COMPARISON_SEEDS)
```

**Design notes on the two ordering tests:**

- The additivity test originally used just `BATCH[0], BATCH[1]` (2 payments), matching the shape hinted by earlier reviews. I checked this manually first and found it passed even against the pre-fix (shared-stream) code — with only 2 payments, the first payment in any run always sits at draw index 0 of a fresh `Random(seed)` regardless of scenario, so a 2-item batch can accidentally not expose the divergence. I verified this empirically (see commands below), then widened the test to `BATCH[:10]` and re-verified the pre-fix code does fail on it (combined recovered_count 8 vs. isolated-sum 10) before writing the fix.
- The reordering test originally used the full uniform `BATCH` (all `network_timeout`, which routes the agent to `retry_payment` — identical action and threshold to naive). I found empirically that reversing a batch of *content-identical* payments is a no-op under the pre-fix code too (swapping two indistinguishable items changes nothing), so it could never have been RED for the right reason. I rebuilt it with a **heterogeneous** 15-payment batch mixing five different root causes/error reasons (so agent and naive actually take different actions on different payments), verified the pre-fix code produces different results forward vs. reversed on this batch, then confirmed the fix makes it order-invariant.

### RED — confirmed for the expected reason

Because Finding 2's new symbols (`COMPARISON_SEEDS`, `multi_seed_summary`) don't exist in the pre-fix module, the whole test file fails to collect once they're imported — the same "module doesn't have this yet" pattern as the original Step 2 RED. To confirm Finding 1's two new tests specifically fail for the RNG-pairing reason (not just collection failure), I ran them standalone against the pre-fix `run_comparison` before touching `compare_policies.py`:

Command (additivity check, pre-fix code):
```
cd analysis && python -c "
from compare_policies import run_comparison
BATCH = [... 50 network_timeout payments ...]
sub_batch = BATCH[:10]
combined = run_comparison(sub_batch, seed=42)
for policy in ('naive_retry_all', 'agent_policy'):
    s_rc = sum(run_comparison([p], seed=42)[policy]['recovered_count'] for p in sub_batch)
    s_at = sum(run_comparison([p], seed=42)[policy]['attempts'] for p in sub_batch)
    print(policy, 'combined rc=', combined[policy]['recovered_count'], 'sum rc=', s_rc,
          'combined at=', combined[policy]['attempts'], 'sum at=', s_at)
"
```

Output:
```
naive_retry_all combined rc= 8 sum rc= 10 combined at= 16 sum at= 20
agent_policy combined rc= 8 sum rc= 10 combined at= 16 sum at= 20
```
Confirmed mismatch (8 ≠ 10) under the pre-fix shared-stream code — this is the real defect, reproduced directly.

Command (reordering check, pre-fix code, heterogeneous batch):
```
cd analysis && python -c "
from compare_policies import run_comparison
# ... 15-payment batch mixing 5 root causes ...
f = run_comparison(batch, seed=42)
b = run_comparison(list(reversed(batch)), seed=42)
print('equal:', f == b)
"
```

Output:
```
equal: False
```
Confirmed the pre-fix code is order-dependent on a heterogeneous batch — also the real defect, reproduced directly.

Full-suite collection error (Finding 2 symbols not yet implemented):
```
cd analysis && python -m pytest test_compare_policies.py -v
```
```
ImportError while importing test module 'D:\RazorPay\reclaim-agent\analysis\test_compare_policies.py'.
...
test_compare_policies.py:1: in <module>
    from compare_policies import COMPARISON_SEEDS, multi_seed_summary, run_comparison
E   ImportError: cannot import name 'COMPARISON_SEEDS' from 'compare_policies' (D:\RazorPay\reclaim-agent\analysis\compare_policies.py)
```

### GREEN — full suite passes after the fix

Command:
```
cd analysis && python -m pytest test_compare_policies.py -v
```

Output:
```
============================= test session starts =============================
platform win32 -- Python 3.14.3, pytest-9.1.1, pluggy-1.6.0 -- ...python.exe
cachedir: .pytest_cache
rootdir: D:\RazorPay\reclaim-agent\analysis
plugins: anyio-4.13.0, langsmith-0.11.1
collecting ... collected 14 items

test_compare_policies.py::test_do_nothing_recovers_nothing PASSED        [  7%]
test_compare_policies.py::test_every_policy_is_reported PASSED           [ 14%]
test_compare_policies.py::test_results_are_deterministic_for_a_fixed_seed PASSED [ 21%]
test_compare_policies.py::test_different_seeds_can_produce_different_results PASSED [ 28%]
test_compare_policies.py::test_recovered_amount_never_exceeds_amount_at_risk PASSED [ 35%]
test_compare_policies.py::test_no_policy_exceeds_three_attempts_per_payment PASSED [ 42%]
test_compare_policies.py::test_naive_retry_respects_same_lifetime_attempt_budget_as_agent_policy PASSED [ 50%]
test_compare_policies.py::test_naive_retry_gets_zero_attempts_when_lifetime_budget_exhausted PASSED [ 57%]
test_compare_policies.py::test_agent_policy_routes_on_classified_cause_not_the_root_cause_label PASSED [ 64%]
test_compare_policies.py::test_a_payments_result_is_independent_of_other_payments_in_the_batch PASSED [ 71%]
test_compare_policies.py::test_reordering_the_batch_does_not_change_the_result PASSED [ 78%]
test_compare_policies.py::test_comparison_seeds_is_a_fixed_list_of_at_least_twenty_seeds PASSED [ 85%]
test_compare_policies.py::test_multi_seed_summary_is_deterministic PASSED [ 92%]
test_compare_policies.py::test_multi_seed_summary_reports_median_and_range_consistently PASSED [100%]

============================== 14 passed in 0.12s ==============================
```

All 6 pre-existing (round 1) tests plus all 5 round-1 fairness tests plus the 3 new round-2 tests pass together — nothing regressed.

### Two-process reproducibility check (string-seed stability, per the controller's instruction to verify this myself)

Ran the harness twice, in two fully separate `python` process invocations, and diffed stdout byte-for-byte:

Command:
```
cd analysis && python compare_policies.py > /tmp/run1.txt
python compare_policies.py > /tmp/run2.txt
diff /tmp/run1.txt /tmp/run2.txt && echo "IDENTICAL - byte-for-byte reproducible across processes"
```

Output: `diff` produced no output (no differences) — `IDENTICAL - byte-for-byte reproducible across processes`. This confirms `random.Random(f"{seed}:{payment_id}")` (a formatted string seed) is stable across process restarts, unlike `hash((seed, payment_id))` would have been under default (randomized) `PYTHONHASHSEED`.

### Harness re-run against the real batch (corrected numbers, both tables)

Command:
```
cd analysis && python compare_policies.py
```

Output (exact, identical in both process runs above):
```
Batch: 75 failed payments, Rs 1,040,061.78 at risk

policy                recovered     Rs recovered   attempts
------------------------------------------------------------
do_nothing                    0             0.00          0
naive_retry_all              37       499,549.47        131
agent_policy                 46       622,120.65        118

Agent policy vs naive retry-all (seed=42): +24.5% recovered

Across 20 seeds (1-20): median lift +16.0%, range +3.4% to +27.6%
```

**Single-seed table (seed=42, for the per-policy detail rows):**

| policy | recovered_count | recovered_inr | attempts |
|---|---|---|---|
| do_nothing | 0 | 0.00 | 0 |
| naive_retry_all | 37 | 499,549.47 | 131 |
| agent_policy | 46 | 622,120.65 | 118 |

**Agent policy vs naive retry-all (seed=42): +24.5% recovered**

**Multi-seed summary (20 fixed seeds, 1 through 20):**

| seed | lift | seed | lift |
|---|---|---|---|
| 1 | +4.2% | 11 | +16.9% |
| 2 | +18.7% | 12 | +15.1% |
| 3 | +20.3% | 13 | +7.3% |
| 4 | +10.7% | 14 | +27.2% |
| 5 | +21.4% | 15 | +18.5% |
| 6 | +17.8% | 16 | +21.3% |
| 7 | +20.2% | 17 | +27.6% |
| 8 | +11.9% | 18 | +9.8% |
| 9 | +8.0% | 19 | +5.9% |
| 10 | +10.9% | 20 | +3.4% |

**Median lift: +16.0%. Range: +3.4% to +27.6%. The agent beat naive retry-all in all 20 of 20 seeds tested** (verified programmatically: `all(lift > 0 for lift in lift_pcts)` is `True`).

### On the outcome

The single-seed number moved substantially between fix round 1 (+12.3% at seed=42, computed under the broken shared-stream pairing) and this round (+24.5% at seed=42, correctly paired). This is expected and was not tuned toward — it's the direct, mechanical consequence of fixing the RNG-pairing defect, run once and reported as-is, same as round 1's own instruction. I did not go looking for a seed or parameter that produces a larger number; `COMPARISON_SEEDS` was fixed as `list(range(1, 21))` before running anything, and I ran the harness exactly once per seed to build the summary.

The headline the controller can safely publish is the **multi-seed one, not the seed-42 point estimate**: median +16.0% lift, with every one of the 20 fixed seeds showing a positive lift (min +3.4%). The agent's advantage did not shrink or disappear under correct pairing — if it had, this section would say so plainly, per the controller's explicit instruction. The seed-42 single-seed table remains useful only as the source of the per-policy detail rows (`recovered_count`, `recovered_inr`, `attempts`), not as the headline lift figure.

### Scope verification for the fix

```
git status --short --untracked-files=all
git diff --stat -- analysis/
```
Confirmed only `analysis/compare_policies.py` and `analysis/test_compare_policies.py` changed (65 and 78 lines respectively, all additions/modifications within those two files). No file outside `analysis/` was touched. `decide/policy.py`, `act/simulate_outcome.py`, and `diagnose/classifier.py` remain unmodified — read-only imports throughout.

### Commit

```
git add analysis/
git commit -m "fix: pair policy luck per payment and report lift across many seeds" (full message includes rationale for both findings)
```

Commit: `a861892` — "fix: pair policy luck per payment and report lift across many seeds" (2 files changed, 135 insertions, 8 deletions).

### Deviation from prior rounds

`_run_do_nothing`, `_run_naive_retry_all`, and `_run_agent_policy` now take a `seed: int` parameter instead of a `random.Random` instance (`rng`) — a deliberate, controller-directed signature change needed to let each runner construct its own per-payment RNGs. `run_comparison`'s own public signature and return shape are unchanged, per the controller's explicit constraint. This is an intentional deviation from the round-1 code, not an oversight; round 1's per-policy-shared-stream approach had the RNG-pairing defect this round fixes.
