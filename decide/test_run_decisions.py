"""Offline tests for the audit detail decide_all writes.

Only the Supabase client is stubbed -- the decisions and the event details
asserted here are produced by the real decide_all/decide code path.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from run_decisions import decide_all

# decide_all stamps `now` from the wall clock, so a hardcoded created_at
# would silently age past the 72h stopping window and start exhausting
# these rows. Anchor it to now instead.
FRESH = datetime.now(timezone.utc).isoformat()


def _row(payment_id, **overrides):
    row = {
        "payment_id": payment_id,
        "status": "diagnosed",
        "predicted_root_cause": "network_timeout",
        "created_at": FRESH,
        "attempt_count": 0,
        "amount_inr": 1000.0,
        "diagnosis_confidence": "high",
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
        return _FakeTable(self, name if name == "failed_payments" else name)


def _details_by_event(client):
    return {e["event"]: e["detail"] for e in client.events}


def test_a_recorded_confidence_is_logged_and_not_marked_defaulted():
    client = _FakeClient([_row("pay_1", diagnosis_confidence="high")])
    decide_all(client)

    detail = _details_by_event(client)["decided"]
    assert detail["confidence"] == "high"
    assert detail["confidence_defaulted"] is False


def test_a_missing_confidence_is_logged_as_defaulted_high():
    # Pre-existing rows predate the diagnosis_confidence column and come
    # back NULL. Defaulting them to "high" authorises a money action, so
    # the audit trail has to say that is what happened -- an auditor must
    # be able to tell a diagnosed high-confidence row from an assumed one.
    client = _FakeClient([_row("pay_1", diagnosis_confidence=None)])
    decide_all(client)

    detail = _details_by_event(client)["decided"]
    assert detail["confidence"] == "high"
    assert detail["confidence_defaulted"] is True


def test_a_low_confidence_row_logs_low_and_not_defaulted():
    client = _FakeClient([_row("pay_1", diagnosis_confidence="low")])
    decide_all(client)

    detail = _details_by_event(client)["held_for_review"]
    assert detail["confidence"] == "low"
    assert detail["confidence_defaulted"] is False


def test_every_logged_event_carries_the_confidence_provenance():
    # decided, exhausted, held_for_review and held_for_approval all record a
    # decision about money; none of them may be missing the provenance.
    client = _FakeClient([
        _row("pay_decided"),
        _row("pay_exhausted", attempt_count=3, diagnosis_confidence=None),
        _row("pay_review", diagnosis_confidence="low"),
        _row("pay_approval", amount_inr=25000.0),
    ])
    total, counts = decide_all(client)

    assert total == 4
    assert set(counts) == {"action_taken", "exhausted", "needs_review", "needs_approval"}
    assert {e["event"] for e in client.events} == {
        "decided", "exhausted", "held_for_review", "held_for_approval",
    }
    for event in client.events:
        assert "confidence" in event["detail"], event["event"]
        assert "confidence_defaulted" in event["detail"], event["event"]
