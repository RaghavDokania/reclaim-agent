# SDD ledger — plan: docs/superpowers/plans/2026-08-20-tier1-improvements.md

Branch: tier1-improvements (created off main at 79802b3)

## Pre-flight conflict scan

| Rows | Shared surface | What one produces vs what the other consumes | Finding |
|---|---|---|---|
| Task 1 ↔ Task 2 | none | Task 1 touches `diagnose/`, `log/`, `requirements.txt`; Task 2 touches `analysis/` only | Clean — no overlap |
| Task 1 ↔ Task 3 | `failed_payments.diagnosis_confidence` column | Task 1 creates + populates the column; Task 3 reads it in `run_decisions.py` | Ordering dependency: Task 3 must follow Task 1. Plan already specifies NULL→"high" handling for pre-existing rows. No file overlap (`diagnose/` vs `decide/`) |
| Task 2 ↔ Task 3 | `decide()` signature in `decide/policy.py` | Task 3 adds `confidence` / `amount_inr` kwargs (defaulted); Task 2's harness calls `decide()` without them | **Conflict surfaced** — see Ruling 1 |
| Task 1 self-check | — | Tests in Step 2 vs impl in Step 5; files created vs files referenced | Consistent |
| Task 2 self-check | — | Tests in Step 1 vs impl in Step 3; attempt accounting traced by hand (max 2 attempts from attempt_count=1, under the `<= n*3` assertion) | Consistent |
| Task 3 self-check | — | 6 new tests vs impl; new kwargs defaulted so the 8 existing tests are unaffected | Consistent |

Ruling 1: The policy-comparison harness (Task 2) will keep calling `decide()` without the Task 3 gate kwargs, so it measures routing-and-timing strategy with the gates off. — Why: the comparison's question is "does cause-aware routing beat blind retrying", and the confidence/value gates are a compliance control that withholds actions from humans rather than a recovery strategy; folding them in would depress the agent's number for a reason unrelated to what is being compared. — Cost if wrong: the published lift figure overstates what the fully-gated live pipeline recovers; the fix is passing two extra kwargs in `_run_agent_policy` and re-running.

Ruling 2: Execute in plan order 1 → 2 → 3. — Why: Task 3 consumes a column Task 1 creates; Task 2 is independent and its position is free. — Cost if wrong: none identified.

Ruling 3: The controller (not the implementer) runs `log/migrations/002_add_diagnosis_confidence.sql` against Supabase, since DDL needs the SQL Editor and the implementer has no live-run responsibility. — Why: implementers are sandboxed to code + offline tests; live schema changes are the controller's job. — Cost if wrong: a live `run_diagnosis.py` fails on the missing column until the migration is applied.

Ruling 4: The plan's `GROQ_MODEL = "llama-3.3-70b-versatile"` is a plan defect — that model returns 404 on this Groq account. Replaced with `openai/gpt-oss-120b`. — Why: verified live against the account's model list; of the three chat-capable candidates, gpt-oss-120b was the only one returning clean parseable JSON (qwen/qwen3.6-27b emits `<think>` preamble, openai/gpt-oss-20b returned empty), and it correctly classified the ambiguous "Transaction declined by bank" as `card_declined_by_issuer` — the exact case the rule layer gets wrong on all 8 misclassifications. — Cost if wrong: a different model choice changes accuracy; swapping the constant is a one-line change.

Note: `GROQ_API_KEY` was absent at plan time; the controller added it to `.env` during Task 1 and verified it live (key valid, `/v1/models` and `/v1/chat/completions` both reachable). Not needed for any task's tests — all LLM calls are injected there — only for end-to-end verification after Task 3.

## Progress

Task 1: complete (commits 79802b3..44f7cec, review clean — spec ✅, quality approved, no Critical/Important findings)
- Reviewer's one "cannot verify from diff" item was the live Supabase migration; controller resolved it — user ran `002_add_diagnosis_confidence.sql`, controller verified the column exists and is NULL on pre-existing rows (consistent with Task 3's NULL→"high" handling).
- Reviewer minor (deferred, no action): fallback-construction logic appears twice in `classify_with_llm` (exception path + invalid/unparseable path), ~6 lines across two call sites. Brief specified that code verbatim. Surface to the final whole-branch review for triage.

Task 2: implemented at 486de13, returned DONE with a concern that agent_policy loses to naive_retry_all by -6.4%. Controller inspected the harness and found the concern is a defect in the comparison, not a property of the policy. Fix round 1 dispatched (resumed original implementer) with two findings:
- Critical: unequal attempt budgets. `_run_naive_retry_all` used `range(MAX_ATTEMPTS)` = 3 recovery attempts while ignoring `payment["attempt_count"]`; `_run_agent_policy` honours it and so gets 2. Naive was handed a 50% larger budget (149 vs 114 attempts). Comparison measured budget, not strategy.
- Important: `_run_agent_policy` routed on ground-truth `root_cause` rather than a predicted cause, crediting the agent with diagnoses it would not always have in production. Directed to route on rules-only `classify()` (pure/offline/deterministic; LLM path excluded so the harness understates rather than overstates the agent).
- Also corrected the implementer's stated root cause: the 72h stopping window never fires in this harness (`now` starts at `created_at`, age grows ~6h per iteration over ≤2 iterations).
- Explicitly instructed: do not tune for the agent to win; report whatever number falls out of a fair comparison.

