# Task 1 Report: LLM escalation for ambiguous diagnoses

## What was implemented

Followed the brief (`task-1-brief.md`) step by step with TDD.

1. **`diagnose/classifier.py`** — added `reasoning: str | None = None` to `ClassificationResult`. `classify()` logic untouched.
2. **`diagnose/test_llm_classifier.py`** — created verbatim from the brief (7 tests, fake `llm_func` injected in every case, no network).
3. **`diagnose/llm_classifier.py`** — created verbatim from the brief: `classify_with_llm(error_reason, error_code, llm_func=None)`. Routes to the rules classifier first; only escalates to the LLM when `method != "keyword"`. Parses JSON out of the model response (tolerating markdown fences via regex), validates `root_cause` against the fixed 5-value vocabulary, and degrades to the existing `CODE_FALLBACK`/`DEFAULT_FALLBACK` with `confidence="low"`, `method="code_fallback"` on any LLM exception, unparseable text, or invalid root cause.
4. **`requirements.txt`** — replaced `langchain-anthropic` line with `langchain-groq        # LLM escalation for ambiguous failure reasons`. Installed via `python -m pip install langchain-groq` (succeeded, pulled in `groq-0.37.1` and `langchain-groq-1.1.3`).
5. **`.env.example`** — appended `GROQ_API_KEY=your-groq-api-key` (placeholder only, no real key ever touched this file).
6. **`log/migrations/002_add_diagnosis_confidence.sql`** — created: `alter table failed_payments add column if not exists diagnosis_confidence text;`. **This migration has NOT been run against Supabase — it must be applied by hand in the Supabase SQL Editor. I have no live-run responsibility for it; the controller runs it.**
7. **`log/supabase_schema.sql`** — added `diagnosis_confidence text,` immediately after `predicted_root_cause` in the `failed_payments` table definition, with the comment `-- "high" | "low", drives the decide layer's review gate`.
8. **`diagnose/run_diagnosis.py`** — swapped `from classifier import classify` for `from llm_classifier import classify_with_llm`; `diagnose_all` now calls `classify_with_llm(row["error_reason"], row["error_code"])`; `update_payment` now also passes `diagnosis_confidence=result.confidence`; `log_event`'s detail dict now also carries `"reasoning": result.reasoning`. `Counter` was already imported (not touched). Added the method-breakdown block inside `print_accuracy_report`, right after the accuracy print, querying `audit_log` for `event == "diagnosed"` events and tallying `detail["method"]` via `Counter`.

## Deviation from the brief (controller-directed, mid-task)

The brief specified `GROQ_MODEL = "llama-3.3-70b-versatile"`. Mid-task the controller reported this model 404s (`model_not_found`) on the live Groq account, and had live-tested the brief's exact prompt against every chat-capable candidate on that account: `openai/gpt-oss-120b` returns clean, directly-parseable JSON with no preamble and correctly classifies the ambiguous "Transaction declined by bank" case as `card_declined_by_issuer`; `qwen/qwen3.6-27b` prepends a `<think>` reasoning block before the JSON; `openai/gpt-oss-20b` returned an empty string. I changed the constant to `GROQ_MODEL = "openai/gpt-oss-120b"` and re-ran the full 30-test suite (unaffected, since every test injects a fake `llm_func` and the constant is only read inside `_default_llm`, which no test exercises). Committed separately as a follow-up (`44f7cec`) since the first commit had already landed when the correction arrived.

## Commands run, in order, with actual output

### Step 1 verify — 10 existing tests still pass after adding `reasoning` field
```
$ cd diagnose && python -m pytest test_classifier.py -v
============================= test session starts =============================
collected 10 items
test_classifier.py::test_classifies_insufficient_funds_from_clear_keyword PASSED [ 10%]
test_classifier.py::test_classifies_expired_card_from_clear_keyword PASSED [ 20%]
test_classifier.py::test_classifies_network_timeout_from_clear_keyword PASSED [ 30%]
test_classifier.py::test_classifies_auth_failure_from_clear_keyword PASSED [ 40%]
test_classifier.py::test_reason_mentioning_both_otp_and_timeout_is_genuinely_ambiguous PASSED [ 50%]
test_classifier.py::test_classifies_card_declined_by_issuer_from_clear_keyword PASSED [ 60%]
test_classifier.py::test_ambiguous_reason_falls_back_to_error_code PASSED [ 70%]
test_classifier.py::test_reason_matching_multiple_categories_falls_back_to_error_code PASSED [ 80%]
test_classifier.py::test_missing_reason_falls_back_to_error_code PASSED  [ 90%]
test_classifier.py::test_unknown_error_code_and_reason_still_returns_a_root_cause PASSED [100%]
============================= 10 passed in 0.03s ==============================
```

