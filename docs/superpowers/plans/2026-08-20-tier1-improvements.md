# Tier 1 Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three highest-impact judging gaps in reclaim-agent — add real AI to the diagnosis layer, prove the agent beats a naive baseline, and stop firing money actions on low-confidence diagnoses.

**Architecture:** The rule-based classifier stays as the cheap deterministic first pass; genuinely ambiguous reasons escalate to a Groq-hosted LLM whose stated reasoning is written into the audit trail. A new offline analysis harness replays the committed batch under three competing policies to produce a defensible lift number. The decision layer gains two new gates — low-confidence diagnoses and high-value payments never auto-execute a money action.

**Tech Stack:** Python 3.14, Supabase (Postgres), Razorpay test-mode SDK, langchain-groq, pytest.

**Spec:** No separate spec document. This plan is the authority; it derives from the Tier 1 items agreed in conversation on 2026-08-20 and the Track 3 judging bar (measured money recovered, compliant escalation, stopping rules, full audit trail, every money action explainable/bounded/gated).

## Global Constraints

- **Never commit `.env`.** It holds live Supabase, Razorpay, and Groq credentials. `.env.example` carries placeholders only.
- **All tests must run without network access.** Inject LLM callables and RNG functions as parameters with real defaults; never let pytest reach Groq, Supabase, or Razorpay.
- **The existing 23 tests must keep passing** (`diagnose/test_classifier.py`, `decide/test_policy.py`, `act/test_simulate_outcome.py`).
- **`python run_pipeline.py` must keep working** end to end after every task.
- **Every money action stays bounded and gated.** Max 3 attempts, 72h window. New gates only ever *restrict* what executes, never widen it.
- **The audit trail is the explainability record.** Any new decision input (LLM reasoning, confidence, gate reason) must land in `audit_log.detail`.
- **Root cause vocabulary is fixed** — exactly these five strings: `insufficient_funds`, `card_declined_by_issuer`, `auth_failure`, `network_timeout`, `expired_card`.
- **Status vocabulary** after this plan: `needs_diagnosis`, `diagnosed`, `action_taken`, `recovered`, `exhausted`, `needs_review`, `needs_approval`.
- **Schema changes ship as numbered migrations** in `log/migrations/NNN_description.sql`, and are also folded into `log/supabase_schema.sql` so a fresh setup gets them.
- Windows environment. Bash tool available; use forward slashes in Python paths.

---

### Task 1: LLM escalation for ambiguous diagnoses

Rules handle the clear cases; the LLM handles only what the rules cannot resolve. This is what makes the project an AI project rather than a keyword matcher, and it should lift accuracy above the current 89.3% by resolving the `card_declined_by_issuer` vs `auth_failure` confusion.

**Files:**
- Create: `diagnose/llm_classifier.py`
- Create: `diagnose/test_llm_classifier.py`
- Modify: `diagnose/classifier.py` (add the router; keep `classify()` unchanged)
- Modify: `diagnose/run_diagnosis.py` (use the router, persist confidence, log reasoning)
- Modify: `requirements.txt` (add `langchain-groq`, drop unused `langchain-anthropic`)
- Modify: `.env.example` (add `GROQ_API_KEY`)
- Create: `log/migrations/002_add_diagnosis_confidence.sql`
- Modify: `log/supabase_schema.sql` (fold in the new column)

**Interfaces:**
- Consumes: `classify(error_reason, error_code) -> ClassificationResult` from `diagnose/classifier.py`, with fields `root_cause`, `confidence` (`"high"`/`"low"`), `method` (`"keyword"`/`"code_fallback"`).
- Produces:
  - `diagnose/llm_classifier.py`: `classify_with_llm(error_reason, error_code, llm_func=None) -> ClassificationResult` where `ClassificationResult` gains a `reasoning: str | None = None` field. `method` may now also be `"llm"`.
  - `llm_func` signature: `Callable[[str], str]` — takes a prompt, returns raw model text.
  - A new `failed_payments.diagnosis_confidence` text column, populated by `run_diagnosis.py`. **Task 3 consumes this column.**

- [ ] **Step 1: Add the `reasoning` field to `ClassificationResult`**