Task 2: fix round 1/5 (2 addressed, 0 open; commits 486de13..efa414d). Both findings confirmed and fixed by the implementer, which also independently verified the controller's correction that the 72h window never fires in this harness (0/75 exhaust by age; max simulated age ~12h) and struck its own wrong diagnosis from the report. Corrected result: do_nothing 0, naive_retry_all 47 / Rs 651,382.95 / 120 attempts, agent_policy 51 / Rs 731,772.81 / 114 attempts — **agent +12.3%**.

Controller verification of the suspicious "agent numbers unchanged after the routing change": genuine, not a no-op fix. The rules classifier disagrees with ground truth on 8/75 records, but 0 of those disagreements change the resulting action, because `card_declined_by_issuer` and `auth_failure` both map to `send_payment_link` at the same delay. Worth surfacing in the pitch: the routing policy is structurally robust to the classifier's only real confusion.

Task 2: fix round 2/5 (2 addressed, 0 open; commits efa414d..a861892). Review (full, adversarial) found a Critical the controller had missed: per-policy `random.Random(seed)` streams diverge once the policies' per-payment draw counts differ, leaving 29% of payments unpaired at seed=42. Directed fix: per-payment RNG seeded `f"{seed}:{payment_id}"`. Controller caught a trap in the reviewer's own suggested fix (`hash((seed, payment_id))` would break cross-process reproducibility via per-process string-hash salting) and specified a string seed instead. Also directed multi-seed reporting to replace the single-seed point estimate.

Task 2: complete (commits 44f7cec..a861892, re-review clean — both findings ADDRESSED, no new breakage, no deferred items).
- Final numbers: seed 42 — naive 37 / Rs 499,549.47 / 131 attempts; agent 46 / Rs 622,120.65 / 118 attempts (+24.5%). **Headline: 20-seed median +16.0%, range +3.4% to +27.6%, agent wins 20/20.**
- Two independent corroborations that the harness now measures what it claims: (1) the implementer found the pre-fix harness was not order-invariant (reversing the batch changed the result) — an objective symptom of the pairing bug; (2) the controller derived expected lift from the assumed success rates alone as +14.3%, consistent with the observed +16.0% median.
- Secondary pitch point: the agent recovers more while making FEWER attempts (118 vs 131).

Task 3: complete (commits a861892..45dc355, review clean — spec ✅, quality approved, no Critical/Important findings). Reviewer independently traced gate ordering against the code rather than trusting the tests, confirmed the 8 pre-existing `test_policy.py` tests are byte-identical (purely additive diff), and confirmed the `run_decisions.py` event mapping is total (any future unmapped status raises `KeyError` loudly rather than mislabelling silently). 50/50 tests pass.
- Task 3: minor (deferred): no test at exactly `amount_inr == 20000.0`, the money boundary itself. Tests cover 19999.0 and 25000.0. Code uses `>=` which is correct per the brief; the brief's own test block omitted the exact-threshold case. Cheap to add.
- Task 3: minor (deferred): no combination test for 72h-exhausted + low-confidence + high-value. The attempt-exhaustion equivalent is covered and the code path is identical (same early return before either gate).

Controller note for the pitch, measured on the committed batch: 17/75 payments (Rs 3,80,957.32) sit at or above the Rs 20,000 approval gate and will now route to `needs_approval` instead of auto-executing. The live recovered figure will therefore come out BELOW the pre-branch Rs 1,80,960.47. This is the gate working, not a regression — the story becomes two numbers (recovered automatically vs deliberately held for human sign-off) rather than one.

Final whole-branch review (Opus): **NOT MERGEABLE as-is.** 1 Critical, 4 Important, 5 Minor. Fix wave dispatched at 45dc355.

Ruling 1 OVERTURNED by the final review, and the controller accepts the correction. The ruling's analysis (gates are a compliance control, not a recovery strategy) stands, but its labelling was indefensible and its stated cost-if-wrong was materially understated: the review measured the shipped, gated agent at **-41.0% median vs naive, losing 20/20 seeds**, against the published +16.0%. A judge re-running with the gate kwargs watches the headline invert. The ruling also claimed remediation was "passing two extra kwargs" — verified false: `_run_agent_policy` only breaks on `"exhausted"`, so a `needs_approval` decision reaches `ASSUMED_SUCCESS_RATES[None]` and raises `KeyError`. Superseded by Ruling 5.

Ruling 5: publish BOTH policies under honest names — `agent_routing_ungated` (the routing-strategy comparison, +16%) and `agent_gated` (what actually ships, reporting auto-recovered vs held-for-human separately). — Why: the two-number story is both honest and a stronger pitch than a lift figure that inverts under inspection; restraint on high-value and low-confidence payments is the "bounded and gated" evidence the track asks for. — Cost if wrong: none identified; this strictly adds information.

