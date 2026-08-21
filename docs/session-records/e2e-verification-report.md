# reclaim-agent — Live End-to-End Verification Report

**Date:** 2026-08-20 (UTC) / 2026-08-21 (IST)
**Branch:** `tier1-improvements` (HEAD `66c768f`, working tree clean except untracked `docs/`)
**Environment:** Windows 11, Python 3.14, live Supabase + live Razorpay **test mode** + live Groq
**Scope:** first-ever live run of the branch's three additions — LLM escalation, offline policy harness, confidence + value gates.

---

## Verdict summary

| Item | Result |
|---|---|
| Clean reset + reload of committed snapshot | PASS |
| Pipeline runs to a stable state | **FAIL — blocked by an uncaught Razorpay `ServerError`** |
| LLM escalation layer engaged live | **PASS** — 15/75 diagnoses, `method: "llm"`, real reasoning |
| Value gate (₹20,000) fired | **PASS** — 4 payments held, `held_for_approval` with reason |
| Confidence gate fired | **NOT DEMONSTRATED** — 0 `needs_review`; only 1 low-confidence diagnosis and it was exhausted by the stopping rule first |
| Audit trail supports explainability claim | **MOSTLY PASS** — strong, with two named gaps |
| `decide/approve.py` works | **PARTIAL — `--list` and `--approve` work, but the release is a no-op loop** |
| Offline harness (`analysis/compare_policies.py`) | PASS |
| Test suite | PASS — 70/70 |
| README accurate | **FAIL** — 5 numeric/procedural contradictions |

---

## 1. Reset to a clean, coherent starting state

### 1.1 Pre-reset state (evidence of the mid-flight batch described in the brief)

```
failed_payments rows: 75
status: {'action_taken': 10, 'recovered': 14, 'exhausted': 51}
confidence: {'None': 75}
audit_log rows: 330
events: {'diagnosed': 75, 'decided': 45, 'exhausted': 51, 'ingested': 75,
         'action_executed': 35, 'action_failed': 21, 'recovered': 14, 'action_error': 14}
```

Confirms the brief: `diagnosis_confidence` NULL on every row, and this is exactly the state the README's "Live example" table was written from (14 recovered / 51 exhausted / 10 in progress). Note `action_error: 14` — Razorpay rate limiting has been hit before, as warned.

### 1.2 Delete

Executed against Supabase (`audit_log` first, FK order):

```python
c.table("audit_log").delete().neq("id", -1).execute()
c.table("failed_payments").delete().neq("payment_id", "__none__").execute()
```

```
audit_log remaining: 0
failed_payments remaining: 0
```

### 1.3 Reload the committed snapshot

`data/failed_payments.json` was **not** regenerated. Verified untouched (`git status` clean for `data/`).

```
$ cd ingest && python load_batch.py
Loaded 75 payments into Supabase and logged 'ingested' event for each.
```

Post-load verification:

```
failed_payments rows: 75
status: {'needs_diagnosis': 75}
confidence: {'None': 75}
audit_log rows: 75
events: {'ingested': 75}
```

**75 rows in each table confirmed.** Clean, coherent starting state achieved.

### 1.4 Properties of the committed snapshot (measured, for cross-checks)

```
records: 75
>=₹20,000: 17          sum ₹380,957.32
total at risk: ₹1,040,061.78
root causes: insufficient_funds 24, auth_failure 17, card_declined_by_issuer 14,
             network_timeout 13, expired_card 7
created_at range: 2026-08-14T00:30:12 .. 2026-08-20T14:25:12
age at run time: 7.1h .. 165.1h
rows older than the 72h stopping window: 45
all statuses: needs_diagnosis; all attempt_counts: 1
```

The 17-payment / ₹3,80,957.32 value-gate cross-check in the brief is **confirmed exactly**.

**But note the third-from-last line: 45 of 75 records were already past the 72h stopping window before the pipeline ever ran.** This is the root cause of most of the divergence below and is discussed as Finding D-1.

---

## 2. Running the pipeline to completion

### 2.1 Run 1 — full pipeline

```
$ python run_pipeline.py

=== diagnose/run_diagnosis.py ===
Diagnosed 75 payments and logged 'diagnosed' event for each.

Population: the 75 payment(s) currently in 'diagnosed' status
Accuracy vs. synthetic ground truth: 70/75 (93.3%)

Diagnosis method breakdown (same 75 payment(s)):
  keyword          60
  llm              15

Misclassifications (actual -> predicted), same 75 payment(s):
   5x  auth_failure                 -> card_declined_by_issuer

=== decide/run_decisions.py ===
Decided on 75 payments.
  action_taken   25
  exhausted      46
  needs_approval 4

=== act/run_actions.py ===
Executed 17 eligible actions.
  recovered:             4
  failed, retry pending: 7
  api error, skipped:    6
```

Observations from run 1:
- LLM layer engaged on exactly the 15 payments the offline classifier escalates. End-to-end accuracy **93.3%**, above the keyword layer's standalone **89.3%** — the escalation measurably earns its keep.
- `exhausted 46` on the *first* decision pass: 45 rows past the 72h window plus 1 more that aged past it during the run.
- `api error, skipped: 6` — six `action_error` events, all `{'error': 'Too many requests', 'action': 'send_payment_link'}`. **The per-row catch worked as designed**: the batch continued, statuses were left untouched for retry, and the errors were logged. This is the graceful-failure feature working, and is recorded here as evidence.

### 2.2 Run 2 — CRASH (blocking)

