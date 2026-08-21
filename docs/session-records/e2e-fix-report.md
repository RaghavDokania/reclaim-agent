# reclaim-agent — E2E Defect Fix Report

**Date:** 2026-08-21
**Branch:** `tier1-improvements`
**Base:** `66c768f` (70 tests passing)
**Head after fixes:** `3dfbde8`
**Method:** TDD throughout — failing test first, run it, confirm the failure reason, then implement.

---

## Summary

| Finding | Status | Commit |
|---|---|---|
| A-3 — human-approval path is an infinite loop | **Fixed** | `ed1d409` |
| A-1 — non-`BadRequestError` Razorpay failure kills the batch | **Fixed** | `52cd300` |
| D-1 — every published number decays daily | **Fixed** | `101e3c5` |
| LLM overconfidence — confidence gate never fires | **Fixed (effect unverified)** | `524e76c` |
| A-2 — undocumented lifetime payment-link cap | **Documented** | `3dfbde8` |

**Tests: 70 → 96.** All 70 originals still pass; none was weakened, skipped, or edited (the two existing test files were appended to only).

```
$ python -m pytest -q          # baseline, before any change
......................................................................   [100%]
70 passed in 0.81s

$ python -m pytest -q          # after all five commits
........................................................................ [ 75%]
........................                                                 [100%]
96 passed in 0.82s
```

---

## A-3 (CRITICAL) — human approval is now durable

**Defect.** `approve.py` set `status='diagnosed'` and logged `human_approved`, but recorded nothing durable. `run_decisions.py` re-read the row and `decide()` re-applied the identical value gate to the identical amount, returning it to `needs_approval`. Observed live: released, re-held 11 seconds later. Same defect on the confidence gate. No held payment could ever be recovered.

**Fix.**

1. `log/migrations/003_add_human_approval.sql` — `alter table failed_payments add column if not exists human_approved_at timestamptz;`, with a comment explaining why the column exists.
2. `log/supabase_schema.sql` — column folded in next to `next_action_at`, commented.
3. `decide/approve.py` — stamps `human_approved_at` (UTC ISO) in the **same** `update_payment` call as the status, so the row is never briefly `diagnosed` without its stamp, and includes it in the `human_approved` audit detail.
4. `decide/policy.py` — `decide()` gains `human_approved: bool = False`. When True, both gates are skipped; both stopping rules still apply and still come first. The reason string gains `" (gates skipped: a human approved this payment)"`.
5. `decide/run_decisions.py` — passes `human_approved=bool(row.get("human_approved_at"))`.

**RED (observed before implementing):**

```
$ cd decide && python -m pytest test_policy.py -q
E       TypeError: decide() got an unexpected keyword argument 'human_approved'
...
FAILED test_policy.py::test_human_approved_high_value_payment_is_actioned_instead_of_held
FAILED test_policy.py::test_human_approved_low_confidence_payment_is_actioned_instead_of_held
FAILED test_policy.py::test_human_approved_reason_records_that_a_human_authorised_it
FAILED test_policy.py::test_human_approval_does_not_resurrect_a_payment_out_of_attempts
FAILED test_policy.py::test_human_approval_does_not_resurrect_a_payment_past_the_72h_window
5 failed, 17 passed in 0.19s
```

```
$ cd decide && python -m pytest test_run_decisions.py -q
>       assert counts["action_taken"] == 1
E       assert 0 == 1
FAILED test_run_decisions.py::test_a_row_stamped_human_approved_at_is_actioned_not_re_held
1 failed, 5 passed in 0.73s
```

```
$ cd decide && python -m pytest test_approve.py -q
E       KeyError: 'human_approved_at'      (x2)
FAILED test_approve.py::test_approve_stamps_human_approved_at_in_the_same_update
FAILED test_approve.py::test_the_human_approved_event_records_the_approval_timestamp
2 failed, 4 passed in 0.73s
```

**GREEN:**

