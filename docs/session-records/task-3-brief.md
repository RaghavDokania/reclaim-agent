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
