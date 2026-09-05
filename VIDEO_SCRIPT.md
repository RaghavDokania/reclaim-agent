# Reclaim Agent — 5-Minute Video Script

**Total Runtime:** ~5 minutes (625 words)  
**Pace:** Conversational, ~125 words per minute  
**Pauses:** 1-2 seconds between visual transitions

---

## **BEFORE RECORDING**

Regenerate the dashboard so it shows current pipeline state:

```bash
python run_pipeline.py          # optional: advance any pending retries
python dashboard/generate.py    # rewrites dashboard.html from live Supabase
```

For the webhook shot at the close, have a second terminal ready with the server up:

```bash
RAZORPAY_WEBHOOK_SECRET=whsec_demo python ingest/server.py
```

`docs/webhook-demo.sh` sends one correctly signed event, one redelivery and one forged one, so the 200, the duplicate and the 401 land back to back on camera.

**If you run `approve.py --approve` on camera, the held figures change as you watch** — the queue drops by one and the held total falls by that payment's amount. That is a good thing to show, but read the numbers off the dashboard rather than the ones written below, and regenerate before the wide shot.

The stream cards and every metric are read from the database at generate time.
Payment IDs on screen will differ from run to run, so the script never names one.

---

## **[0:00–0:30] HOOK & PROBLEM**

**Visual:** Show dashboard hero metrics (75 payments, 93.3% accuracy, ₹1.73L held)

---

When a customer's payment fails — whether it's a timeout, a declined card, or insufficient funds — most companies either give up or retry blindly.

But that money is often recoverable.

If you can diagnose *why* it failed, take the right action, stay within safe limits, and explain every decision you made... you can recover it.

This is **Reclaim Agent** — an AI system that recovers failed payments with full compliance controls.

*[Pause 2 seconds]*

---

## **[0:30–1:15] THE PROBLEM: A REAL EXAMPLE**

**Visual:** Show the first card in the Active Recovery Stream (status: Recovered)

---

Here's a real example from our live run.

A customer's payment timed out due to a network issue.

A naive system has two options: retry immediately — frustrating the customer. Or give up — losing the money.

Instead, here's what we do:

We diagnose: *network timeout*. We know that retrying in two hours has a 35% success rate. We execute a real Razorpay API call. When the customer completes it, we recover the amount shown on the card.

Every decision is logged. Every artifact is real. Nothing is a guess.

*[Pause 1 second]*

---

## **[1:15–2:30] THE SOLUTION: TWO-LAYER DIAGNOSIS**

**Visual:** Dashboard "Diagnosis Method" section (60 keyword, 15 LLM, 5 misclassifications)  
**Then:** Show classifier.py code snippet with KEYWORD_RULES

---

Most payment failures fall into five categories: insufficient funds, expired cards, auth failures, network timeouts, and issuer declines.

We built a two-layer classifier.

**Layer One: Rules.**

Eighty percent of cases are unambiguous. "Card has expired" — that's an expired card. "Insufficient balance" — that's insufficient funds. Fast. Deterministic. No API calls.

On this batch, the rules alone hit **93.3% accuracy** — 70 out of 75 correct.

**Layer Two: LLM escalation.**

Twenty percent of cases are genuinely ambiguous. "Transaction declined by bank" — does that mean the issuer said no, or did the customer fail authentication? Even a human reading that line honestly couldn't tell.

We escalate to Groq's LLM, which returns *two things*: a root cause, and its own confidence level.

If the LLM says "I'm not sure," we know it. We don't pretend certainty.

Any LLM failure falls back to error codes. A diagnosis is *always* produced. A payment is never left undiagnosed.

*[Pause 1 second]*

---

## **[2:30–3:45] COMPLIANCE-FIRST DECISIONS**

**Visual:** Runtime Metrics matrix  
**Then:** Zoom to the two Held cards in the stream (low confidence + high value)

---

This is where most AI systems fail.

They optimize for recovery and break compliance. We do the opposite.

After diagnosis comes decision: What action do we take? Retry the payment. Send a payment link. Ask them to update their card.

