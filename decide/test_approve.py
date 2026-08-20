"""Offline tests for the release approve.py performs.

Only the Supabase client is stubbed -- the update and the audit detail
asserted here are produced by the real approve() code path.

The point of these: releasing a payment is only real if it leaves a
durable record that a human authorised it. A release that only flips
status back to 'diagnosed' is undone by the next decide pass, which
re-applies the same gate to the same amount.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from approve import approve


def _row(**overrides):
    row = {
        "payment_id": "pay_1",
        "status": "needs_approval",
        "amount_inr": 25000.0,
        "predicted_root_cause": "auth_failure",
        "diagnosis_confidence": "high",
        "human_approved_at": None,
    }
    row.update(overrides)
    return row


class _FakeTable:
    def __init__(self, client, name):
        self._client = client
        self._name = name
        self._rows = client.rows

    def select(self, *args, **kwargs):
        return self

    def eq(self, column, value):
        self._rows = [r for r in self._rows if r.get(column) == value]
        return self

    def in_(self, column, values):
        self._rows = [r for r in self._rows if r.get(column) in values]
        return self

    def insert(self, record):
        self._client.events.append(record)
        return self

    def update(self, fields):
        self._client.updates.append(fields)
        return self

    def execute(self):
        return SimpleNamespace(data=self._rows)


class _FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.events = []
        self.updates = []

    def table(self, name):
        return _FakeTable(self, name)


def test_approve_returns_the_status_it_released_from():
    client = _FakeClient([_row()])
    assert approve(client, "pay_1") == "needs_approval"


def test_approve_sets_status_back_to_diagnosed():
    client = _FakeClient([_row()])
    approve(client, "pay_1")
    assert client.updates[0]["status"] == "diagnosed"


def test_approve_stamps_human_approved_at_in_the_same_update():
    # One update, not two: a row must never exist in 'diagnosed' without
    # its approval stamp, or a concurrent decide pass re-holds it.
    client = _FakeClient([_row()])
    approve(client, "pay_1")

    assert len(client.updates) == 1
    stamped = client.updates[0]["human_approved_at"]
    parsed = datetime.fromisoformat(stamped)
    assert parsed.tzinfo is not None
    assert abs((datetime.now(timezone.utc) - parsed).total_seconds()) < 60


def test_the_human_approved_event_records_the_approval_timestamp():
    client = _FakeClient([_row()])
    approve(client, "pay_1")

    event = client.events[0]
    assert event["event"] == "human_approved"
    assert event["detail"]["human_approved_at"] == client.updates[0]["human_approved_at"]


def test_a_payment_that_is_not_held_is_not_approved():
    client = _FakeClient([_row(status="diagnosed")])
    assert approve(client, "pay_1") is None
    assert client.updates == []
    assert client.events == []


def test_an_unknown_payment_is_not_approved():
    client = _FakeClient([_row()])
    assert approve(client, "pay_missing") is None
    assert client.updates == []