In `diagnose/classifier.py`, extend the dataclass (existing fields and `classify()` logic stay exactly as they are):

```python
@dataclass
class ClassificationResult:
    root_cause: str
    confidence: str   # "high" | "low"
    method: str        # "keyword" | "llm" | "code_fallback"
    reasoning: str | None = None
```

Run `python -m pytest diagnose/test_classifier.py -v` — all 10 must still pass (the new field is optional).

- [ ] **Step 2: Write the failing tests for the LLM router**

Create `diagnose/test_llm_classifier.py`. These tests inject a fake `llm_func`, so no network is touched:

```python
import pytest

from llm_classifier import classify_with_llm

VALID = {
    "insufficient_funds", "card_declined_by_issuer", "auth_failure",
    "network_timeout", "expired_card",
}


def test_clear_keyword_match_never_calls_the_llm():
    def exploding_llm(prompt):
        raise AssertionError("LLM must not be called for a clear keyword match")

    result = classify_with_llm("Card has expired", "BAD_REQUEST_ERROR", llm_func=exploding_llm)
    assert result.root_cause == "expired_card"
    assert result.method == "keyword"
    assert result.confidence == "high"


def test_ambiguous_reason_escalates_to_the_llm():
    def fake_llm(prompt):
        return '{"root_cause": "card_declined_by_issuer", "reasoning": "issuer-side decline"}'

    result = classify_with_llm("Transaction declined by bank", "GATEWAY_ERROR", llm_func=fake_llm)
    assert result.root_cause == "card_declined_by_issuer"
    assert result.method == "llm"
    assert result.confidence == "high"
    assert result.reasoning == "issuer-side decline"


def test_llm_prompt_contains_the_error_reason_and_code():
    captured = {}

    def fake_llm(prompt):
        captured["prompt"] = prompt
        return '{"root_cause": "auth_failure", "reasoning": "ok"}'

    classify_with_llm("Transaction declined by bank", "GATEWAY_ERROR", llm_func=fake_llm)
    assert "Transaction declined by bank" in captured["prompt"]
    assert "GATEWAY_ERROR" in captured["prompt"]


def test_llm_returning_an_invalid_root_cause_falls_back_to_error_code():
    def fake_llm(prompt):
        return '{"root_cause": "customer_changed_their_mind", "reasoning": "made up"}'

    result = classify_with_llm("Transaction declined by bank", "GATEWAY_ERROR", llm_func=fake_llm)
    assert result.root_cause in VALID
    assert result.method == "code_fallback"
    assert result.confidence == "low"


def test_llm_returning_unparseable_text_falls_back_to_error_code():
    def fake_llm(prompt):
        return "I'm not sure, could be a few things honestly"

    result = classify_with_llm("Transaction declined by bank", "GATEWAY_ERROR", llm_func=fake_llm)
    assert result.method == "code_fallback"
    assert result.confidence == "low"


def test_llm_raising_an_exception_falls_back_to_error_code():
    def exploding_llm(prompt):
        raise RuntimeError("groq is down")

    result = classify_with_llm("Transaction declined by bank", "GATEWAY_ERROR", llm_func=exploding_llm)
    assert result.method == "code_fallback"
    assert result.confidence == "low"
    assert result.root_cause in VALID


def test_llm_response_wrapped_in_markdown_fences_is_parsed():
    def fake_llm(prompt):
        return '```json\n{"root_cause": "network_timeout", "reasoning": "gateway side"}\n```'

    result = classify_with_llm("Transaction declined by bank", "SERVER_ERROR", llm_func=fake_llm)
    assert result.root_cause == "network_timeout"
    assert result.method == "llm"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd diagnose && python -m pytest test_llm_classifier.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'llm_classifier'`.

- [ ] **Step 4: Install langchain-groq and update dependency files**

```bash
python -m pip install langchain-groq
```

In `requirements.txt`, replace the line `langchain-anthropic   # or langchain-groq, whichever LLM you're wiring in` with:

```
langchain-groq        # LLM escalation for ambiguous failure reasons
```

In `.env.example`, append:

```
GROQ_API_KEY=your-groq-api-key
```

- [ ] **Step 5: Write `diagnose/llm_classifier.py`**