```
$ python run_pipeline.py

=== diagnose/run_diagnosis.py ===
Diagnosed 0 payments ...
=== decide/run_decisions.py ===
Decided on 7 payments.
  action_taken   7

=== act/run_actions.py ===
Traceback (most recent call last):
  File "D:\RazorPay\reclaim-agent\act\run_actions.py", line 97, in main
    total, results = run_eligible_actions(supabase, razorpay_client)
  File "D:\RazorPay\reclaim-agent\act\run_actions.py", line 63, in run_eligible_actions
    artifact = create_recovery_payment_link(razorpay_client, payment_id, amount, LINK_REASONS[action])
  File "D:\RazorPay\reclaim-agent\act\razorpay_client.py", line 40, in create_recovery_payment_link
    link = client.payment_link.create({...})
  ...
razorpay.errors.ServerError: test mode limit of 30 reached for payment_link
!!! act/run_actions.py exited with code 1 -- stopping pipeline
```

Re-run of `act/run_actions.py` on its own reproduced the identical crash, this time on the **very first row**, processing nothing:

```
$ cd act && python run_actions.py
razorpay.errors.ServerError: test mode limit of 30 reached for payment_link
```

**The act layer is now permanently blocked.** See Finding A-1 (code bug) and Finding A-2 (environment).

Per the standing instruction not to modify application code and not to work around a blocking crash, no fix or workaround was applied. In particular I deliberately did **not** push `next_action_at` forward on the payment-link rows to let the retry rows through — that would have manufactured the "one clean sweep" numbers rather than measured them.

### 2.3 Terminal state reached

```
status: {'exhausted': 47, 'needs_approval': 4, 'action_taken': 20, 'recovered': 4}
eligible-but-blocked:                  12  {'send_payment_link': 9, 'retry_payment': 2, 'prompt_card_update': 1}
correctly deferred (6h retry delay):    8  {'retry_payment': 8}
```

So of the 20 rows reported as `still_in_progress`:
- **8 are legitimately deferred** by the real 6-hour `insufficient_funds` retry delay. This is correct behaviour, exactly as the brief anticipated, and they were not waited on.
- **12 are stuck**, not deferred. Their `next_action_at` has passed; they cannot be actioned because the act layer crashes on the first payment-link row before reaching any of them. Two of them are `retry_payment` rows that would otherwise have succeeded.

The batch therefore **did not reach a stable state**, and could not, without a code change.

---

## 3. LLM layer verification

### 3.1 Method breakdown from `audit_log`

```
method counts:        {'keyword': 60, 'llm': 15}
method x confidence:  {('keyword','high'): 60, ('llm','high'): 14, ('llm','low'): 1}
```

**Zero `code_fallback` events.** Groq was reachable for all 15 escalations; the degrade path was never exercised (which is itself worth noting — the fallback is unit-tested but not live-tested).

### 3.2 Every live LLM diagnosis, with its real reasoning text

| payment_id | root cause | conf | reasoning |
|---|---|---|---|
| pay_0b5927267efd44 | card_declined_by_issuer | high | The message explicitly says the bank declined the transaction, indicating an issuer refusal. |
| pay_6d2b44f180c549 | card_declined_by_issuer | high | The message explicitly says the bank declined the transaction, indicating an issuer refusal. |
| pay_962c9894f7b048 | card_declined_by_issuer | high | The message explicitly says the bank declined the transaction, indicating an issuer refusal. |
| pay_ff2cd4b59d1445 | card_declined_by_issuer | high | The message explicitly says the bank declined the transaction, indicating an issuer refusal. |
| pay_cb97006de64849 | card_declined_by_issuer | high | The error explicitly states the transaction was declined by the bank, indicating an issuer refusal. |
| pay_8eed1533f42a47 | card_declined_by_issuer | **low** | The bank declined the transaction, but the specific reason (e.g., insufficient funds) is not stated. |
| pay_8c65cf14d02d48 | auth_failure | high | The error explicitly mentions OTP/3DS authentication failure or timeout. |
| pay_392e9b643c7a4d | card_declined_by_issuer | high | The message explicitly says the bank declined the transaction, indicating an issuer refusal. |
| pay_911fe79844454c | card_declined_by_issuer | high | The message explicitly says the bank declined the transaction, indicating an issuer refusal. |
| pay_5d0279b14ca44f | card_declined_by_issuer | high | The message explicitly says the bank declined the transaction. |
| pay_f8583ea6414445 | auth_failure | high | The error explicitly mentions OTP/3DS authentication failure or timeout, indicating an auth issue. |
| pay_8c0d05a1f35240 | card_declined_by_issuer | high | The message explicitly says the bank declined the transaction, indicating an issuer refusal. |
| pay_32605dba9a504b | card_declined_by_issuer | high | The message explicitly states the bank declined the transaction, indicating an issuer refusal. |
| pay_5dc101f4df324e | card_declined_by_issuer | high | The message explicitly says the bank declined the transaction, indicating an issuer refusal. |
| pay_6a540f9b7b1944 | card_declined_by_issuer | high | The message states the bank declined the transaction, indicating issuer refusal. |

**Result: the LLM demonstrably engaged.** Real `method: "llm"`, real per-payment reasoning, real confidence values.

### 3.3 Low-confidence count — and an honesty finding

**1 of 15** LLM diagnoses came back `low` (6.7%). The other 14 came back `high`.

This is a problem for the confidence-gate story, and it should be stated plainly in the submission rather than glossed:

- All 15 escalations were on the deliberately ambiguous string **"Transaction declined by bank"** (plus two OTP variants).
- **5 of the 15 were wrong** — actual `auth_failure`, predicted `card_declined_by_issuer`.
- **All 5 wrong answers were returned with `confidence: "high"`.**

