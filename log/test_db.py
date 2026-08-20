"""Offline tests for get_metrics.

get_metrics is pure arithmetic over the rows Supabase hands back, so the
only thing stubbed here is the client's row fetch -- every number asserted
is computed by the real function.
"""

from types import SimpleNamespace

from db import get_metrics


class _FakeTable:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *args, **kwargs):
        return self

    def execute(self):
        return SimpleNamespace(data=self._rows)


class _FakeClient:
    """Stands in for the Supabase client's failed_payments row fetch."""

    def __init__(self, rows):
        self._rows = rows

    def table(self, name):
        assert name == "failed_payments", name
        return _FakeTable(self._rows)


def _row(payment_id, status, amount_inr, recovered_amount_inr=None):
    return {
        "payment_id": payment_id,
        "status": status,
        "amount_inr": amount_inr,
        "recovered_amount_inr": recovered_amount_inr,
    }


BATCH = [
    _row("pay_1", "recovered", 1000.0, 1000.0),
    _row("pay_2", "recovered", 2000.0, 2000.0),
    _row("pay_3", "exhausted", 3000.0),
    _row("pay_4", "needs_review", 4000.0),
    _row("pay_5", "needs_approval", 25000.0),
    _row("pay_6", "needs_approval", 30000.0),
    _row("pay_7", "action_taken", 500.0),
]


def test_held_payments_are_counted_by_the_gate_that_held_them():
    metrics = get_metrics(_FakeClient(BATCH))
    assert metrics["held_for_review_count"] == 1
    assert metrics["held_for_approval_count"] == 2


def test_held_amount_sums_both_kinds_of_hold():
    metrics = get_metrics(_FakeClient(BATCH))
    assert metrics["held_amount_inr"] == 59000.0


def test_held_payments_are_not_reported_as_still_in_progress():
    # A held payment has no retry pending -- it is waiting on a human. Only
    # pay_7 (action_taken) is genuinely still moving through the pipeline.
    metrics = get_metrics(_FakeClient(BATCH))
    assert metrics["still_in_progress"] == 1


def test_existing_metric_keys_are_unchanged():
    # README and other callers read these; adding the held figures must not
    # rename or drop any of them.
    metrics = get_metrics(_FakeClient(BATCH))
    assert metrics["total_batch_size"] == 7
    assert metrics["recovered_count"] == 2
    assert metrics["exhausted_count"] == 1
    assert metrics["recovery_rate_pct"] == 28.6
    assert metrics["total_amount_at_risk_inr"] == 65500.0
    assert metrics["total_amount_recovered_inr"] == 3000.0


def test_an_empty_batch_reports_zeroes_rather_than_dividing_by_zero():
    metrics = get_metrics(_FakeClient([]))
    assert metrics["total_batch_size"] == 0
    assert metrics["recovery_rate_pct"] == 0
    assert metrics["held_amount_inr"] == 0
    assert metrics["still_in_progress"] == 0
