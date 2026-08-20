"""
Runs the two-layer diagnosis router (classify_with_llm) over every
failed_payments row still in needs_diagnosis: the keyword rules resolve
unambiguous reasons for free, and only the ones they cannot resolve
escalate to the LLM, which returns both a cause and its own confidence.
Writes predicted_root_cause and diagnosis_confidence back to Supabase,
logs a 'diagnosed' audit event per row, then prints an accuracy report
against the synthetic ground truth (root_cause) purely for local
validation -- that comparison is never written to Supabase, since a real
audit trail wouldn't have ground truth to compare against.

Run:
    python run_diagnosis.py
"""

import os
import sys
from collections import Counter

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "log"))
from db import get_client, log_event, update_payment  # noqa: E402

from llm_classifier import classify_with_llm


def diagnose_all(client):
    rows = (
        client.table("failed_payments")
        .select("*")
        .eq("status", "needs_diagnosis")
        .execute()
        .data
    )

    for row in rows:
        result = classify_with_llm(row["error_reason"], row["error_code"])

        update_payment(
            client,
            row["payment_id"],
            predicted_root_cause=result.root_cause,
            diagnosis_confidence=result.confidence,
            status="diagnosed",
        )
        log_event(
            client,
            row["payment_id"],
            "diagnosed",
            {
                "predicted_root_cause": result.root_cause,
                "confidence": result.confidence,
                "method": result.method,
                "reasoning": result.reasoning,
            },
        )

    return len(rows)


def print_accuracy_report(client):
    rows = (
        client.table("failed_payments")
        .select("payment_id, root_cause, predicted_root_cause")
        .eq("status", "diagnosed")
        .execute()
        .data
    )

    total = len(rows)
    if total == 0:
        print("\nNo payments currently sitting in 'diagnosed' status -- nothing to report.")
        return

    correct = sum(1 for r in rows if r["predicted_root_cause"] == r["root_cause"])

    # Everything below is reported over one population -- the rows sitting
    # in 'diagnosed' right now -- so the accuracy figure and the method
    # breakdown describe the same payments. Reporting accuracy over these
    # rows but method counts over every 'diagnosed' event ever logged puts
    # two different denominators under one heading.
    population_ids = {r["payment_id"] for r in rows}

    print(f"\nPopulation: the {total} payment(s) currently in 'diagnosed' status")
    print(f"Accuracy vs. synthetic ground truth: {correct}/{total} ({correct / total * 100:.1f}%)")

    events = (
        client.table("audit_log")
        .select("payment_id, detail")
        .eq("event", "diagnosed")
        .execute()
        .data
    )
    methods = Counter(
        e["detail"].get("method")
        for e in events
        if e.get("detail") and e.get("payment_id") in population_ids
    )
    if methods:
        print(f"\nDiagnosis method breakdown (same {total} payment(s)):")
        for method, n in methods.most_common():
            print(f"  {method:16s} {n}")

    confusions = Counter(
        (r["root_cause"], r["predicted_root_cause"])
        for r in rows if r["predicted_root_cause"] != r["root_cause"]
    )
    if confusions:
        print(f"\nMisclassifications (actual -> predicted), same {total} payment(s):")
        for (actual, predicted), n in confusions.most_common():
            print(f"  {n:2d}x  {actual:28s} -> {predicted}")
    else:
        print("\nNo misclassifications.")


def main():
    client = get_client()
    n = diagnose_all(client)
    print(f"Diagnosed {n} payments and logged 'diagnosed' event for each.")
    print_accuracy_report(client)


if __name__ == "__main__":
    main()
