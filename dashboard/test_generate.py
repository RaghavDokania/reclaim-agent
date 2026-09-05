import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from generate import (
    accuracy_pct,
    build_view_model,
    count_methods,
    format_inr,
    render,
    render_samples,
    scoreable,
)


def _row(status, amount, root_cause="network_timeout", confidence="high",
         payment_id="pay_x", predicted=...):
    return {
        "payment_id": payment_id,
        "status": status,
        "amount_inr": amount,
        "root_cause": root_cause,
        "predicted_root_cause": root_cause if predicted is ... else predicted,
        "diagnosis_confidence": confidence,
    }


METRICS = {
    "total_batch_size": 5,
    "recovered_count": 2,
    "exhausted_count": 1,
    "held_for_review_count": 1,
    "held_for_approval_count": 1,
    "held_amount_inr": 193634.53,
    "still_in_progress": 0,
    "recovery_rate_pct": 40.0,
    "total_amount_at_risk_inr": 1040061.78,
    "total_amount_recovered_inr": 59515.77,
}


def test_metrics_are_passed_through_untouched():
    view = build_view_model(METRICS, [])
    for key, value in METRICS.items():
        assert view[key] == value


def test_amounts_over_a_lakh_are_shown_in_lakhs():
    assert format_inr(193634.53) == "₹ 1.94L"
    assert format_inr(1040061.78) == "₹ 10.40L"


def test_amounts_under_a_lakh_keep_full_precision():
    assert format_inr(59515.77) == "₹ 59,515.77"


def test_one_sample_is_picked_per_outcome():
    rows = [
        _row("recovered", 14195.92, payment_id="pay_rec"),
        _row("needs_review", 14597.63, confidence="low", payment_id="pay_rev"),
        _row("needs_approval", 24634.97, payment_id="pay_app"),
        _row("exhausted", 10.0, payment_id="pay_exh"),
    ]

    view = build_view_model(METRICS, rows)
    ids = [s["payment_id"] for s in view["samples"]]

    assert ids == ["pay_rec", "pay_rev", "pay_app"]


def test_a_missing_outcome_is_simply_absent_from_samples():
    rows = [_row("recovered", 100.0, payment_id="pay_only")]

    view = build_view_model(METRICS, rows)

    assert [s["payment_id"] for s in view["samples"]] == ["pay_only"]


def test_each_sample_carries_the_reason_it_landed_there():
    rows = [_row("needs_approval", 24634.97, root_cause="expired_card", payment_id="pay_app")]

    sample = build_view_model(METRICS, rows)["samples"][0]

    assert sample["gate"] == "Value (>= 20k)"
    assert sample["root_cause"] == "expired_card"


def test_a_sample_shows_what_the_agent_predicted_not_the_hidden_answer():
    # If the card showed ground truth, a misdiagnosis would look correct
    # on screen -- the dashboard would flatter the agent it is reporting on.
    rows = [_row("recovered", 100.0, root_cause="auth_failure",
                 predicted="card_declined_by_issuer", payment_id="pay_wrong")]

    sample = build_view_model(METRICS, rows)["samples"][0]

    assert sample["root_cause"] == "card_declined_by_issuer"


def test_an_undiagnosed_sample_says_so_rather_than_showing_nothing():
    rows = [_row("recovered", 100.0, predicted=None, payment_id="pay_new")]

    sample = build_view_model(METRICS, rows)["samples"][0]

    assert sample["root_cause"] == "undiagnosed"


def test_a_low_confidence_hold_names_the_confidence_gate():
    rows = [_row("needs_review", 500.0, confidence="low", payment_id="pay_rev")]

    sample = build_view_model(METRICS, rows)["samples"][0]

    assert sample["gate"] == "Confidence"


def test_render_substitutes_every_placeholder():
    template = "<p>{{recovered_count}} recovered, {{held_amount_display}} held</p>"

    html = render(template, {"recovered_count": 4, "held_amount_display": "X"})

    assert html == "<p>4 recovered, X held</p>"