But we apply *three gates, in order*.

**First gate: Stopping rules.** Max three attempts in a payment's lifetime. Nothing older than 72 hours gets chased. This protects exhausted cases.

**Second gate: Confidence gate.** If the LLM says "low confidence," we don't move money on a guess. We hold it for a human to review.

**Third gate: Value gate.** Anything at or above twenty thousand rupees needs human approval. Not automation.

On this batch of 75 payments, twelve are held. Nine for review — low confidence diagnoses. Three for approval — high value.

Total held: one lakh seventy-three thousand rupees.

That's not money lost. That's the system working. That's compliance.

The agent recovers 59,516 rupees on its own authority.

A human can release one with a single command.

```
python decide/approve.py --approve <payment_id>
```

That payment leaves the hold queue, and the audit trail records why it was allowed to move: *gates skipped, a human approved this payment*. The stopping rules still apply — approval authorises acting on a payment, it does not resurrect one that already spent its retries.

*[Pause 2 seconds]*

---

## **[3:45–4:30] WHY THIS MATTERS: POLICY COMPARISON**

**Visual:** Policy comparison table (4 rows: do_nothing, naive_retry_all, agent_routing_ungated, agent_gated)

---

Does this approach actually work? We ran the entire batch offline under four policies with identical payment luck.

**Naive retry:** Recover four lakh ninety-nine thousand rupees, but make 131 API calls.

**Our routing strategy, ungated:** Six lakh twenty-two thousand rupees recovered, 118 calls. Sixteen percent better. Fewer attempts.

**Our routing strategy, gated:** Three lakh twenty thousand rupees recovered automatically. Four lakh eighty-six thousand rupees held for a person to sign off.

Same outcome for the customer — money recovers. Same compliance risk — a real person approves big moves.

We're not choosing recovery *or* compliance.

We're choosing both.

*[Pause 2 seconds]*

---

## **[4:30–5:00] CLOSE: THE FULL STACK**

**Visual:** Terminal running `python ingest/server.py`, then the curl/POST returning `{"status": "ingested"}`, then a forged signature returning 401 → Dashboard final shot

---

One last thing — this isn't only a batch demo.

`ingest/server.py` is a live endpoint for Razorpay's payment-failed webhook. Point a webhook at it and real failures enter the same pipeline, one event at a time.

It verifies Razorpay's signature against the raw request body, so a forged POST to a public URL gets a 401. It refuses to start at all without a secret configured — it fails closed rather than quietly accepting anything. And because Razorpay redelivers until it gets a success, a repeat delivery is a no-op: re-inserting would hand a payment a fresh budget of retries it had already spent.

So the whole system is:

- Rules plus LLM diagnosis, with stated confidence
- Bounded, gated decisions: stopping rules, confidence, value
- Real Razorpay API calls, with simulated customer completion flagged as such
- Live webhook ingestion, signed and idempotent
- Full audit trail: query any payment, see every decision and why
- A hundred and forty-seven tests, zero network calls required

This is **Reclaim Agent** — payment recovery, built for compliance.

*[End]*

---

## **VISUAL CHECKLIST**

- [ ] 0:00–0:30: Dashboard metrics hero shot (full screen)
- [ ] 0:30–1:15: First Recovered card in the Active Recovery Stream
- [ ] 1:15–2:30: Classifier.py code + diagnosis stats
- [ ] 2:30–3:45: Runtime Metrics matrix + the two Held stream cards
- [ ] 3:45–4:30: Policy comparison table (zoom/highlight key rows)
- [ ] 4:30–5:00: Webhook server running + signed POST accepted + forged POST 401 + dashboard final shot

---

## **RECORDING NOTES**

- **Pacing:** Read at natural conversational speed, slight pauses at section breaks
- **Pauses:** 1–2 seconds between visual transitions (let viewers absorb)
- **Tone:** Confident, clear, explain-it-to-a-colleague (not salesy)
- **Audio:** Record voiceover separately from screen recording for cleaner audio
- **Emphasis:** Slow down slightly on key phrases ("compliance-first," "held for a human," "full audit trail")
