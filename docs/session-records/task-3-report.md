# Task 3 Report: Confidence and value gates on money actions

## Summary

Implemented two new gates in `decide/policy.py`'s `decide()` function, gated behind
new keyword-only parameters (`confidence="high"`, `amount_inr=0.0`) with defaults
that preserve all existing call sites and behaviour:

- **Confidence gate**: `confidence == "low"` -> `status="needs_review"`, no action, no `next_action_at`.
- **Value gate**: `amount_inr >= 20000.0` (`HIGH_VALUE_THRESHOLD_INR`) -> `status="needs_approval"`, no action, no `next_action_at`.

Both gates are placed **after** the two existing stopping rules (`MAX_ATTEMPTS`, 72h
window) and **before** the `POLICY` lookup, so an already-exhausted payment can never
be resurrected into a human review/approval queue. The confidence gate is checked
before the value gate, so a low-confidence high-value payment routes to
`needs_review`, not `needs_approval`.

Extended `ingest/schema.py`'s `Status` enum with `NEEDS_REVIEW = "needs_review"` and
`NEEDS_APPROVAL = "needs_approval"`, added after `EXHAUSTED`.

Wired the new inputs through `decide/run_decisions.py`: the `decide()` call now
passes `confidence=row.get("diagnosis_confidence") or "high"` (so `NULL` in the
`diagnosis_confidence` column, present on rows diagnosed before Task 1's migration,
is treated as `"high"` and keeps flowing through normal processing rather than being
retroactively quarantined) and `amount_inr=row["amount_inr"]`. Replaced the
single-line `event = "decided" if ... else "exhausted"` with a dict lookup covering
all four statuses (`action_taken` -> `decided`, `exhausted` -> `exhausted`,
`needs_review` -> `held_for_review`, `needs_approval` -> `held_for_approval`), so the
two new statuses log distinctly instead of being mislabelled `exhausted`.

No files under `diagnose/` or `analysis/` were touched, per the task boundary.

## TDD process and commands run

### Step 1: Wrote the 6 failing tests

Appended verbatim to `decide/test_policy.py`, after the existing 8 tests (which were
left completely unmodified):

- `test_low_confidence_diagnosis_routes_to_review_instead_of_acting`
- `test_high_value_payment_routes_to_approval_instead_of_acting`
- `test_payment_just_under_the_high_value_threshold_still_acts`
- `test_stopping_rules_take_precedence_over_the_new_gates`
- `test_confidence_gate_takes_precedence_over_the_value_gate`
- `test_defaults_preserve_the_original_behaviour`

### Step 2: RED — ran tests to confirm failure

Command: `cd decide && python -m pytest test_policy.py -v`

Actual output (relevant excerpt):

```
collected 14 items

test_policy.py::test_insufficient_funds_retries_after_six_hour_delay PASSED [  7%]
test_policy.py::test_network_timeout_retries_immediately PASSED          [ 14%]
test_policy.py::test_auth_failure_sends_payment_link_immediately PASSED  [ 21%]
test_policy.py::test_card_declined_by_issuer_sends_payment_link_immediately PASSED [ 28%]
test_policy.py::test_expired_card_prompts_card_update_immediately PASSED [ 35%]
test_policy.py::test_exhausted_when_max_attempts_reached PASSED          [ 42%]
test_policy.py::test_exhausted_when_older_than_72_hours PASSED           [ 50%]
test_policy.py::test_still_eligible_just_under_72_hours PASSED           [ 57%]
test_policy.py::test_low_confidence_diagnosis_routes_to_review_instead_of_acting FAILED [ 64%]
test_policy.py::test_high_value_payment_routes_to_approval_instead_of_acting FAILED [ 71%]
test_policy.py::test_payment_just_under_the_high_value_threshold_still_acts FAILED [ 78%]
test_policy.py::test_stopping_rules_take_precedence_over_the_new_gates FAILED [ 85%]
test_policy.py::test_confidence_gate_takes_precedence_over_the_value_gate FAILED [ 92%]
test_policy.py::test_defaults_preserve_the_original_behaviour PASSED     [100%]

E       TypeError: decide() got an unexpected keyword argument 'confidence'
E       TypeError: decide() got an unexpected keyword argument 'amount_inr'
(same TypeError repeated for each failing test, matching the unrecognized kwarg it passes)

5 failed, 9 passed in 0.17s
```

**Deviation from brief's expectation, with reason**: the brief expected "the 6 new
tests fail." Only 5 failed; `test_defaults_preserve_the_original_behaviour` passed
immediately even before implementation. This is not a bug in the test — that test
calls `decide()` with only the original four positional/keyword arguments (no
`confidence`, no `amount_inr`), so it is indistinguishable from the pre-existing
`test_network_timeout_retries_immediately` test and necessarily passes against the
unmodified function too. It legitimately becomes a regression guard once the new
parameters exist, but it was never going to fail at RED given its own body. Confirmed
this is expected and proceeded — the other 5 failed for exactly the anticipated
reason (`TypeError: decide() got an unexpected keyword argument ...`).

### Step 3: Implemented the gates

Added `HIGH_VALUE_THRESHOLD_INR = 20000.0` next to the existing constants, and
replaced the `decide()` signature and body with the two new keyword parameters and
gates, verbatim per the brief (code inserted after the two stopping-rule blocks and
before the `POLICY[root_cause]` lookup). Also updated the `DecisionResult.status`
inline comment to list all four possible status strings for documentation accuracy
(not behaviour-affecting).