Cross-task interaction found only by the whole-branch view: Task 1 defeats Task 3's confidence gate. `llm_classifier.py` hardcodes `confidence="high"` for any parseable response, so after Task 1 the gate can fire only on an LLM outage, never on genuine ambiguity — the exact population it was built for. Fix: ask the model for its own confidence.

Final-review fix wave: commits 45dc355..66c768f (6e3977f naming + gated agent, 0a91bf5 LLM self-reported confidence, 1a72f84 held metrics + approve.py, 66c768f README). The fix agent was killed mid-task by a session limit while finishing the README; controller verified on resume that all 5 findings and every triaged minor had already landed. Tests 50 → 70, all passing via the README's own `python -m pytest`. Two new test files appeared (`decide/test_run_decisions.py`, `log/test_db.py`). Not written: `final-fix-report.md` (agent died before it) — the work is committed and controller-verified instead.

Four-policy output now self-documents the ungated caveat in the printed text, so a reader learns it from the output rather than by discovering it in the source. seed=42: naive 37 / Rs 499,549.47; agent_routing_ungated 46 / Rs 622,120.65 (+24.5%, 20-seed median +16.0%); agent_gated 26 recovered / Rs 320,872.04 automatically plus 29 held / Rs 485,826.81 for human sign-off.

## Known follow-up after the branch lands (not part of any task)

- `README.md` hardcodes numbers this branch invalidates: the "89.3% accuracy" claim (LLM escalation changes it), the live-example metrics table (14 recovered / Rs 1,80,960.47 / 18.7%), and the "no AI" framing in *What this doesn't do*. All need refreshing from a clean end-to-end run before submission.
- Supabase currently holds a mid-flight batch from pre-branch runs (14 recovered, 51 exhausted, `diagnosis_confidence` NULL on every row). A clean end-to-end verification should reset to the committed snapshot and re-run the whole pipeline so the published numbers come from one coherent pass.
- Status vocabulary gains `needs_review` / `needs_approval` in Task 3; README's pipeline description does not mention either gate.


## E2E defect fix wave (2026-08-21) — commits 66c768f..3dfbde8

Five findings from `e2e-verification-report.md` addressed TDD-first. Tests 70 -> 96, all passing; no existing test weakened or edited (both touched test files are purely additive). Full evidence in `e2e-fix-report.md`.

- `ed1d409` **A-3 (CRITICAL) fixed** — human approval is now durable. New column `human_approved_at` (migration `003_add_human_approval.sql`, folded into `supabase_schema.sql`), stamped by `approve.py` in the same update as the status, honoured by `decide(human_approved=...)`. Both gates stand down for an approved payment; **both stopping rules still apply and still come first** — approval authorises acting on a payment, it does not resurrect one out of attempts or past 72h. The reason string says a human authorised it.
- `52cd300` **A-1 (CRITICAL) fixed** — `run_actions.py` no longer dies on a non-`BadRequestError`. Notable: **`razorpay.errors.RazorpayError` does not exist** — all four SDK error classes subclass `Exception` directly, so the briefed base-class catch would have been an `AttributeError`. Catch tuple is built from `vars(razorpay.errors)` plus `requests.exceptions.RequestException`. On-catch behaviour and the `action_error` detail shape are unchanged.
- `101e3c5` **D-1 (HIGH) fixed** — `ingest/load_batch.py --rebase-timestamps` shifts the batch by one constant offset (newest record -> now), preserving relative spacing exactly, in memory only. `data/failed_payments.json` untouched. Offset printed to stdout.
- `524e76c` **L-1 addressed, effect UNVERIFIED** — `PROMPT_TEMPLATE` now states the low-confidence rule as an obligation, closes the "I named a best guess so I'm confident" loophole, and works `"Transaction declined by bank"` (consistent with both `card_declined_by_issuer` and `auth_failure`) through as an in-prompt example. Parsing contract and all fallback paths unchanged. Whether the model actually obeys must be re-measured live.
- `3dfbde8` **A-2 (HIGH) documented** — README setup step 3 now carries the lifetime 30-payment-link cap, what the failure looks like, and that it understates rather than invalidates a run. No other README edit; the live-metrics table is untouched pending a re-run.

**Blocking before the next live run: apply `log/migrations/003_add_human_approval.sql` to Supabase.** Everything in A-3 is inert until it is. `run_decisions.py` degrades safely without it (missing column -> `human_approved=False` -> today's behaviour), so nothing breaks — but no approval takes effect.

Follow-ups for the README beyond the verification report's list: document `--rebase-timestamps` (an undocumented flag will not be used by a judge); setup step 2 must now say three migrations; the line-33 "held payments are not a dead end" and "safe to re-run anytime" claims can now stand, and line 33 should add that approval overrides the gates but not the stopping rules.

Expect the next live run's shape to change: with `--rebase-timestamps` far fewer payments exhaust on age, so held count / held amount rise sharply alongside recovered. That is the gate working on a fuller population, not a regression.