The model is systematically overconfident on precisely the phrase the escalation layer exists to handle. The prompt asks for honest low confidence and the model largely does not give it. The confidence gate therefore catches far less than the design implies: it caught 1 ambiguous payment and let 5 wrong high-confidence diagnoses through to the decision layer. This does not mean the gate is broken — the plumbing works — but the README should not imply the gate reliably protects against LLM error. It protects against *self-declared* uncertainty, which this model rarely declares.

(The 5 misclassifications did not move money in this run only because all 5 were separately caught by the 72h stopping rule or the value gate.)

---

## 4. Gate verification

### 4.1 Value gate — FIRED

4 `held_for_approval` events, each carrying an explicit numeric reason:

```
held_for_approval pay_962c9894f7b048 {"action": null, "reason": "amount Rs 22,263.88 is at or above the Rs 20,000 auto-action threshold", "confidence": "high", "next_action_at": null, "confidence_defaulted": false}
held_for_approval pay_ed32cd06537c4d {"action": null, "reason": "amount Rs 20,766.83 is at or above the Rs 20,000 auto-action threshold", "confidence": "high", "next_action_at": null, "confidence_defaulted": false}
held_for_approval pay_3eeb7c5812b24a {"action": null, "reason": "amount Rs 24,634.97 is at or above the Rs 20,000 auto-action threshold", "confidence": "high", "next_action_at": null, "confidence_defaulted": false}
held_for_approval pay_c23f4a1ac1d849 {"action": null, "reason": "amount Rs 20,436.38 is at or above the Rs 20,000 auto-action threshold", "confidence": "high", "next_action_at": null, "confidence_defaulted": false}
```

`confidence_defaulted: false` on every one — the branch's new column is populated and the audit detail distinguishes a diagnosed confidence from an assumed one, as designed.

### 4.2 Value gate cross-check against the data — DISCREPANCY

```
high-value rows (>=₹20,000): 17   sum ₹380,957.32     <- matches the brief exactly
their statuses:  {'exhausted': 13, 'needs_approval': 4}
held sum: ₹88,102.06
```

The 17 records exist as claimed, but **only 4 reached the value gate.** The other 13 were exhausted first by the 72h stopping rule, which `decide()` applies before the gates by design:

```
pay_dbd30814d49047  exhausted  23343.83  age 103.2h  attempts 1
pay_3d5837fb053e4a  exhausted  21237.74  age 103.0h  attempts 1
pay_8c65cf14d02d48  exhausted  22638.99  age 156.5h  attempts 1
pay_9ee33a063a1747  exhausted  23759.98  age 114.4h  attempts 1
pay_8eed1533f42a47  exhausted  21564.07  age  76.8h  attempts 1
pay_3bf4f2842abf4f  exhausted  23578.99  age  86.1h  attempts 1
pay_685671d3ef6746  exhausted  20579.70  age 144.3h  attempts 1
pay_0e754cc951044e  exhausted  21216.86  age  74.4h  attempts 1
pay_1576005d6cc747  exhausted  23845.79  age 148.6h  attempts 1
pay_0cdb0bfa131b43  exhausted  24878.70  age 128.5h  attempts 1
pay_1d0cb27c86b544  exhausted  21568.48  age 129.9h  attempts 1
pay_aa62694daa2349  exhausted  21642.33  age  86.3h  attempts 1
pay_29b5df8d62aa4f  exhausted  22999.80  age 110.6h  attempts 1
```

The ordering is intentional and documented in `decide/policy.py`, and it is the right ordering (a finished payment should not land in a human queue). **The bug is in the README's claim, not the code:** README line 31 states "On the committed batch that's 17 payments, ₹3,80,957.32 deliberately withheld from automation." Live, it is **4 payments, ₹88,102.06**, and that figure will keep shrinking as the committed snapshot ages. See Finding D-1.

### 4.3 Confidence gate — NOT DEMONSTRATED LIVE

```
gate events: {'held_for_approval': 4}     # held_for_review: 0
needs_review count: 0
low-confidence payments in batch: 1
  pay_8eed1533f42a47  status=exhausted  ₹21,564.07  card_declined_by_issuer
```

The single low-confidence diagnosis the whole batch produced (`pay_8eed1533f42a47`) was 76.8h old, so the stopping rule exhausted it before the confidence gate could see it. **No payment reached `needs_review` and no `held_for_review` event was ever written.**

The gate is unit-tested and its code path is a two-line branch immediately above the value gate that demonstrably fires, so I do not believe it is broken. But it has **never executed live**, and the submission should not claim it has. Two independent causes conspired: the model rarely says "low", and the aged snapshot exhausts most candidates first.

---

## 5. Audit trail / explainability verification

Three payments with three different final outcomes. Every `audit_log` row for each `payment_id` is reproduced in full.

### 5.1 RECOVERED — `pay_6d2b44f180c549` (₹7,990.76)

Row state:
```json
{
  "payment_id": "pay_6d2b44f180c549", "order_id": "order_e7b09907f55b44",
  "amount_inr": 7990.76, "currency": "INR",
  "error_code": "GATEWAY_ERROR", "error_reason": "Transaction declined by bank",
  "root_cause": "card_declined_by_issuer", "predicted_root_cause": "card_declined_by_issuer",
  "created_at": "2026-08-19T00:51:12.118082+00:00", "attempt_count": 1,
  "status": "recovered", "action_taken": "send_payment_link",
  "recovered_amount_inr": 7990.76, "diagnosis_confidence": "high"
}
```