```
$ cd decide && python -m pytest -q
..................................                                       [100%]
34 passed in 0.57s
```

Tests added (13): approved high-amount → `action_taken`; approved low-confidence → `action_taken`; reason names the human; approved-but-out-of-attempts → still `exhausted`; approved-but-past-72h → still `exhausted`; `human_approved=False` default still holds both gates; `run_decisions` honours / does not invent the stamp; plus six in the new `decide/test_approve.py` covering the release itself (single update, tz-aware recent timestamp, audit detail, non-held and unknown payments are no-ops).

---

## A-1 (CRITICAL) — one payment's API failure can no longer end the batch

**Verification of the base class before relying on it — this is the notable finding:**

```
$ python -c "import razorpay.errors as e; print([n for n in dir(e) if 'Error' in n]); print(e.ServerError.__mro__)"
['BadRequestError', 'GatewayError', 'ServerError', 'SignatureVerificationError']
(<class 'razorpay.errors.ServerError'>, <class 'Exception'>, <class 'BaseException'>, <class 'object'>)
```

**There is no `razorpay.errors.RazorpayError` in the installed package.** All four error classes subclass `Exception` directly — the SDK defines no shared base. `except razorpay.errors.RazorpayError` as briefed would have raised `AttributeError` at import time and made the crash worse. Source confirmed by `inspect.getsource(razorpay.errors)`.

**Fix.** `act/run_actions.py` builds the catch tuple from the errors module itself, so any error class Razorpay adds later is covered the day it appears, plus the transport layer underneath:

```python
RECOVERABLE_API_ERRORS = tuple(
    obj for obj in vars(razorpay.errors).values()
    if isinstance(obj, type) and issubclass(obj, Exception)
) + (requests.exceptions.RequestException,)
```

Resolves to `(BadRequestError, GatewayError, ServerError, SignatureVerificationError, RequestException)` (verified at runtime). Behaviour on catch is byte-for-byte unchanged: `action_error` event with the same `{"action": ..., "error": ...}` detail shape, row status untouched for retry next run, counter incremented, sleep, continue.

`requests` added to `requirements.txt` — `run_actions.py` now imports it directly rather than relying on it being razorpay's transitive dependency.

**RED:**

```
$ cd act && python -m pytest test_run_actions.py -q
E           requests.exceptions.ConnectionError: connection reset
FAILED test_run_actions.py::test_a_server_error_on_one_payment_does_not_stop_the_batch
FAILED test_run_actions.py::test_a_server_error_leaves_the_failing_row_status_untouched_for_retry
FAILED test_run_actions.py::test_a_transport_failure_is_caught_per_row_too
3 failed, 1 passed in 0.80s
```

(The 4th, `test_a_bad_request_error_is_still_caught_per_row`, passed from the start — it is the regression guard on the path that already worked.)

**GREEN:**

```
$ cd act && python -m pytest -q
.........                                                                [100%]
9 passed in 0.58s
```

New `act/test_run_actions.py` injects a fake Razorpay client whose `order.create` / `payment_link.create` raise on demand, and a stub Supabase. **No network is touched.** The tests prove: a `ServerError` on row 1 still lets row 2's API call and `action_executed` event happen; the failing row gets **zero** status updates (so it is retried, not corrupted) and one `action_error` carrying the real error text; a `requests.exceptions.ConnectionError` behaves the same; `BadRequestError` still behaves as before.

---

## D-1 (HIGH) — `--rebase-timestamps`

**Fix.** Two pure functions in `ingest/load_batch.py`, plus an argparse flag:

