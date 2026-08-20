"""Offline tests for the timestamp rebase.

Pure functions only -- no Supabase, no file writes. data/failed_payments.json
is a fixed-timestamp snapshot, so against decide()'s 72h stopping window
every published number decays with wall-clock time: the further a run is
from the day the batch was generated, the more of it exhausts on contact.
Rebasing shifts the whole window forward by ONE constant offset, so the
batch's internal spacing -- which is what the policy actually reacts to --
is preserved exactly while the ages become current.
"""

from datetime import datetime, timedelta, timezone

from load_batch import build_records, rebase_timestamps

BATCH = [
    {"payment_id": "pay_1", "created_at": "2026-08-14T00:30:12.118082", "audit_log": []},
    {"payment_id": "pay_2", "created_at": "2026-08-17T21:35:12.118082", "audit_log": []},
    {"payment_id": "pay_3", "created_at": "2026-08-20T14:25:12.118082", "audit_log": []},
]


def _parsed(records):
    return [datetime.fromisoformat(r["created_at"]) for r in records]


def test_the_newest_record_lands_at_approximately_now():
    rebased, _ = rebase_timestamps(BATCH, datetime.now(timezone.utc))
    newest = max(_parsed(rebased))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs((now - newest).total_seconds()) < 60


def test_every_relative_gap_between_records_is_preserved_exactly():
    # The offset is one constant for the whole batch, so ages shift but
    # the batch's shape does not. If gaps moved, the rebase would be
    # manufacturing a different batch rather than re-dating this one.
    before = _parsed(BATCH)
    rebased, _ = rebase_timestamps(BATCH, datetime.now(timezone.utc))
    after = _parsed(rebased)

    gaps_before = [b - before[0] for b in before]
    gaps_after = [a - after[0] for a in after]
    assert gaps_before == gaps_after


def test_the_applied_offset_is_returned_so_a_run_can_describe_itself():
    now = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    _, offset = rebase_timestamps(BATCH, now)
    assert offset == datetime(2026, 9, 1, 12, 0, 0) - datetime.fromisoformat(
        "2026-08-20T14:25:12.118082"
    )


def test_rebasing_does_not_mutate_the_records_it_was_given():
    # The committed snapshot must survive a rebase untouched -- the
    # 89.3% accuracy figure is measured against those exact records.
    rebase_timestamps(BATCH, datetime.now(timezone.utc))
    assert BATCH[0]["created_at"] == "2026-08-14T00:30:12.118082"


def test_build_records_without_the_flag_leaves_timestamps_untouched():
    records, offset = build_records(BATCH)
    assert [r["created_at"] for r in records] == [b["created_at"] for b in BATCH]
    assert offset is None


def test_build_records_with_the_flag_shifts_the_batch_forward():
    records, offset = build_records(BATCH, rebase=True, now=datetime.now(timezone.utc))
    assert offset is not None
    assert offset > timedelta(0)
    assert [r["created_at"] for r in records] != [b["created_at"] for b in BATCH]


def test_build_records_strips_the_audit_log_field_either_way():
    plain, _ = build_records(BATCH)
    rebased, _ = build_records(BATCH, rebase=True, now=datetime.now(timezone.utc))
    assert all("audit_log" not in r for r in plain)
    assert all("audit_log" not in r for r in rebased)
