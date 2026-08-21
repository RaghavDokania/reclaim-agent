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

