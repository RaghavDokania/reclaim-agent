# reclaim-agent

Diagnoses failed payments, decides a bounded and gated recovery action, executes it against Razorpay's test-mode API, and reports money recovered with a full audit trail — built for Razorpay's AI Buildathon (Track 3: AI Revenue Recovery).

Every payment failure is the same story told badly: a card gets declined, an OTP times out, a gateway hiccups — and most of that money is recoverable if someone (or something) diagnoses *why* it failed and takes the right next step, within limits, and can explain every action it took. That's what this agent does.

## Live example

Running against the committed synthetic batch of 75 failed payments (₹10,40,061.78 at risk):

| Metric | Value |
|---|---|
| Recovered | 14 payments, ₹1,80,960.47 |
| Recovery rate | 18.7% |
| Exhausted (correctly gated, not chased forever) | 51 |
| Still in progress (delayed retries pending) | 10 |

Numbers change as delayed retries become eligible — see [Reproducing the metrics](#reproducing-the-metrics) to regenerate them yourself, or query `get_metrics()` directly against the live Supabase project.

## Pipeline

```
ingest  ->  diagnose  ->  decide  ->  act  ->  log (throughout)
```

- **`ingest/`** — generates a synthetic batch of failed payments (5 root causes, realistic proportions, deliberately varied and sometimes-ambiguous free-text error reasons) and loads it into Supabase.
- **`diagnose/`** — a keyword-based classifier guesses the root cause from the free-text `error_reason`, falling back to a coarser `error_code`-based guess when the text is ambiguous. Runs at **89.3% accuracy** on the committed batch — a real, non-circular number (see [Why 89.3% and not 100%](#why-893-and-not-100)).
- **`decide/`** — maps each diagnosed root cause to a bounded recovery action (retry, payment link, or card-update prompt), gated by a stopping rule: max 3 attempts, and nothing older than 72 hours gets chased.
- **`act/`** — executes the decided action as a **real Razorpay test-mode API call** (an Order for retries, a Payment Link for the other two actions — both visible in the Razorpay test dashboard). Whether the customer actually completes it is the one part no backend agent can automate (see [Why some outcomes are simulated](#why-some-outcomes-are-simulated)) — that step is explicitly simulated using documented, stated success-rate assumptions, never hidden.
- **`log/`** — every stage writes to Supabase's `audit_log` table. Pull any `payment_id` and you can see every action taken on it and why, from `ingested` to its final state.

Run one full pass of all three stages:

```bash
python run_pipeline.py
```

Safe to re-run anytime — each stage only touches rows in the status it cares about, so re-running just picks up whatever's newly eligible (a delayed retry whose time has come, a payment that bounced back from a failed attempt for another decision pass, etc.).

## Why 89.3% and not 100%

The first version of the classifier scored 100% — because the same person wrote both the synthetic data's failure-reason strings and the classifier's keyword rules, so they trivially matched. That's not a real accuracy number, it's a circular one.

The fix: `ingest/generate_synthetic_batch.py` draws each failure's `error_reason` from a pool of 4-5 realistic phrasings per category, a few of which are deliberately ambiguous across categories (e.g. "Transaction declined by bank" could plausibly mean either an issuer decline or an auth failure — even a human reading that line honestly couldn't tell). The classifier's rules in `diagnose/classifier.py` were written independently from general knowledge of how banks phrase declines, not copied from the generator. Result: a genuine 89.3% (67/75), and every misclassification clusters on that one ambiguous phrase — explainable, not arbitrary.

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
4. Copy `.env.example` to `.env` and fill in `SUPABASE_URL`, `SUPABASE_KEY`, `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`.
5. `cd ingest && python generate_synthetic_batch.py && python load_batch.py`

## Reproducing the metrics

```bash
python run_pipeline.py
```

Then query the numbers directly:

```python
from log.db import get_client, get_metrics
print(get_metrics(get_client()))
```

Re-run `run_pipeline.py` periodically to let delayed retries (up to 6h for `insufficient_funds`) and failed-attempt loop-backs settle — the batch reaches a stable final state once nothing remains in `needs_diagnosis`, `diagnosed`, or an eligible `action_taken`.

## Testing

```bash
python -m pytest diagnose/test_classifier.py decide/test_policy.py act/test_simulate_outcome.py
```

23 tests covering the classifier's keyword/fallback logic, the decision policy's action mapping and stopping rules, and the simulated-outcome rates — all pure functions, no network required.

## What this doesn't do (yet)

- No live dashboard — the audit trail is queryable directly via Supabase's Table Editor or `get_metrics()`.
- Only operates on the synthetic batch — wiring to real Razorpay webhook events (rather than a generated batch) would be the natural next step past this buildathon submission.