```python
"""
LLM escalation layer for root-cause diagnosis.

The rule-based classifier in classifier.py handles unambiguous failure
reasons deterministically and for free. Only reasons it cannot resolve --
text that matches no category, or more than one -- escalate here, where a
model reads the reason and picks a cause. Roughly one failure in eight
reaches this path, so the LLM cost stays proportional to the genuinely
hard cases rather than the whole batch.

Any LLM failure (unreachable, malformed output, a made-up category)
degrades to the same error_code fallback the rules layer already used, so
a diagnosis is always produced.
"""

import json
import os
import re
from typing import Callable, Optional

from classifier import CODE_FALLBACK, DEFAULT_FALLBACK, ClassificationResult, classify

VALID_ROOT_CAUSES = {
    "insufficient_funds",
    "card_declined_by_issuer",
    "auth_failure",
    "network_timeout",
    "expired_card",
}

GROQ_MODEL = "llama-3.3-70b-versatile"

PROMPT_TEMPLATE = """You are triaging a failed payment for an Indian payment gateway.

Classify the failure into exactly one of these root causes:
- insufficient_funds: the customer's account did not have enough money
- card_declined_by_issuer: the issuing bank refused the transaction (not an authentication problem)
- auth_failure: OTP, 3D Secure, or two-factor authentication was wrong, incomplete, or abandoned
- network_timeout: a gateway or bank connectivity problem, not a customer problem
- expired_card: the card's expiry date has passed

Error code: {error_code}
Error reason: {error_reason}

Respond with JSON only, no prose:
{{"root_cause": "<one of the five above>", "reasoning": "<one short sentence>"}}"""


def _default_llm(prompt: str) -> str:
    """Real Groq call. Imported lazily so tests never need the dependency."""
    from langchain_groq import ChatGroq

    model = ChatGroq(model=GROQ_MODEL, temperature=0, api_key=os.environ["GROQ_API_KEY"])
    return model.invoke(prompt).content


def _parse(raw: str) -> Optional[dict]:
    """Pull the JSON object out of a model response, tolerating markdown fences."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def classify_with_llm(
    error_reason: str,
    error_code: str,
    llm_func: Optional[Callable[[str], str]] = None,
) -> ClassificationResult:
    rules_result = classify(error_reason, error_code)
    if rules_result.method == "keyword":
        return rules_result

    llm = llm_func or _default_llm
    prompt = PROMPT_TEMPLATE.format(error_code=error_code, error_reason=error_reason)

    try:
        parsed = _parse(llm(prompt))
    except Exception as e:  # noqa: BLE001 -- any LLM failure degrades to the rules fallback
        return ClassificationResult(
            root_cause=CODE_FALLBACK.get(error_code, DEFAULT_FALLBACK),
            confidence="low",
            method="code_fallback",
            reasoning=f"llm unavailable: {e}",
        )

    if not parsed or parsed.get("root_cause") not in VALID_ROOT_CAUSES:
        return ClassificationResult(
            root_cause=CODE_FALLBACK.get(error_code, DEFAULT_FALLBACK),
            confidence="low",
            method="code_fallback",
            reasoning="llm returned an unusable response",
        )

    return ClassificationResult(
        root_cause=parsed["root_cause"],
        confidence="high",
        method="llm",
        reasoning=str(parsed.get("reasoning", ""))[:300],
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd diagnose && python -m pytest test_llm_classifier.py test_classifier.py -v`
Expected: all 17 pass (7 new + 10 existing).

- [ ] **Step 7: Add the `diagnosis_confidence` column**

Create `log/migrations/002_add_diagnosis_confidence.sql`:

```sql
alter table failed_payments add column if not exists diagnosis_confidence text;
```

In `log/supabase_schema.sql`, add the column to the `failed_payments` definition immediately after the `predicted_root_cause` line:

```sql
    diagnosis_confidence text,                  -- "high" | "low", drives the decide layer's review gate
```

Report in the task report that this migration must be run by hand in the Supabase SQL Editor — the controller will run it.

- [ ] **Step 8: Wire the router into `run_diagnosis.py`**

In `diagnose/run_diagnosis.py`, change the import from `from classifier import classify` to:

```python
from llm_classifier import classify_with_llm
```