### Step 3 — RED: new test file fails for the expected reason
```
$ cd diagnose && python -m pytest test_llm_classifier.py -v
ERRORS ====================================
ERROR collecting test_llm_classifier.py
ImportError while importing test module 'D:\RazorPay\reclaim-agent\diagnose\test_llm_classifier.py'.
test_llm_classifier.py:3: in <module>
    from llm_classifier import classify_with_llm
E   ModuleNotFoundError: No module named 'llm_classifier'
=========================== short test summary info ===========================
ERROR test_llm_classifier.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
============================== 1 error in 0.35s ===============================
```
Confirmed: fails for the expected reason (module doesn't exist yet), not a typo or wrong assertion.

### Step 4 — dependency install
```
$ python -m pip install langchain-groq
...
Successfully installed groq-0.37.1 langchain-groq-1.1.3
```

### Step 6 — GREEN: LLM router tests + existing classifier tests
```
$ cd diagnose && python -m pytest test_llm_classifier.py test_classifier.py -v
collected 17 items
test_llm_classifier.py::test_clear_keyword_match_never_calls_the_llm PASSED [  5%]
test_llm_classifier.py::test_ambiguous_reason_escalates_to_the_llm PASSED [ 11%]
test_llm_classifier.py::test_llm_prompt_contains_the_error_reason_and_code PASSED [ 17%]
test_llm_classifier.py::test_llm_returning_an_invalid_root_cause_falls_back_to_error_code PASSED [ 23%]
test_llm_classifier.py::test_llm_returning_unparseable_text_falls_back_to_error_code PASSED [ 29%]
test_llm_classifier.py::test_llm_raising_an_exception_falls_back_to_error_code PASSED [ 35%]
test_llm_classifier.py::test_llm_response_wrapped_in_markdown_fences_is_parsed PASSED [ 41%]
test_classifier.py (all 10) PASSED
============================= 17 passed in 0.04s ==============================
```

### Sanity check — `run_diagnosis.py` still parses/imports cleanly, no network needed
```
$ python -c "import ast; ast.parse(open('run_diagnosis.py').read())" && echo "SYNTAX OK"
SYNTAX OK
$ python -c "import run_diagnosis; print('import ok')"
import ok
```
(Confirms `load_dotenv()`/`get_client()` are not called at import time, so no live credentials are needed just to import the module.)

### Step 10 — full suite from repo root
```
$ python -m pytest diagnose/test_classifier.py diagnose/test_llm_classifier.py decide/test_policy.py act/test_simulate_outcome.py -v
collected 30 items
... (all 30 PASSED, listed individually) ...
============================= 30 passed in 0.06s ==============================
```

### Post-fix re-verification (after GROQ_MODEL change to openai/gpt-oss-120b)
```
$ python -m pytest diagnose/test_classifier.py diagnose/test_llm_classifier.py decide/test_policy.py act/test_simulate_outcome.py -v
... 30 passed in 0.19s ...
```

## Commits

1. `67f0758` — `feat: escalate ambiguous diagnoses to a Groq LLM, persist confidence`
   Files: `.env.example`, `diagnose/classifier.py`, `diagnose/llm_classifier.py` (new), `diagnose/run_diagnosis.py`, `diagnose/test_llm_classifier.py` (new), `log/migrations/002_add_diagnosis_confidence.sql` (new), `log/supabase_schema.sql`, `requirements.txt`. 8 files changed, 207 insertions(+), 5 deletions(-).
2. `44f7cec` — `fix: switch GROQ_MODEL to openai/gpt-oss-120b`
   Files: `diagnose/llm_classifier.py`. 1 file changed, 1 insertion(+), 1 deletion(-). Controller-directed correction, see Deviation section above.

## Things that surprised me / notes

- `git add diagnose/ log/ requirements.txt .env.example` also picked up an unrelated untracked `docs/` directory in `git status` output — I did **not** stage or commit it, since it's out of scope for this task and not listed in the brief's file list.
- Git warned about LF→CRLF conversion on every staged file (Windows `core.autocrlf` behavior) — cosmetic only, did not affect test runs or diffs.
- `pip install langchain-groq` triggered a `groq` package downgrade-then-upgrade (uninstalled 1.1.2, installed 0.37.1 as a langchain-groq dependency) — no conflict, install succeeded cleanly.
- No other deviations from the brief. All code blocks were used verbatim except the single controller-directed `GROQ_MODEL` value change.

## Outstanding / for the controller

- `log/migrations/002_add_diagnosis_confidence.sql` has not been applied to Supabase. Needs to be run by hand in the Supabase SQL Editor before `diagnosis_confidence` can actually be written/read against the live table.
- `GROQ_API_KEY` is still absent from the real `.env` (not committed, and correctly so — only the placeholder went into `.env.example`). Live Groq calls will fail with a `KeyError` on `os.environ["GROQ_API_KEY"]` until that's added by whoever owns the live run.
