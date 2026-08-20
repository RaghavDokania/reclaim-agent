"""Offline tests for the act layer's per-row failure isolation.

No network: the Razorpay client is a fake that raises on demand, and the
Supabase client is a stub that records what was written. The behaviour
under test is real run_eligible_actions code.

Why these exist: a single API failure on one payment must not end the
batch. Observed live, a razorpay.errors.ServerError ("test mode limit of
30 reached for payment_link") escaped the handler, killed the run, and
froze 12 payments mid-flight in 'action_taken'.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import razorpay

import run_actions
from run_actions import run_eligible_actions

PAST = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(run_actions, "DELAY_BETWEEN_CALLS_SECONDS", 0)


def _row(payment_id, action="send_payment_link"):
    return {
        "payment_id": payment_id,
        "status": "action_taken",
        "action_taken": action,
        "amount_inr": 1000.0,
        "attempt_count": 1,
        "next_action_at": PAST,
    }


class _FakeTable:
    def __init__(self, client, name):
        self._client = client
        self._name = name
        self._rows = client.rows if name == "failed_payments" else []
        self._filter = {}

    def select(self, *args, **kwargs):
        return self

    def eq(self, column, value):
        if self._name == "failed_payments":
            self._filter[column] = value
            self._rows = [r for r in self._rows if r.get(column) == value]
        return self

    def lte(self, column, value):
        self._rows = [r for r in self._rows if r.get(column) is not None and r[column] <= value]
        return self

    def insert(self, record):
        self._client.events.append(record)
        return self

    def update(self, fields):
        self._client.pending_update = fields
        return self

    def execute(self):
        if self._client.pending_update is not None:
            self._client.updates.append((self._filter.get("payment_id"), self._client.pending_update))
            self._client.pending_update = None
        return SimpleNamespace(data=self._rows)


class _FakeSupabase:
    def __init__(self, rows):
        self.rows = rows
        self.events = []
        self.updates = []
        self.pending_update = None

    def table(self, name):
        return _FakeTable(self, name)


class _FakeResource:
    def __init__(self, client, kind):
        self._client = client
        self._kind = kind

    def create(self, payload):
        self._client.calls.append(self._kind)
        exc = self._client.raise_on.pop(0) if self._client.raise_on else None
        if exc is not None:
            raise exc
        return {"id": f"fake_{len(self._client.calls)}", "short_url": "https://example.invalid/x"}


class _FakeRazorpay:
    """raise_on is consumed one entry per API call; None means succeed."""

    def __init__(self, raise_on=None):
        self.raise_on = list(raise_on or [])
        self.calls = []
        self.order = _FakeResource(self, "order")
        self.payment_link = _FakeResource(self, "payment_link")


def _events_for(supabase, payment_id):
    return [e for e in supabase.events if e["payment_id"] == payment_id]


def test_a_server_error_on_one_payment_does_not_stop_the_batch():
    supabase = _FakeSupabase([_row("pay_boom"), _row("pay_ok")])
    client = _FakeRazorpay(raise_on=[
        razorpay.errors.ServerError("test mode limit of 30 reached for payment_link"),
        None,
    ])

    total, results = run_eligible_actions(supabase, client)

    assert total == 2
    assert len(client.calls) == 2, "the batch stopped at the failing row"
    assert results["api_error_skipped"] == 1
    assert [e["event"] for e in _events_for(supabase, "pay_ok")][0] == "action_executed"


def test_a_server_error_leaves_the_failing_row_status_untouched_for_retry():
    supabase = _FakeSupabase([_row("pay_boom")])
    client = _FakeRazorpay(raise_on=[razorpay.errors.ServerError("test mode limit of 30 reached")])

    run_eligible_actions(supabase, client)

    assert [pid for pid, _ in supabase.updates] == []
    detail = _events_for(supabase, "pay_boom")[0]
    assert detail["event"] == "action_error"
    assert detail["detail"]["action"] == "send_payment_link"
    assert "test mode limit of 30 reached" in detail["detail"]["error"]


def test_a_transport_failure_is_caught_per_row_too():
    import requests

    supabase = _FakeSupabase([_row("pay_net", action="retry_payment"), _row("pay_ok")])
    client = _FakeRazorpay(raise_on=[requests.exceptions.ConnectionError("connection reset"), None])

    total, results = run_eligible_actions(supabase, client)

    assert results["api_error_skipped"] == 1
    assert len(client.calls) == 2
    assert _events_for(supabase, "pay_net")[0]["event"] == "action_error"


def test_a_bad_request_error_is_still_caught_per_row():
    # The pre-existing rate-limit path, pinned so broadening the net does
    # not quietly change what already worked.
    supabase = _FakeSupabase([_row("pay_rate")])
    client = _FakeRazorpay(raise_on=[razorpay.errors.BadRequestError("Too many requests")])

    total, results = run_eligible_actions(supabase, client)

    assert results["api_error_skipped"] == 1
    assert _events_for(supabase, "pay_rate")[0]["event"] == "action_error"