Replace the `result = classify(...)` call inside `diagnose_all` with:

```python
        result = classify_with_llm(row["error_reason"], row["error_code"])
```

Extend the `update_payment` call to persist confidence:

```python
        update_payment(
            client,
            row["payment_id"],
            predicted_root_cause=result.root_cause,
            diagnosis_confidence=result.confidence,
            status="diagnosed",
        )
```

Extend the `log_event` detail dict to carry the reasoning:

```python
            {
                "predicted_root_cause": result.root_cause,
                "confidence": result.confidence,
                "method": result.method,
                "reasoning": result.reasoning,
            },
```

- [ ] **Step 9: Add a method-breakdown line to the accuracy report**

In `print_accuracy_report`, after the accuracy line, the report should show how many diagnoses came from each method so the LLM's contribution is visible. Since `method` is not persisted, derive it from the audit log instead — add this inside `print_accuracy_report`, after the accuracy print:

```python
    events = (
        client.table("audit_log")
        .select("payment_id, detail")
        .eq("event", "diagnosed")
        .execute()
        .data
    )
    methods = Counter(e["detail"].get("method") for e in events if e.get("detail"))
    if methods:
        print("\nDiagnosis method breakdown (all diagnosed events):")
        for method, n in methods.most_common():
            print(f"  {method:16s} {n}")
```

- [ ] **Step 10: Verify the full existing suite still passes**

Run from the repo root: `python -m pytest diagnose/test_classifier.py diagnose/test_llm_classifier.py decide/test_policy.py act/test_simulate_outcome.py -v`
Expected: 30 passed.

- [ ] **Step 11: Commit**

```bash
git add diagnose/ log/ requirements.txt .env.example
git commit -m "feat: escalate ambiguous diagnoses to a Groq LLM, persist confidence"
```

---

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

### Task 3: Confidence and value gates on money actions

The track's bar asks for compliant escalation. Right now a low-confidence guess fires a real payment link exactly like a high-confidence match does, and a ₹24,000 payment is treated identically to a ₹200 one. Both get a gate: low-confidence diagnoses route to `needs_review`, high-value payments route to `needs_approval`, and neither executes a money action until a human moves it.

**Files:**
- Modify: `decide/policy.py`
- Modify: `decide/test_policy.py`
- Modify: `decide/run_decisions.py`
- Modify: `ingest/schema.py` (extend the `Status` enum)

**Interfaces:**
- Consumes: `failed_payments.diagnosis_confidence` (text, `"high"`/`"low"`) — **created by Task 1**. Rows diagnosed before Task 1 ran will have `NULL` here; treat `NULL` as `"high"` so the existing batch keeps flowing rather than being retroactively quarantined.
- Produces: `decide(root_cause, created_at, attempt_count, now, confidence="high", amount_inr=0.0) -> DecisionResult`. The two new parameters are keyword arguments with defaults, so every existing call site and test keeps working unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `decide/test_policy.py` (keep all 8 existing tests exactly as they are):

```python
def test_low_confidence_diagnosis_routes_to_review_instead_of_acting():
    result = decide("auth_failure", NOW, attempt_count=1, now=NOW, confidence="low")
    assert result.status == "needs_review"
    assert result.action is None
    assert result.next_action_at is None
    assert "confidence" in result.reason.lower()


def test_high_value_payment_routes_to_approval_instead_of_acting():
    result = decide("auth_failure", NOW, attempt_count=1, now=NOW, amount_inr=25000.0)
    assert result.status == "needs_approval"
    assert result.action is None
    assert result.next_action_at is None
    assert "20,000" in result.reason or "20000" in result.reason


def test_payment_just_under_the_high_value_threshold_still_acts():
    result = decide("auth_failure", NOW, attempt_count=1, now=NOW, amount_inr=19999.0)
    assert result.status == "action_taken"


def test_stopping_rules_take_precedence_over_the_new_gates():
    # An exhausted payment is finished -- it must not be resurrected into a
    # review queue by a low-confidence diagnosis.
    result = decide("auth_failure", NOW, attempt_count=3, now=NOW, confidence="low", amount_inr=25000.0)
    assert result.status == "exhausted"


def test_confidence_gate_takes_precedence_over_the_value_gate():
    result = decide("auth_failure", NOW, attempt_count=1, now=NOW, confidence="low", amount_inr=25000.0)
    assert result.status == "needs_review"


def test_defaults_preserve_the_original_behaviour():
    result = decide("network_timeout", NOW, attempt_count=1, now=NOW)
    assert result.status == "action_taken"
    assert result.action == "retry_payment"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd decide && python -m pytest test_policy.py -v`