Full audit trail (5 events):
```
[2026-08-20T21:34:20] ingested         {"source": "synthetic_batch", "error_code": "GATEWAY_ERROR"}
[2026-08-20T21:34:47] diagnosed        {"method": "llm", "reasoning": "The message explicitly says the bank declined the transaction, indicating an issuer refusal.", "confidence": "high", "predicted_root_cause": "card_declined_by_issuer"}
[2026-08-20T21:35:32] decided          {"action": "send_payment_link", "reason": "card_declined_by_issuer -> send_payment_link", "confidence": "high", "next_action_at": "2026-08-20T21:36:01.451510+00:00", "confidence_defaulted": false}
[2026-08-20T21:35:58] action_executed  {"action": "send_payment_link", "short_url": "https://rzp.io/rzp/dT8p9CSX", "artifact_id": "plink_TSAsjmGlEAFllm", "artifact_type": "payment_link"}
[2026-08-20T21:36:00] recovered        {"simulated": true, "assumed_rate": 0.5}
```

**Reconstructed story:** A ₹7,990.76 payment failed with `GATEWAY_ERROR`. The keyword rules could not resolve the reason, so it escalated to the LLM, which read it as an issuer refusal with high confidence and said why. The deterministic policy table mapped `card_declined_by_issuer -> send_payment_link` with zero delay. A **real Razorpay test-mode payment link** was created — `plink_TSAsjmGlEAFllm`, `https://rzp.io/rzp/dT8p9CSX` — and is inspectable in the Razorpay dashboard. The customer-completion step was then **simulated at the stated 50% rate and flagged `simulated: true`**. It came up successful, so the payment was marked recovered for the full ₹7,990.76.

**Can an auditor answer "why here, decided on what?" from the log alone? Yes**, with one caveat: the log never records the `error_reason` string the diagnosis was made from, and the `recovered` event never records the rupee amount. Both are recoverable from the `failed_payments` row but not from `audit_log` in isolation. See Finding E-1.

### 5.2 HELD FOR A HUMAN — `pay_962c9894f7b048` (₹22,263.88)

Row state:
```json
{
  "payment_id": "pay_962c9894f7b048", "amount_inr": 22263.88,
  "error_code": "GATEWAY_ERROR", "error_reason": "Transaction declined by bank",
  "root_cause": "auth_failure", "predicted_root_cause": "card_declined_by_issuer",
  "created_at": "2026-08-19T14:29:12.118082+00:00", "attempt_count": 1,
  "status": "needs_approval", "action_taken": null,
  "recovered_amount_inr": null, "diagnosis_confidence": "high"
}
```

Full audit trail (3 events):
```
[2026-08-20T21:34:20] ingested           {"source": "synthetic_batch", "error_code": "GATEWAY_ERROR"}
[2026-08-20T21:34:49] diagnosed          {"method": "llm", "reasoning": "The message explicitly says the bank declined the transaction, indicating an issuer refusal.", "confidence": "high", "predicted_root_cause": "card_declined_by_issuer"}
[2026-08-20T21:35:33] held_for_approval  {"action": null, "reason": "amount Rs 22,263.88 is at or above the Rs 20,000 auto-action threshold", "confidence": "high", "next_action_at": null, "confidence_defaulted": false}
```

**Reconstructed story:** Same ambiguous reason text, same LLM escalation, same high-confidence issuer-refusal call — and here the LLM was **wrong**: ground truth is `auth_failure`. The payment never moved anyway, because at ₹22,263.88 it is above the ₹20,000 threshold and the value gate held it for a human with an explicit numeric reason. No money moved on a wrong diagnosis. `action_taken` is null and `next_action_at` is null, so no artifact was ever created.

This is the single best illustration of the submission's thesis: the value gate caught a mistake the confidence gate did not.

**Can an auditor answer the question from the log alone? Yes**, and this trail is exemplary — the exact threshold, the exact amount, and the fact that confidence was diagnosed rather than defaulted are all on the record.

### 5.3 EXHAUSTED — `pay_8eed1533f42a47` (₹21,564.07)

Row state:
```json
{
  "payment_id": "pay_8eed1533f42a47", "amount_inr": 21564.07,
  "error_code": "GATEWAY_ERROR", "error_reason": "Transaction declined by bank",
  "root_cause": "card_declined_by_issuer", "predicted_root_cause": "card_declined_by_issuer",
  "created_at": "2026-08-17T16:49:12.118082+00:00", "attempt_count": 1,
  "status": "exhausted", "action_taken": null,
  "recovered_amount_inr": null, "diagnosis_confidence": "low"
}
```

Full audit trail (3 events):
```
[2026-08-20T21:34:25] ingested   {"source": "synthetic_batch", "error_code": "GATEWAY_ERROR"}
[2026-08-20T21:35:01] diagnosed  {"method": "llm", "reasoning": "The bank declined the transaction, but the specific reason (e.g., insufficient funds) is not stated.", "confidence": "low", "predicted_root_cause": "card_declined_by_issuer"}
[2026-08-20T21:35:41] exhausted  {"action": null, "reason": "older than 72h stopping window (76.8h old)", "confidence": "low", "next_action_at": null, "confidence_defaulted": false}
```

**Reconstructed story:** The only payment in the batch on which the model admitted uncertainty, and it said so in plain language. It never reached the confidence gate: the stopping rule fired first, because at 76.8 hours the payment was past the 72-hour chase window. The agent stopped, recorded the precise age that triggered the stop, and took no action. The `confidence: "low"` is carried into the `exhausted` event, so an auditor can see the diagnosis was shaky *and* that it did not matter to the outcome.

