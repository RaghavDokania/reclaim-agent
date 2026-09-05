# reclaim-agent

Diagnoses failed payments, decides a bounded and gated recovery action, executes it against Razorpay's test-mode API, and reports money recovered with a full audit trail — built for Razorpay's AI Buildathon (Track 3: AI Revenue Recovery).

Every payment failure is the same story told badly: a card gets declined, an OTP times out, a gateway hiccups — and most of that money is recoverable if someone (or something) diagnoses *why* it failed and takes the right next step, within limits, and can explain every action it took. That's what this agent does.

## Live example

Running against the committed synthetic batch of 75 failed payments (₹10,40,061.78 at risk):

| Metric | Value |
|---|---|
| **Recovered (agent's authority)** | 4 payments, ₹59,515.77 |
| **Held for human review** | 9 payments (low confidence diagnosis) |
| **Held for human approval** | 4 payments (≥₹20,000) |
| **Held total** | ₹1,93,634.53 deliberately withheld from automation |
| Exhausted (correctly gated, not chased forever) | 42 payments |
| Still in progress (retries pending) | 16 payments |

**Why these numbers are conservative by design:** The gated agent holds high-value and low-confidence payments for human review instead of auto-executing on a guess. 5.3% auto-recovery on the agent's own authority + ₹1,93,635 held for compliance = full control at every decision point. See [Comparing policies (offline)](#comparing-policies-offline) for the counterfactual (naive retry) and routing strategy isolation.

Numbers reflect the current run's stable state — see [Reproducing the metrics](#reproducing-the-metrics) to regenerate them, or view the interactive dashboard (`dashboard.html`) for real-time audit trail and policy comparison.

## Pipeline

```
ingest  ->  diagnose  ->  decide  ->  act  ->  log (throughout)
```

- **`ingest/`** — two ways in, same row shape. `generate_synthetic_batch.py` + `load_batch.py` produce and load a reproducible batch (5 root causes, realistic proportions, deliberately varied and sometimes-ambiguous free-text error reasons). `server.py` receives real Razorpay `payment.failed` webhooks one event at a time — see [Receiving live webhooks](#receiving-live-webhooks).
- **`diagnose/`** — a two-layer router. A keyword classifier resolves unambiguous `error_reason` text deterministically and for free, scoring **89.3% on its own** (67/75 on the committed batch — a real, non-circular number, see [Why 89.3% and not 100%](#why-893-and-not-100)). The 15 of 75 reasons it *can't* resolve — text matching no category or more than one — escalate to an LLM (Groq, `openai/gpt-oss-120b`) that returns both a root cause and **its own confidence**, so a model that genuinely can't tell two causes apart says so instead of guessing. Any LLM failure degrades to the same `error_code` fallback, so a diagnosis is always produced.
- **`decide/`** — maps each diagnosed root cause to a bounded recovery action (retry, payment link, or card-update prompt), subject to three controls applied in order:
  - **stopping rules** — max 3 lifetime attempts, and nothing older than 72 hours gets chased;
  - **confidence gate** — a low-confidence diagnosis goes to human review (`needs_review`) instead of moving money on a guess;
  - **value gate** — anything at or above **₹20,000** needs human approval (`needs_approval`) instead of auto-executing. On the committed batch that's 17 payments, ₹3,80,957.32 deliberately withheld from automation.

  Held payments are not a dead end: `python decide/approve.py --list` shows the queue and `--approve <payment_id>` releases one back into the pipeline, logging a `human_approved` audit event that records a person authorised it.
- **`act/`** — executes the decided action as a **real Razorpay test-mode API call** (an Order for retries, a Payment Link for the other two actions — both visible in the Razorpay test dashboard). Whether the customer actually completes it is the one part no backend agent can automate (see [Why some outcomes are simulated](#why-some-outcomes-are-simulated)) — that step is explicitly simulated using documented, stated success-rate assumptions, never hidden.
- **`log/`** — every stage writes to Supabase's `audit_log` table. Pull any `payment_id` and you can see every action taken on it and why, from `ingested` to its final state.

Run one full pass of all three stages:

```bash
python run_pipeline.py
```

Safe to re-run anytime — each stage only touches rows in the status it cares about, so re-running just picks up whatever's newly eligible (a delayed retry whose time has come, a payment that bounced back from a failed attempt for another decision pass, etc.).

## Why 89.3% and not 100%

The first version of the classifier scored 100% — because the same person wrote both the synthetic data's failure-reason strings and the classifier's keyword rules, so they trivially matched. That's not a real accuracy number, it's a circular one.

The fix: `ingest/generate_synthetic_batch.py` draws each failure's `error_reason` from a pool of 4-5 realistic phrasings per category, a few of which are deliberately ambiguous across categories (e.g. "Transaction declined by bank" could plausibly mean either an issuer decline or an auth failure — even a human reading that line honestly couldn't tell). The classifier's rules in `diagnose/classifier.py` were written independently from general knowledge of how banks phrase declines, not copied from the generator. Result: a genuine 89.3% (67/75) for the keyword layer alone, and every misclassification clusters on that one ambiguous phrase — explainable, not arbitrary.

That 89.3% is the *keyword layer's* score, measured offline and reproducible with no API key. It is exactly the population the LLM layer exists for: the 15 reasons the rules can't resolve are where the escalation earns its keep, and the end-to-end number depends on a live model call, so it is reported from a run rather than hardcoded here.

## Why some outcomes are simulated

No backend agent can force a card to charge or a customer to click a payment link — completing a payment requires the customer in the loop, which no PCI-compliant gateway lets you automate server-side. So `act/` makes a **real** Razorpay API call to create the recovery artifact (the order or payment link genuinely exists, with a real id and, for payment links, a real URL), and then simulates whether the customer completed it using stated assumptions:

| Action | Assumed completion rate |
|---|---|
| `retry_payment` | 35% (typical automated dunning-retry recovery rate) |
| `send_payment_link` | 50% (higher — customer already intends to pay) |
| `prompt_card_update` | 45% |

Every simulated outcome is flagged `simulated: true` with the rate used, logged to `audit_log` alongside the real artifact id. Nothing here pretends to be a real customer action.

## Setup

1. `pip install -r requirements.txt`
2. Create a Supabase project, run `log/supabase_schema.sql` in its SQL Editor, then any files in `log/migrations/` in order.
3. Get a Razorpay test-mode API key (Test Mode → Settings → API Keys → Generate Test Key — no KYC needed).

   > **⚠️ Razorpay test mode caps payment links at 30 per account — for the lifetime of the account, not per run.** It is not a rate limit and waiting does not reset it. A full sweep of this batch can request up to ~38 payment links (17 `auth_failure` + 14 `card_declined_by_issuer` → `send_payment_link`, plus 7 `expired_card` → `prompt_card_update`, before any retries), so a reproducer running the batch through cleanly **will** hit the cap.
   >
   > When you do, Razorpay raises `ServerError: test mode limit of 30 reached for payment_link`. The act layer catches that **per row**: it logs an `action_error` audit event, leaves the payment's status untouched so it is retried on a later run, and carries on with the rest of the batch. The run is not wrong and no state is corrupted — the affected payments simply stay in `action_taken` instead of reaching `recovered` or `exhausted`, so the recovery figure understates what the policy would have achieved. `retry_payment` uses Razorpay **Orders**, which are not capped this way and keep working.
4. Get a Groq API key from [console.groq.com](https://console.groq.com) (free tier is enough) for the diagnosis layer's LLM escalation.
5. Copy `.env.example` to `.env` and fill in all five keys:

   | Key | Used by | Required? |
   |---|---|---|
   | `SUPABASE_URL` | `log/db.py` | Yes — nothing runs without it |
   | `SUPABASE_KEY` | `log/db.py` | Yes — nothing runs without it |
   | `RAZORPAY_KEY_ID` | `act/razorpay_client.py` | Yes, for the act layer |
   | `RAZORPAY_KEY_SECRET` | `act/razorpay_client.py` | Yes, for the act layer |
   | `GROQ_API_KEY` | `diagnose/llm_classifier.py` | Yes, for LLM escalation — see below |

   **If `GROQ_API_KEY` is missing, the pipeline does not crash — it quietly gets worse.** `_default_llm` raises `KeyError` looking the key up, `classify_with_llm` catches it like any other LLM failure, and every ambiguous payment falls back to a coarse `error_code` guess marked low-confidence. Those then hit the confidence gate and pile up in `needs_review` instead of being recovered. The run looks successful and recovers less, so set the key.
6. `cd ingest && python generate_synthetic_batch.py && python load_batch.py`

## Reproducing the metrics

```bash
python run_pipeline.py
```

Then query the numbers directly:

```python
from log.db import get_client, get_metrics
print(get_metrics(get_client()))
```

Alongside the recovery figures, `get_metrics()` reports `held_for_review_count`, `held_for_approval_count` and `held_amount_inr` — money the agent deliberately did not touch. Held payments are excluded from `still_in_progress`, because nothing is pending on them: they are waiting on a person, and `decide/approve.py --list` is where you find them.

Re-run `run_pipeline.py` periodically to let delayed retries (up to 6h for `insufficient_funds`) and failed-attempt loop-backs settle — the batch reaches a stable final state once nothing remains in `needs_diagnosis`, `diagnosed`, or an eligible `action_taken`. Held payments stay held until a human releases them.

## Comparing policies (offline)

A recovery number means nothing without a counterfactual. `analysis/compare_policies.py` replays the committed batch under four policies, entirely offline — no Supabase, no Razorpay, no API key:

```bash
cd analysis && python compare_policies.py
```

| Policy | What it is |
|---|---|
| `do_nothing` | The floor. Recovers nothing. |
| `naive_retry_all` | Retry everything, immediately, regardless of cause — held to the same lifetime attempt budget as the agent, so the comparison measures strategy and not budget. |
| `agent_routing_ungated` | **Not what the agent ships.** The agent's cause-aware routing and timing with the confidence and value gates *switched off*. It exists to isolate one question — does routing on a diagnosed cause beat retrying blindly? — with the compliance controls held constant on both arms. Any lift it reports is a lift for the routing strategy alone and is labelled as such in the output. |
| `agent_gated` | What the live pipeline actually runs: `decide()` called with the diagnosis confidence and the payment amount, so low-confidence and high-value payments are held for a human. |

**How to read it.** The routing-only lift answers "is cause-aware routing worth building?" and must always be quoted with the gates-off caveat — quoting it as the shipped agent's lift would be dishonest, since the gates deliberately withhold the batch's largest payments from automation. `agent_gated` is therefore reported as **two** figures — recovered automatically, and held for human sign-off — rather than one lift number. Money held for approval is the gate working, not money lost, and it is never counted as recovered.

Outcomes are drawn from the same assumed completion rates the act layer uses, keyed per `(seed, payment_id)` so every policy faces identical luck on a given payment. The run is byte-for-byte reproducible across processes, and the routing-only lift is reported as a median and range across 20 seeds rather than one cherry-picked draw.

## Receiving live webhooks

The batch loader is for reproducible measurement. For live traffic, `ingest/server.py` accepts Razorpay's `payment.failed` webhook:

```bash
pip install -r requirements.txt
python ingest/server.py       # listens on :5000
ngrok http 5000               # Razorpay needs a public https URL
```

Then in the Razorpay dashboard: **Settings → Webhooks → Add New Webhook**, point it at `https://<your-ngrok>.ngrok.io/webhook/razorpay`, subscribe it to `payment.failed`, and copy the secret Razorpay generates into `RAZORPAY_WEBHOOK_SECRET`.

What the endpoint guarantees:

| Behaviour | Why it matters |
|---|---|
| Verifies `X-Razorpay-Signature` against the **raw** request body, using `hmac.compare_digest` | Anyone can POST to a public URL. Verifying a re-serialized body would reject valid events; a plain `==` would leak the signature through timing. |
| Refuses to start with no `RAZORPAY_WEBHOOK_SECRET` | Fails closed. A misconfigured deployment stops, rather than quietly accepting every unsigned request. |
| A redelivered event is a no-op, answered `200 {"status": "duplicate"}` | Razorpay redelivers until it gets a 2xx. Re-inserting would reset `attempt_count` and hand a payment a fresh budget of retries it had already spent. |
| Unrelated events answered `200 {"status": "ignored"}` | A `4xx` would make Razorpay retry an event we were never going to act on, forever. |
| Amounts converted from paise; timestamps from unix epoch | Razorpay's units, not the table's. Getting either wrong silently corrupts every downstream money decision, so both are pinned by tests. |

**A live event carries no `root_cause`.** That column is the synthetic batch's ground-truth label, and the accuracy figure is measured against it. Webhook rows deliberately leave it unset rather than inventing an answer — so they flow through diagnose → decide → act normally, but they are not scored, because nobody knows the true cause of a real failure.

## Testing

```bash
python -m pytest
```

147 tests, no network required — nothing in the suite reaches Supabase, Razorpay, or Groq. They cover the classifier's keyword/fallback logic, the LLM router's confidence mapping and its degrade-to-fallback paths, the decision policy's action mapping, stopping rules and both gates (including the exact ₹20,000 boundary), the approval workflow for held payments, the audit detail written for every decision, the metrics arithmetic, the simulated-outcome rates, the policy-comparison harness's determinism and accounting, the timestamp rebasing for reproducible runs, the dashboard generator's formatting, sample selection, method counting, accuracy arithmetic and HTML escaping, and the webhook layer's payload normalization, signature verification, idempotency and HTTP status contract.

## What this doesn't do (yet)

- The webhook listener handles `payment.failed` only. Other Razorpay events are acknowledged and ignored rather than acted on.
- The listener writes the row and returns; diagnosis runs on the next `run_pipeline.py` pass rather than inline. Acting inside the request would risk Razorpay's delivery timeout, and a redelivery mid-diagnosis is harder to reason about than a redelivery that hits the idempotency check and stops.
- The LLM is a *router*, not an agent with a budget: it reads one ambiguous failure reason and returns a cause plus a confidence. It never chooses an action, never sees an amount, and never talks to Razorpay. Every money action comes from the deterministic policy table in `decide/policy.py`, which is why each one can be explained and bounded.
- The human approval queue is a CLI (`decide/approve.py`), not a UI — a real deployment would put the held queue in front of an ops team rather than behind a terminal. The CLI does write an audit trail (human_approved event), so compliance tracking is there.

## Visualization

`dashboard.html` is **generated from the live database**, not hand-written. Regenerate it any time the pipeline advances:

```bash
python dashboard/generate.py
```

It reads `get_metrics()`, the `failed_payments` rows and the `diagnosed` audit events, picks one real payment per outcome (recovered / held for review / held for approval), and renders `dashboard/template.html` into `dashboard.html`. The runtime metrics, the stream cards and the diagnosis-method distribution all come out of Supabase at generate time, so they are never stale relative to the run and the payment ids change from run to run. The policy-comparison table is the one exception: it reports the offline harness, which is deterministic by design and does not depend on the live database.

The arithmetic, the sample selection and the HTML escaping are unit-tested in `dashboard/test_generate.py` with no network access; only `main()` touches Supabase.

Open the result in a browser to see:
- The active recovery stream — one real payment per outcome, with the gate that decided it
- Runtime metrics (recovered, held for review, held for approval, exhausted, in progress)
- Policy comparison results (offline harness)
- Diagnosis method distribution (keyword vs LLM escalation)