Expected: the 6 new tests fail (`TypeError: decide() got an unexpected keyword argument 'confidence'`), the 8 existing ones pass.

- [ ] **Step 3: Implement the gates in `decide/policy.py`**

Add the threshold constant next to the existing ones:

```python
HIGH_VALUE_THRESHOLD_INR = 20000.0
```

Replace the `decide` function signature and add the two gates **after** the existing stopping rules and **before** the policy lookup, so a finished payment is never resurrected into a queue:

```python
def decide(
    root_cause: str,
    created_at: datetime,
    attempt_count: int,
    now: datetime,
    confidence: str = "high",
    amount_inr: float = 0.0,
) -> DecisionResult:
    if attempt_count >= MAX_ATTEMPTS:
        return DecisionResult(
            action=None, status="exhausted", next_action_at=None,
            reason=f"hit max attempt limit ({MAX_ATTEMPTS})",
        )

    age_hours = (now - created_at).total_seconds() / 3600
    if age_hours > STOPPING_WINDOW_HOURS:
        return DecisionResult(
            action=None, status="exhausted", next_action_at=None,
            reason=f"older than {STOPPING_WINDOW_HOURS}h stopping window ({age_hours:.1f}h old)",
        )

    # Gates below hold a payment back from an automatic money action. They
    # come after the stopping rules on purpose: a payment that is already
    # finished stays finished rather than landing in a human queue.
    if confidence == "low":
        return DecisionResult(
            action=None, status="needs_review", next_action_at=None,
            reason="low confidence diagnosis -- not acting on a guess, routing to human review",
        )

    if amount_inr >= HIGH_VALUE_THRESHOLD_INR:
        return DecisionResult(
            action=None, status="needs_approval", next_action_at=None,
            reason=f"amount Rs {amount_inr:,.2f} is at or above the Rs {HIGH_VALUE_THRESHOLD_INR:,.0f} auto-action threshold",
        )

    action, delay_hours = POLICY[root_cause]
    next_action_at = now + timedelta(hours=delay_hours)
    return DecisionResult(
        action=action, status="action_taken", next_action_at=next_action_at,
        reason=f"{root_cause} -> {action}",
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd decide && python -m pytest test_policy.py -v`
Expected: 14 passed.

- [ ] **Step 5: Extend the `Status` enum**

In `ingest/schema.py`, add the two new statuses to the `Status` enum, after `EXHAUSTED`:

```python
    NEEDS_REVIEW = "needs_review"        # low-confidence diagnosis, held for a human
    NEEDS_APPROVAL = "needs_approval"    # high-value, held for explicit sign-off
```

- [ ] **Step 6: Pass the new inputs through `run_decisions.py`**

In `decide/run_decisions.py`, extend the `decide(...)` call inside `decide_all`:

```python
        result = decide(
            root_cause=row["predicted_root_cause"],
            created_at=created_at,
            attempt_count=row["attempt_count"],
            now=now,
            confidence=row.get("diagnosis_confidence") or "high",
            amount_inr=row["amount_inr"],
        )
```

Replace the event-name line so the two new statuses log distinctly rather than being mislabelled `exhausted`:

```python
        event = {
            "action_taken": "decided",
            "exhausted": "exhausted",
            "needs_review": "held_for_review",
            "needs_approval": "held_for_approval",
        }[result.status]
```

- [ ] **Step 7: Verify the whole suite passes**

Run from the repo root:
`python -m pytest diagnose/test_classifier.py diagnose/test_llm_classifier.py decide/test_policy.py act/test_simulate_outcome.py analysis/test_compare_policies.py -v`
Expected: 42 passed.

- [ ] **Step 8: Commit**

```bash
git add decide/ ingest/schema.py
git commit -m "feat: gate money actions on diagnosis confidence and payment value"
```