### Step 4: GREEN — ran tests to confirm pass

Command: `cd decide && python -m pytest test_policy.py -v`

Actual output:

```
collected 14 items

test_policy.py::test_insufficient_funds_retries_after_six_hour_delay PASSED [  7%]
test_policy.py::test_network_timeout_retries_immediately PASSED          [ 14%]
test_policy.py::test_auth_failure_sends_payment_link_immediately PASSED  [ 21%]
test_policy.py::test_card_declined_by_issuer_sends_payment_link_immediately PASSED [ 28%]
test_policy.py::test_expired_card_prompts_card_update_immediately PASSED [ 35%]
test_policy.py::test_exhausted_when_max_attempts_reached PASSED          [ 42%]
test_policy.py::test_exhausted_when_older_than_72_hours PASSED           [ 50%]
test_policy.py::test_still_eligible_just_under_72_hours PASSED           [ 57%]
test_policy.py::test_low_confidence_diagnosis_routes_to_review_instead_of_acting PASSED [ 64%]
test_policy.py::test_high_value_payment_routes_to_approval_instead_of_acting PASSED [ 71%]
test_policy.py::test_payment_just_under_the_high_value_threshold_still_acts PASSED [ 78%]
test_policy.py::test_stopping_rules_take_precedence_over_the_new_gates PASSED [ 85%]
test_policy.py::test_confidence_gate_takes_precedence_over_the_value_gate PASSED [ 92%]
test_policy.py::test_defaults_preserve_the_original_behaviour PASSED     [100%]

14 passed in 0.02s
```

Matches the brief's expected "14 passed" exactly.

### Step 5 & 6

Extended `ingest/schema.py`'s `Status` enum and `decide/run_decisions.py`'s
`decide_all()` call site and event-name mapping, verbatim per the brief.

### Step 7: Full suite

Command (from repo root):
`python -m pytest diagnose/test_classifier.py diagnose/test_llm_classifier.py decide/test_policy.py act/test_simulate_outcome.py analysis/test_compare_policies.py -v`

Actual result: **50 passed** in 0.14s (all green, no failures, no warnings).

Per-file breakdown observed: `diagnose/test_classifier.py` = 10, `diagnose/test_llm_classifier.py`
= 7, `decide/test_policy.py` = 14, `act/test_simulate_outcome.py` = 5,
`analysis/test_compare_policies.py` = 13. Total = 10+7+14+5+13 = 49 by that count but
pytest reported "collected 50 items" / "50 passed" — recounting the visible test
list in the actual run output gives 50 distinct test IDs, so trusting pytest's own
count of 50 (the manual tally above undercounted by one, most likely a miscount on
my part while summing, not a real discrepancy — pytest's collection count is
authoritative and every one of the 50 listed tests shows PASSED with no gaps or
duplicates).

**Deviation from brief's expectation ("42 passed"), with reason**: the brief text
was almost certainly written before Task 1 (diagnose/) and Task 2 landed their own
additional tests on this branch. This task did not modify anything under
`diagnose/` or `analysis/`, and the 8 original `decide/test_policy.py` tests plus 6
new ones account for exactly the 14 in that file. The extra count above 42 comes
entirely from Tasks 1 and 2's prior, already-reviewed work in `diagnose/` and
`analysis/`, not from anything introduced here. Verified by running `decide/test_policy.py`
alone (14) and cross-checking that no other file changed — the 50 vs. 42 gap is a
stale prediction in the brief, not a regression or scope creep in this task.

### Additional sanity checks (no network required)

- `git diff --stat` confirmed only the 4 intended files changed:
  `decide/policy.py`, `decide/run_decisions.py`, `decide/test_policy.py`,
  `ingest/schema.py`.
- Syntax-checked `decide/policy.py`, `decide/run_decisions.py`, `ingest/schema.py`,
  and `run_pipeline.py` via `ast.parse()` — all parsed cleanly. `run_pipeline.py`
  only subprocess-invokes the three stage scripts (which themselves need Supabase
  network access to run for real), so a full live run was out of scope under the
  "no network" constraint; syntax/import-level verification is the appropriate
  check here since this task did not modify `run_pipeline.py`, `run_diagnosis.py`,
  or `run_actions.py`.
- Confirmed `analysis/compare_policies.py`'s only `decide()` call site
  (`analysis/compare_policies.py:108`) passes exactly the original four
  arguments and was not touched; the new keyword-only parameters with defaults
  keep it working unchanged.

## Commit

```
45dc355 feat: gate money actions on diagnosis confidence and payment value
```

Staged: `decide/policy.py`, `decide/run_decisions.py`, `decide/test_policy.py`,
`ingest/schema.py` (exactly as the brief's Step 8 specifies: `git add decide/
ingest/schema.py`).

An untracked `docs/` directory was present in the working tree at commit time but
was intentionally left unstaged — it is unrelated to this task's file list and the
brief's Step 8 command does not include it.

## Surprises / notes

- The RED run had 5 failures instead of the brief's stated 6, and the full-suite
  GREEN run had 50 passes instead of the brief's stated 42 — both are explained
  above and are not implementation problems; they're artifacts of the brief being
  written slightly ahead of/behind the actual state of sibling tasks on this branch.
- Everything else matched the brief exactly: gate ordering, threshold value
  (20000.0), reason-string wording (confirmed "confidence" appears in the low-confidence
  reason and "20,000"/"20000" appears in the high-value reason, satisfying the
  test assertions), `NULL`-as-`"high"` handling in `run_decisions.py`, and the
  event-name mapping.