**Can an auditor answer the question from the log alone? Yes** — and notably the log is honest about the fact that a *different* control (age, not confidence) is what stopped this payment.

### 5.4 Honest assessment of the explainability claim

**The claim holds.** For all three outcome classes, `audit_log` alone answers "why did this payment end up here, and what was it decided on?":
- the diagnosis, which layer produced it (`keyword` vs `llm`), the model's own words, and its confidence;
- whether that confidence was diagnosed or defaulted (`confidence_defaulted`);
- the decision, the exact rule that fired, and its numeric threshold or age;
- the real Razorpay artifact id and URL for any money action;
- an explicit `simulated: true` with the assumed rate on every outcome.

Nothing is hidden and no outcome is asserted without a reason string. I tested this adversarially and could not find an event that leaves the reader guessing.

**Two real gaps** (Finding E-1, both minor and both fixable without touching the pipeline's logic):
1. The `diagnosed` event records the model's conclusion and reasoning but **not the `error_reason` input text** it read. An auditor reading `audit_log` in isolation cannot see the evidence the diagnosis was made from — they must join to `failed_payments`.
2. The `recovered` event records `simulated` and `assumed_rate` but **not the amount recovered**. The headline money number is reconstructable only from `failed_payments.recovered_amount_inr`, not from the audit trail.

---

## 6. Human-approval path (`decide/approve.py`) — CRITICAL DEFECT

### 6.1 `--list` — works

```
$ cd decide && python approve.py --list
4 payment(s) held, Rs 88,102.06 awaiting a human decision:

payment_id               status                     Rs  root cause                 confidence
--------------------------------------------------------------------------------------------
pay_962c9894f7b048       needs_approval      22,263.88  card_declined_by_issuer    high
pay_ed32cd06537c4d       needs_approval      20,766.83  auth_failure               high
pay_c23f4a1ac1d849       needs_approval      20,436.38  insufficient_funds         high
pay_3eeb7c5812b24a       needs_approval      24,634.97  expired_card               high

Release one with: python approve.py --approve <payment_id>
```

### 6.2 `--approve` — works at the surface

`pay_c23f4a1ac1d849` was chosen deliberately: its cause is `insufficient_funds`, which maps to `retry_payment` (a Razorpay **Order**, not a payment link), so releasing it could not consume the exhausted payment-link quota.

```
$ python approve.py --approve pay_c23f4a1ac1d849
Released pay_c23f4a1ac1d849 (was needs_approval) back to 'diagnosed' and logged 'human_approved'. Run decide/run_decisions.py to action it.
```

Verified in Supabase:
```
AFTER APPROVE: {"payment_id": "pay_c23f4a1ac1d849", "status": "diagnosed", "action_taken": null, "next_action_at": null}
```

Audit trail after approval:
```
[2026-08-20T21:34:16] ingested           {"source": "synthetic_batch", "error_code": "BAD_REQUEST_ERROR"}
[2026-08-20T21:35:19] diagnosed          {"method": "keyword", "reasoning": null, "confidence": "high", "predicted_root_cause": "insufficient_funds"}
[2026-08-20T21:35:50] held_for_approval  {"action": null, "reason": "amount Rs 20,436.38 is at or above the Rs 20,000 auto-action threshold", "confidence": "high", "next_action_at": null, "confidence_defaulted": false}
[2026-08-20T21:39:42] human_approved     {"note": "human released this payment for automated action", "amount_inr": 20436.38, "released_from": "needs_approval", "diagnosis_confidence": "high", "predicted_root_cause": "insufficient_funds"}
```

Status returned to `diagnosed` — correct. `human_approved` event logged with `released_from`, amount, cause and confidence — correct and well-formed.

### 6.3 The next decide pass — THE RELEASE IS A NO-OP LOOP

```
$ python run_decisions.py
Decided on 2 payments.
  exhausted      1
  needs_approval 1

AFTER RE-DECIDE: {"payment_id": "pay_c23f4a1ac1d849", "status": "needs_approval", "action_taken": null, "next_action_at": null}

[2026-08-20T21:39:42] human_approved     {"note": "human released this payment for automated action", "amount_inr": 20436.38, "released_from": "needs_approval", ...}
[2026-08-20T21:39:53] held_for_approval  {"action": null, "reason": "amount Rs 20,436.38 is at or above the Rs 20,000 auto-action threshold", "confidence": "high", "next_action_at": null, "confidence_defaulted": false}
```

**The approved payment was immediately re-held.** Eleven seconds after a human authorised it, the value gate put it straight back into `needs_approval`.

**Root cause.** `decide/approve.py` sets `status="diagnosed"` and writes an audit event, but records **no durable signal that a human approved the payment**. `decide/run_decisions.py` then re-reads the row and calls `decide(...)` with `amount_inr=row["amount_inr"]` unconditionally (line 63), and `decide()` re-applies the ₹20,000 value gate (`policy.py` line 79) exactly as it did the first time. The row loops: `needs_approval -> diagnosed -> needs_approval`, forever, one cycle per `run_decisions.py` invocation, appending a duplicate `held_for_approval` event each time.

**The confidence gate has the identical defect** (by inspection, not exercised live — no payment reached `needs_review`): `run_decisions.py` line 53 reads `diagnosis_confidence` back off the row, which `approve.py` never changes, so a released low-confidence payment hits `confidence == "low"` at `policy.py` line 73 and returns to `needs_review` on the same cycle.

**Impact on the submission's central claim.** README line 33 — "Held payments are not a dead end: ... `--approve <payment_id>` releases one back into the pipeline" — and the module docstring's claim that without this script "the escalation is nominal rather than real" are **both false as shipped**. The escalation *is* nominal. A human can approve a payment and the agent will refuse to act on it. No held payment can ever be recovered.

This is the highest-priority defect found. Per instructions it was **not fixed**. A minimal fix would be a `human_approved_at` / `approved_by` column on `failed_payments` that `approve.py` sets and `run_decisions.py` checks before passing the gate kwargs — but that is a design decision for the controller.

**Side effect on the audit trail:** this test left one extra `held_for_approval` event on `pay_c23f4a1ac1d849` and one `human_approved` event in the log. The final metrics below therefore include 5 `held_for_approval` events across 4 held payments. No money moved and no status is wrong; the batch's four held payments remain four.

---

## 7. Final metrics

Collected via `get_metrics()` from `log/db.py`, exactly as the README instructs:

```python
from log.db import get_client, get_metrics
print(get_metrics(get_client()))
```

```json
{
  "total_batch_size": 75,
  "recovered_count": 4,
  "exhausted_count": 47,
  "held_for_review_count": 0,
  "held_for_approval_count": 4,
  "held_amount_inr": 88102.06,
  "still_in_progress": 20,
  "recovery_rate_pct": 5.3,
  "total_amount_at_risk_inr": 1040061.78,
  "total_amount_recovered_inr": 40428.92
}
```

| Metric | Value |
|---|---|
| Total batch size | 75 |
| Total at risk | ₹10,40,061.78 |
| **Recovered** | **4 payments, ₹40,428.92** |
| **Recovery rate** | **5.3%** |
| Exhausted | 47 |
| Held for review (confidence gate) | 0 |
| Held for approval (value gate) | 4 |
| **Held amount** | **₹88,102.06** |
| Still in progress | 20 (8 legitimately deferred + 12 blocked by the act-layer crash) |

Audit trail totals:
```
total audit_log rows: 265
events: {'ingested': 75, 'diagnosed': 75, 'decided': 32, 'exhausted': 47,
         'held_for_approval': 5, 'human_approved': 1, 'action_executed': 12,
         'action_failed': 8, 'action_error': 6, 'recovered': 4}
```

**These numbers are not a fair headline for the agent** and should not be published as-is. They are depressed by two things unrelated to the agent's strategy: 47 exhausted because the committed snapshot has aged past the 72h window (Finding D-1), and 12 payments frozen mid-flight by the act-layer crash (Finding A-1/A-2). They are reported here because they are what one clean sweep actually produced, which is what was asked for.

---

## 8. Supporting verification

### 8.1 Test suite — PASS

```
$ python -m pytest -q
......................................................................   [100%]
70 passed in 0.85s
```

70/70 as claimed. No test was modified, skipped, or weakened.

### 8.2 Offline keyword-accuracy claim — VERIFIED EXACTLY

```
keyword-layer-alone overall accuracy: 67/75 = 89.3%
escalations to LLM: 15
keyword-resolved only: 60/60 = 100.0%
```

README's "89.3% (67/75)" and "15 of 75 reasons escalate" are both **exactly correct**. The keyword layer is 100% accurate on everything it is willing to resolve and abstains on the rest — a genuinely well-behaved router, and the README undersells this.

### 8.3 Offline policy harness — PASS

```
$ cd analysis && python compare_policies.py
Batch: 75 failed payments, Rs 1,040,061.78 at risk
Simulated completion rates from act/simulate_outcome.py; seed=42 unless stated.

policy                  recovered     Rs recovered  attempts   held        Rs held
----------------------------------------------------------------------------------
do_nothing                      0             0.00         0      0           0.00
naive_retry_all                37       499,549.47       131      0           0.00
agent_routing_ungated          46       622,120.65       118      0           0.00
agent_gated                    26       320,872.04        77     29     485,826.81

Routing-only lift (gates OFF on both arms), seed=42: +24.5% recovered vs naive retry-all
Routing-only lift (gates OFF on both arms) across 20 seeds (1-20): median +16.0%, range +3.4% to +27.6%

agent_gated -- what the live pipeline does with this batch (seed=42).
  recovered automatically:  26 payments, Rs 320,872.04  (77 attempts)
  held for human sign-off:  29 payments, Rs 485,826.81  (low confidence or at/above the Rs 20,000 value gate)
```

Runs fully offline, no Supabase / Razorpay / Groq. The caveating in the output is careful and honest — `agent_routing_ungated` is clearly labelled as not-what-ships, and `agent_gated` is correctly reported as two figures.

**One thing the README should say and does not.** `compare_policies.py` line 159 sets `now = created_at`, i.e. it evaluates each payment **from the moment it failed** and lets the policy's own delays advance the clock. That is the right choice for a counterfactual, but it means the harness and the live pipeline are *not* measuring the same thing: the harness never triggers the 72h stopping rule, while the live pipeline (running against an aged snapshot) triggers it on 47 of 75. This is why the harness reports 26 recovered and the live run reports 4. Anyone comparing the two numbers will think something is broken. It should be stated.

Secondary: the harness uses rules-only `classify()` (documented in-code as a deliberate understatement), so its 29 holds include ~15 low-confidence `code_fallback` rows that the live LLM layer resolves at high confidence. The harness therefore overstates `held` relative to live — also worth a line.

### 8.4 Setup section, walked end to end

| README step | Result |
|---|---|
| 1. `pip install -r requirements.txt` | Works. All five deps import. **No version pins** — `langchain-groq 1.1.3` emits a Pydantic-V1/Python-3.14 `UserWarning` on every diagnose run (cosmetic, not fatal). |
| 2. Supabase schema + `log/migrations/` in order | Correct. `001_add_next_action_at.sql`, `002_add_diagnosis_confidence.sql` both present and both required — `diagnosis_confidence` is what this whole branch depends on. |
| 3. Razorpay test key | Works, **but see Finding A-2 — the test-mode payment-link quota is a hard cap the README never mentions.** |
| 4. Groq key | Works. Model `openai/gpt-oss-120b` responded to all 15 escalations. |
| 5. Five keys in `.env` | `.env.example` contains exactly the five documented keys. Table is accurate. The `GROQ_API_KEY`-missing paragraph is accurate by inspection but was **not** exercised live (no `code_fallback` events occurred). |
| 6. `cd ingest && python generate_synthetic_batch.py && python load_batch.py` | **Contradicts the rest of the README.** Both scripts exist and run, but running `generate_synthetic_batch.py` overwrites `data/failed_payments.json` — the committed snapshot every hardcoded number in the README (89.3%, 67/75, 15 escalations, 17 payments, ₹3,80,957.32, ₹10,40,061.78) is measured against. A reader following setup literally destroys the reproducibility the README claims. |

---

## 9. Findings, prioritised

### A-1 — CRITICAL (code): `run_actions.py` catches only `BadRequestError`; `ServerError` kills the batch

`act/run_actions.py` line 64 catches `razorpay.errors.BadRequestError` only. Razorpay raises `razorpay.errors.ServerError` for test-mode quota exhaustion (and for 5xx generally). The exception propagates out of `run_eligible_actions`, out of `main`, and `run_pipeline.py` then `sys.exit(1)`s the whole pipeline.

Observed: `razorpay.errors.ServerError: test mode limit of 30 reached for payment_link`, crashing on the first eligible row and processing nothing. 12 payments are stranded in `action_taken` with an elapsed `next_action_at` and cannot progress.

This directly contradicts the design intent stated in the code's own comment ("leave the row's status untouched so it's picked up and retried on the next run, rather than losing the batch") and the README's "Safe to re-run anytime". The narrower `BadRequestError` path **did** work — six `Too many requests` errors were caught, logged as `action_error`, and the batch continued — so the pattern is right and only the exception class is too narrow. Broadening to `razorpay.errors.RazorpayError` (or `except Exception`) would restore the intended behaviour. **Not fixed, per instructions.**

### A-2 — HIGH (environment/docs): Razorpay test mode caps payment links at 30 per account, lifetime

The cap is account-lifetime, not a rate limit, and this account has now hit it. Two consequences:
- The act layer cannot create another payment link on this account, ever. Re-running will not help.
- **Even on a brand-new test account a full sweep would hit it.** The batch contains 17 `auth_failure` + 14 `card_declined_by_issuer` (→ `send_payment_link`) + 7 `expired_card` (→ `prompt_card_update`) = up to 38 link actions before counting retries after failed attempts. 38 > 30. The current run only stayed under briefly because 47 rows were exhausted by age first.

The README says nothing about this. Anyone reproducing the metrics on a fresh key will hit the same crash. Razorpay **Orders** (used by `retry_payment`) do not appear to be capped this way — 18 exist on the account with no error.

### A-3 — CRITICAL (code): `approve.py` releases a payment that is instantly re-held — the escalation is nominal

Fully documented in §6.3 with live evidence. `approve.py` writes no durable approval signal, so `run_decisions.py` → `decide()` re-applies the same gate that held the payment and returns it to `needs_approval` on the very next pass. Confirmed live for the value gate; identical by inspection for the confidence gate. **No held payment can ever be recovered.** This falsifies README line 33 and the "escalation is real rather than nominal" claim the submission leans on. **Not fixed, per instructions.**

### D-1 — HIGH (design/docs): the committed snapshot has absolute timestamps, so every published number decays with wall-clock time

`data/failed_payments.json` carries fixed `created_at` values spanning 2026-08-14 to 2026-08-20. Against `decide()`'s 72h stopping window and a wall-clock `now`, **45 of 75 rows were already past the window before the pipeline started**, and 47 had aged out by the end of the run.

This is why every headline number moved:

| Claim | README | This run | Cause |
|---|---|---|---|
| Recovered | 14 (₹1,80,960.47) | 4 (₹40,428.92) | snapshot aged + act crash |
| Recovery rate | 18.7% | 5.3% | same |
| Exhausted | 51 | 47 | 12 frozen by the crash instead |
| Value gate holds | 17 (₹3,80,957.32) | 4 (₹88,102.06) | 13 exhausted by age first |

The snapshot cannot be regenerated (correctly — that would break the 89.3% figure), so the numbers will keep drifting downward every day until essentially everything exhausts on ingest. The README presents them as reproducible; they are not. This needs an explicit caveat at minimum, and ideally a note that the honest, time-stable measurement of the agent's strategy is `analysis/compare_policies.py`, which evaluates each payment from its own failure time.

### L-1 — MEDIUM (honesty): the model self-reports "high" on the answers it gets wrong

1 of 15 LLM escalations returned `low`. All 5 incorrect LLM diagnoses returned `high`. The confidence gate is plumbed correctly but catches self-declared uncertainty, which this model rarely declares on exactly the ambiguous phrase the layer exists for. The README's framing ("a model that genuinely can't tell two causes apart says so instead of guessing") is aspirational, not what was measured. Worth stating with the measured 1/15 rather than implied.

### G-1 — MEDIUM: the confidence gate has never fired live

0 `needs_review`, 0 `held_for_review`. Consequence of L-1 plus D-1. Unit-tested, never live-exercised. Should not be claimed as demonstrated.

### E-1 — LOW: two fields missing from the audit trail

(a) `diagnosed` events omit the `error_reason` the diagnosis was made from — the evidence is not in the log, only the conclusion. (b) `recovered` events omit `recovered_amount_inr` — the money number is not in the audit trail. Both currently require a join to `failed_payments`. Adding them would close the "audit trail alone" claim completely.

### E-2 — LOW: no live coverage of the LLM degrade path

Zero `code_fallback` events occurred, so the "any LLM failure degrades gracefully" claim remains unit-test-only. Not a defect — just not yet demonstrated live.

### R-1 — LOW: `requirements.txt` has no version pins

Reproducibility risk for a judged submission; also the source of the Pydantic-V1/Python-3.14 warning printed on every diagnose run.

---

## 10. Recommended README corrections (NOT APPLIED — for the controller)

The README was **not edited**, per instructions. Precise changes:

1. **Lines 11-18, "Live example" table.** Every figure is stale. Either replace with the measured values below *and* add the age caveat, or (better) reframe the section around `compare_policies.py`'s time-stable figures and present the live run as a demonstration of the audit trail rather than a headline recovery number.
   - Recovered: `14 payments, ₹1,80,960.47` → **`4 payments, ₹40,428.92`**
   - Recovery rate: `18.7%` → **`5.3%`**
   - Exhausted: `51` → **`47`**
   - Still in progress: `10` → **`20`** (8 deferred by the 6h retry delay, 12 blocked — see item 6)
   - Add: **`Held for a human: 4 payments, ₹88,102.06`**

2. **Add a caveat immediately under that table (new — this is the most important addition).** State that `data/failed_payments.json` is a fixed-timestamp snapshot, that the 72-hour stopping rule is evaluated against wall-clock time, and that consequently the number of `exhausted` payments grows and `recovered` shrinks the further the run is from 2026-08-20. Point readers at `analysis/compare_policies.py` for the time-independent measurement.

3. **Line 31.** `On the committed batch that's 17 payments, ₹3,80,957.32 deliberately withheld from automation` → the 17 / ₹3,80,957.32 figure is a correct property of the *data* but not of any *run*. Rephrase to: 17 of the 75 records are at or above the threshold (₹3,80,957.32 in total), and any of them still inside the 72-hour window at run time is held — 4 payments, ₹88,102.06, on the verification run.

4. **Line 33 — must change, currently false.** "Held payments are not a dead end" is contradicted by Finding A-3: an approved payment is re-held on the next decide pass. Either fix `approve.py` (add a durable approval flag that `run_decisions.py` honours) and then keep the sentence, or remove the claim. Do not ship it as written.

5. **Line 27 / the LLM section.** Add the measured self-confidence rate: 15 escalations, 1 returned `low`, and all 5 incorrect LLM diagnoses returned `high`. Soften "says so instead of guessing" to reflect that.

6. **Line 43, "Safe to re-run anytime".** False for the act layer (Finding A-1). Either fix the exception class or state that a Razorpay `ServerError` currently aborts the run.

7. **Setup step 6 (line 82).** Remove `python generate_synthetic_batch.py` from the setup command. It overwrites the committed snapshot that every hardcoded number in the README depends on. Should read `cd ingest && python load_batch.py`, with `generate_synthetic_batch.py` mentioned separately as an optional way to make a *new* batch, explicitly warning that doing so invalidates the quoted 89.3% / 17-payment figures.

8. **Setup step 3 (line 69).** Add the payment-link quota warning (Finding A-2): Razorpay test mode allows only 30 payment links per account, lifetime, and a full sweep of this batch can request up to 38. Reproducers should expect to hit it.

9. **"Comparing policies" section (~line 118).** Add one sentence explaining that the harness evaluates each payment from its own `created_at` rather than from wall-clock now, so its recovery figures will be much higher than a live run against the aged snapshot, and the two are not directly comparable. Optionally note that its `held` count is inflated because it uses rules-only `classify()` and so marks the 15 ambiguous rows low-confidence, where the live LLM layer resolves 14 of them at high confidence.

10. **Confidence gate.** Anywhere the confidence gate is described as operational, note it is unit-tested but did not fire on the verification run (0 `needs_review`).

11. **Nothing to change:** the 89.3% / 67/75 / 15-escalations figures (verified exactly), the ₹10,40,061.78 at-risk total (verified exactly), the 70-test count (verified), the simulated-completion-rate table (matches `act/simulate_outcome.py`), the `.env` key table, and the "What this doesn't do (yet)" section — which is accurate and commendably honest.

---

## 11. Constraint compliance

- **No application code was modified.** `git status` shows only the pre-existing untracked `docs/` directory plus this report.
- **No test was weakened, skipped, or edited.** 70/70 pass.
- **`data/failed_payments.json` was not regenerated.**
- **Nothing was committed.**
- **No credential values were printed.** Only presence-and-length checks were emitted.
- Supabase was mutated (authorised): rows deleted, snapshot reloaded, pipeline writes, and one `approve.py` release.
- Razorpay test-mode artifacts were created (authorised): 12 `action_executed` artifacts this sweep — 7 Orders and 5 Payment Links, all in test mode.
- **The blocking crash (A-1) was reported, not worked around.** In particular, `next_action_at` was not manipulated to route around the payment-link quota, because doing so would have fabricated the "one clean sweep" metrics rather than measured them.
