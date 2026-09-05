"""
Turns live pipeline state into the numbers the dashboard renders.

Split so the arithmetic is testable without a network: build_view_model
takes the metrics dict get_metrics() already produces plus the raw rows,
and returns everything the HTML needs. Only main() touches Supabase.
"""

import os
import re
import sys
from html import escape

LAKH = 100_000

GATE_BY_STATUS = {
    "needs_approval": "Value (>= 20k)",
    "needs_review": "Confidence",
    "recovered": "Passed",
}

SAMPLE_ORDER = ("recovered", "needs_review", "needs_approval")

PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")

DOT_BY_STATUS = {
    "recovered": "",
    "needs_review": " critical",
    "needs_approval": " critical",
}

LABEL_BY_STATUS = {
    "recovered": "Recovered",
    "needs_review": "Held",
    "needs_approval": "Held",
}


def format_inr(amount):
    if amount >= LAKH:
        return f"₹ {amount / LAKH:.2f}L"
    return f"₹ {amount:,.2f}"


def _sample(row):
    return {
        "payment_id": row["payment_id"],
        "status": row["status"],
        "amount_inr": row["amount_inr"],
        "amount_display": format_inr(row["amount_inr"]),
        # What the agent concluded, never the hidden ground truth --
        # showing root_cause here would make a misdiagnosis look correct.
        "root_cause": row.get("predicted_root_cause") or "undiagnosed",
        "confidence": row.get("diagnosis_confidence") or "high",
        "gate": GATE_BY_STATUS.get(row["status"], "-"),
    }


def build_view_model(metrics, rows):
    view = dict(metrics)

    samples = []
    for status in SAMPLE_ORDER:
        match = next((r for r in rows if r["status"] == status), None)
        if match:
            samples.append(_sample(match))
    view["samples"] = samples

    view["held_amount_display"] = format_inr(metrics["held_amount_inr"])
    view["recovered_amount_display"] = format_inr(metrics["total_amount_recovered_inr"])
    view["at_risk_display"] = format_inr(metrics["total_amount_at_risk_inr"])

    return view


def render(template, view):
    def swap(match):
        key = match.group(1)
        value = str(view.get(key, ""))
        # _html keys hold markup this module built and already escaped.
        return value if key.endswith("_html") else escape(value)

    return PLACEHOLDER.sub(swap, template)


def render_samples(samples):
    cards = []
    for s in samples:
        tone = "high" if s["confidence"] == "high" else "low"
        dot = DOT_BY_STATUS.get(s["status"], "")
        label = LABEL_BY_STATUS.get(s["status"], s["status"])
        cards.append(
            '\n                <div class="stream-card">\n'
            f'                    <div class="payment-id">{escape(s["payment_id"])}</div>\n'
            '                    <div style="margin-bottom: 12px;">\n'
            f'                        <span class="status-indicator{dot}"></span>\n'
            f'                        <span class="stream-status">{escape(label)}</span>\n'
            '                    </div>\n'
            f'                    <div class="amount-display">{escape(s["amount_display"])}</div>\n'
            '                    <div class="metadata-grid">\n'
            '                        <div class="metadata-item">\n'
            '                            <div class="metadata-label">Diagnosis</div>\n'
            f'                            <div class="metadata-value">{escape(s["root_cause"] or "undiagnosed")}</div>\n'
            '                        </div>\n'
            '                        <div class="metadata-item">\n'
            '                            <div class="metadata-label">Confidence</div>\n'
            f'                            <div class="metadata-value {tone}">{escape(s["confidence"])}</div>\n'
            '                        </div>\n'
            '                        <div class="metadata-item">\n'
            '                            <div class="metadata-label">Gate</div>\n'
            f'                            <div class="metadata-value">{escape(s["gate"])}</div>\n'
            '                        </div>\n'
            '                        <div class="metadata-item">\n'
            '                            <div class="metadata-label">Status</div>\n'
            f'                            <div class="metadata-value">{escape(s["status"])}</div>\n'
            '                        </div>\n'
            '                    </div>\n'
            '                </div>'
        )
    return "\n".join(cards)


def count_methods(audit_rows):
    counts = {}
    for row in audit_rows:
        method = (row.get("detail") or {}).get("method") or "code_fallback"
        counts[method] = counts.get(method, 0) + 1
    return counts


def scoreable(rows):
    """Rows the accuracy figure can honestly be measured on.

    Needs both a prediction and a ground-truth label. Live webhook rows
    have no root_cause, so scoring them would count every real payment as
    a miss and quietly drag the published accuracy down.
    """
    return [r for r in rows if r.get("predicted_root_cause") and r.get("root_cause")]


def accuracy_pct(rows):
    if not rows:
        return 0
    correct = sum(1 for r in rows if r["predicted_root_cause"] == r["root_cause"])
    return round(correct / len(rows) * 100, 1)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.dirname(here))

    from log.db import get_client, get_metrics

    client = get_client()
    metrics = get_metrics(client)
    rows = client.table("failed_payments").select("*").execute().data
    diagnosed = client.table("audit_log").select("detail").eq("event", "diagnosed").execute().data

    view = build_view_model(metrics, rows)
    view["samples_html"] = render_samples(view["samples"])

    methods = count_methods(diagnosed)
    total_diagnosed = sum(methods.values()) or 1
    view["keyword_count"] = methods.get("keyword", 0)
    view["llm_count"] = methods.get("llm", 0)
    view["keyword_pct"] = f"{methods.get('keyword', 0) / total_diagnosed * 100:.0f}%"
    view["llm_pct"] = f"{methods.get('llm', 0) / total_diagnosed * 100:.0f}%"

    scored = scoreable(rows)
    view["accuracy_display"] = f"{accuracy_pct(scored)}%"
    view["misclassified_count"] = sum(
        1 for r in scored if r["predicted_root_cause"] != r["root_cause"]
    )
    view["misclassified_pct"] = (
        f"{view['misclassified_count'] / len(scored) * 100:.1f}%" if scored else "0%"
    )

    template_path = os.path.join(here, "template.html")
    output_path = os.path.join(os.path.dirname(here), "dashboard.html")

    with open(template_path, encoding="utf-8") as f:
        template = f.read()

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(render(template, view))

    print(f"Wrote {output_path} from {metrics['total_batch_size']} live rows.")


if __name__ == "__main__":
    main()