- `rebase_timestamps(records, now) -> (records, offset)` — computes the single offset that moves the **newest** record to `now`, applies it to every record, returns new dicts. Never mutates its input. Matches `now`'s tz-awareness to the data's (the snapshot's timestamps are naive) so the subtraction is always valid.
- `build_records(batch, rebase=False, now=None) -> (records, offset_or_None)` — strips `audit_log`, optionally rebases. This is what `main()` calls.

`data/failed_payments.json` is **not** modified or regenerated; the rebase is in memory at load time only. The offset is printed to stdout when applied:

```
Rebased created_at forward by <offset> (in memory; data/failed_payments.json untouched).
```

**RED:**

```
$ cd ingest && python -m pytest test_load_batch.py -q
E   ImportError: cannot import name 'build_records' from 'load_batch'
1 error in 0.80s
```

**GREEN:**

```
$ cd ingest && python -m pytest test_load_batch.py -q
.......                                                                  [100%]
7 passed in 0.55s
$ python -m py_compile load_batch.py     # exit 0
```

Tests (7): newest record lands within 60s of now; every relative gap is preserved **exactly** (compared as `timedelta` list equality, not approximately); the returned offset equals `now - newest` exactly; the input records are not mutated; without the flag timestamps are byte-identical and offset is `None`; with the flag they move forward; `audit_log` is stripped either way. No Supabase, no file writes.

`load_batch.py` was **not executed** (per constraints — it constructs a live client). `python -m py_compile` used as the check.

---

## LLM overconfidence — prompt tightened (effect UNVERIFIED)

`diagnose/llm_classifier.py` `PROMPT_TEMPLATE` now:

- states the test as an obligation — *"You MUST answer 'low' whenever the text is consistent with more than one cause, even if one feels more likely"*;
- closes the loophole the model was using — *"You MUST still name your single best guess in root_cause … Naming a best guess does NOT make your confidence high"*;
- redefines `high` as requiring evidence that **rules out** every other cause ("The text must distinguish, not merely suggest");
- works the concrete failing case through as an in-prompt example: `"Transaction declined by bank"` is consistent with **both** `card_declined_by_issuer` **and** `auth_failure`, so the correct answer is the best guess at `confidence: "low"`;
- ends with an explicit tie-break: *"When in doubt between the two, answer 'low'."*

**Parsing contract unchanged.** `_parse`, `VALID_ROOT_CAUSES`, `VALID_CONFIDENCES`, the unknown-confidence→`low` default and all three `code_fallback` paths are untouched. All 12 pre-existing LLM tests still pass.

**RED** (new test asserts the prompt names the concrete case and states the rule as an obligation):

```
$ cd diagnose && python -m pytest test_llm_classifier.py -q
>       assert "must" in prompt.lower()
E       assert 'must' in 'you are triaging a failed payment for an indian payment gateway...'
FAILED test_llm_classifier.py::test_the_prompt_gives_the_model_the_concrete_ambiguous_case_as_an_example
1 failed, 12 passed in 0.16s
```

**⚠️ The live effect of this change is UNVERIFIED and cannot be verified offline.** The test proves the instruction is present and phrased as an obligation; it cannot prove the model will obey it. Whether the `low` rate on ambiguous reasons actually rises — and whether the confidence gate finally fires — must be measured by a later live run. The honest current claim remains the measured one: 1 of 15 escalations returned `low`, and all 5 incorrect diagnoses returned `high`.

---

## A-2 (HIGH) — documented

Added a call-out block under README setup step 3 (Razorpay key), stating: the cap is 30 payment links **per account, for the lifetime of the account**, not a rate limit and not recoverable by waiting; a full sweep of this batch can request ~38 (17 `auth_failure` + 14 `card_declined_by_issuer` + 7 `expired_card`), so a reproducer will hit it; the failure is `ServerError: test mode limit of 30 reached for payment_link`, now caught **per row**; hitting it leaves the affected payments in `action_taken` and understates the recovery figure, rather than making the run wrong or corrupting state; `retry_payment` uses Orders, which are not capped this way.

No other README change was made. The "Live example" metrics table and every recovered-rupee figure are untouched.

---

## Migrations needing manual application

**`log/migrations/003_add_human_approval.sql` must be run against the live Supabase project before the next live run.**

```sql
alter table failed_payments add column if not exists human_approved_at timestamptz;
```

Supabase → SQL Editor → New query → Run. Without it, `approve.py` will fail on the update (unknown column) and A-3 remains unfixed in practice. `run_decisions.py` degrades safely if the column is missing (`row.get("human_approved_at")` → `None` → `human_approved=False`, i.e. today's behaviour), so the decide layer will not break — but no approval will ever take effect.

`001` and `002` are unchanged and still required.

---

## Deliberately not done, with reasons

1. **`except razorpay.errors.RazorpayError` as literally briefed.** That class does not exist in the installed SDK (verified above); using it would have raised `AttributeError` at import. Used a tuple built from the errors module instead, which is strictly broader and self-maintaining.
2. **Did not document `--rebase-timestamps` in the README.** The brief restricted README edits to the A-2 note and asked for other corrections to be listed rather than applied. Listed below — this one matters, because an undocumented flag will not be used by a judge.
3. **Did not run `run_pipeline.py`, `load_batch.py`, `approve.py`, or anything constructing a live client**, per constraints. All scripts checked with `python -m py_compile`; all behaviour covered by offline tests with injected fakes.
4. **Did not touch `data/failed_payments.json`.** `git status` shows it unmodified.
5. **Did not fix E-1** (the `diagnosed` event omits `error_reason`; the `recovered` event omits the rupee amount). Out of scope for this brief and not among the assigned findings.
6. **Did not fix the README's other 10 listed corrections** (stale metrics table, setup step 6 regenerating the snapshot, etc.) — explicitly out of scope; a re-run refreshes the numbers.
7. **Did not pin `requirements.txt` versions** (R-1). Out of scope, and pinning against an unverified live environment risks breaking a working install. Added only `requests`, which my own change now imports directly.

---

## Other README corrections believed needed (NOT applied)

Beyond the 11 already listed in §10 of the verification report, all still outstanding:

1. **Document `--rebase-timestamps`** in setup step 6 / "Reproducing the metrics". Recommended wording: run `cd ingest && python load_batch.py --rebase-timestamps` to load the committed batch with its ages shifted to the present, so the 72h stopping rule sees the same relative ages the batch was designed around. Without this the flag is invisible and D-1 is fixed in code but not in practice.
2. **README line 33's "Held payments are not a dead end" claim can now stand** — A-3 is fixed. It should additionally note that approval overrides the two gates but **not** the stopping rules, since that is the compliance-relevant part of the design.
3. **README's "Safe to re-run anytime" (line ~43) can now stand** for the act layer — A-1 is fixed. Worth adding that an API failure is logged as `action_error` and the row is retried on the next run.
4. **Setup step 2 must now list three migrations**, not two; `003_add_human_approval.sql` is required for the human-approval path to work at all.

---

## Concerns

1. **The LLM prompt fix is unverified live.** Prompt engineering against an overconfident model is not guaranteed to work. The next live run should re-measure the `low` rate on the 15 ambiguous escalations before any claim about the confidence gate is published. If it is still ~1/15, the gate needs a mechanical trigger (e.g. treat a known-ambiguous reason string as low regardless of what the model says) rather than a persuasive one.
2. **Migration 003 is not applied.** Everything in A-3 is inert until it is.
3. **A-2 is a hard external ceiling.** The current test account has already consumed its 30 links permanently. A clean full-sweep demonstration needs a **fresh** Razorpay test account, and even then ~38 requested links exceed 30 — so a complete, unblocked sweep of this batch is not achievable on one test account at all. This is documented, not solved, and the recovery figure will understate the policy for that reason.
4. **The value gate's live population changes once D-1 is used.** With `--rebase-timestamps`, far fewer payments exhaust on age, so many more of the 17 high-value records will reach the value gate and be held. Expect held count and held amount to rise sharply, and expect `recovered` to rise too. Whoever re-runs should not read that as a regression.