def test_render_leaves_no_unfilled_placeholder_behind():
    template = "<p>{{recovered_count}}</p><p>{{missing_key}}</p>"

    html = render(template, {"recovered_count": 4})

    assert "{{" not in html


def test_a_sample_renders_its_payment_id_and_gate():
    html = render_samples([
        {
            "payment_id": "pay_app",
            "status": "needs_approval",
            "amount_display": "Rs 24,634.97",
            "root_cause": "expired_card",
            "confidence": "high",
            "gate": "Value (>= 20k)",
        }
    ])

    assert "pay_app" in html
    assert "Value (&gt;= 20k)" in html
    assert "expired_card" in html


def test_sample_content_is_escaped_so_it_cannot_inject_markup():
    html = render_samples([
        {
            "payment_id": "<script>alert(1)</script>",
            "status": "recovered",
            "amount_display": "Rs 1.00",
            "root_cause": "network_timeout",
            "confidence": "high",
            "gate": "Passed",
        }
    ])

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_a_prerendered_html_placeholder_is_not_escaped_again():
    template = "<div>{{samples_html}}</div>"

    html = render(template, {"samples_html": '<span class="x">hi</span>'})

    assert html == '<div><span class="x">hi</span></div>'


def test_a_scalar_placeholder_is_still_escaped():
    template = "<div>{{root_cause}}</div>"

    html = render(template, {"root_cause": "<script>"})

    assert "&lt;script&gt;" in html


def test_method_counts_come_from_the_diagnosed_audit_events():
    audit = [
        {"detail": {"method": "keyword"}},
        {"detail": {"method": "keyword"}},
        {"detail": {"method": "llm"}},
    ]

    counts = count_methods(audit)

    assert counts == {"keyword": 2, "llm": 1}


def test_an_event_missing_its_method_is_counted_as_a_code_fallback():
    counts = count_methods([{"detail": {}}])

    assert counts == {"code_fallback": 1}


def test_accuracy_compares_the_prediction_against_the_true_cause():
    rows = [
        {"root_cause": "auth_failure", "predicted_root_cause": "auth_failure"},
        {"root_cause": "auth_failure", "predicted_root_cause": "card_declined_by_issuer"},
        {"root_cause": "expired_card", "predicted_root_cause": "expired_card"},
    ]

    assert accuracy_pct(rows) == 66.7


def test_accuracy_of_an_empty_batch_is_zero_rather_than_a_crash():
    assert accuracy_pct([]) == 0


def test_a_row_never_diagnosed_counts_against_accuracy():
    rows = [{"root_cause": "auth_failure", "predicted_root_cause": None}]

    assert accuracy_pct(rows) == 0.0


def test_an_unscoreable_webhook_row_is_excluded_rather_than_counted_wrong():
    # A live webhook row has no ground-truth root_cause. Once diagnosed it
    # has a prediction and no answer to check it against, so scoring it
    # would silently count every real payment as a misclassification.
    rows = [
        {"root_cause": "auth_failure", "predicted_root_cause": "auth_failure"},
        {"root_cause": None, "predicted_root_cause": "network_timeout"},
    ]

    assert accuracy_pct(scoreable(rows)) == 100.0


def test_a_row_with_ground_truth_but_no_prediction_yet_is_also_excluded():
    rows = [
        {"root_cause": "auth_failure", "predicted_root_cause": "auth_failure"},
        {"root_cause": "expired_card", "predicted_root_cause": None},
    ]

    assert scoreable(rows) == [rows[0]]


def test_a_webhook_row_without_a_root_cause_still_renders_as_a_sample():
    # Live rows have no ground-truth root_cause; rendering must not crash
    # on the None, and must not print "None" at the viewer either.
    html = render_samples([
        {
            "payment_id": "pay_live",
            "status": "recovered",
            "amount_display": "₹ 1.00",
            "root_cause": None,
            "confidence": "high",
            "gate": "Passed",
        }
    ])

    assert "pay_live" in html
    assert "None" not in html
