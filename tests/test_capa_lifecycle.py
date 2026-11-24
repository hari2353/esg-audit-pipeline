"""CAPA lifecycle — Sedex's 2026-27 SMETA CAR redesign as a deterministic
state machine: findings carry recorded progress steps and blockers,
suppliers mark them resolved, and only a separate VERIFICATION closes
them (rejection reopens with progress preserved). "Open or in progress
until resolved" was the problem Sedex set out to fix; this repo had the
same shape — CAPAs were born open and never moved."""

from datetime import date, datetime

import pytest

from esg_pipeline.capa_lifecycle import mark_resolved, start, verify
from esg_pipeline.models import (
    CorrectiveAction,
    CorrectiveActionStatus,
    ProgressEntry,
    SupplierRiskRecord,
)


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def now_date(self) -> date:
        return self.now.date()

    def now_datetime(self) -> datetime:
        return self.now


def make_action() -> CorrectiveAction:
    return CorrectiveAction(
        supplier_id="SUP-1",
        category="health_safety",
        severity="major",
        description="Blocked fire exit",
        action="Clear and mark exit route",
        due_date=date(2026, 9, 30),
    )


CLOCK = FixedClock(datetime(2026, 9, 1, 10, 0, 0))


def test_full_lifecycle_open_to_verified():
    action = start(make_action(), note="supplier began remediation", clock=CLOCK)
    assert action.status is CorrectiveActionStatus.IN_PROGRESS
    assert action.progress_log[0].kind == "started"

    action = mark_resolved(action, note="exit cleared, photo evidence sent", clock=CLOCK)
    assert action.status is CorrectiveActionStatus.RESOLVED
    assert action.resolved_at == CLOCK.now_datetime()

    action = verify(action, outcome="verified", note="auditor confirmed on site", clock=CLOCK)
    assert action.status is CorrectiveActionStatus.VERIFIED
    assert action.verified_at == CLOCK.now_datetime()
    kinds = [entry.kind for entry in action.progress_log]
    assert kinds == ["started", "resolved", "verified"]


def test_rejected_verification_reopens_with_progress_preserved():
    action = start(make_action(), note="begun", clock=CLOCK)
    action = mark_resolved(action, note="claims done", clock=CLOCK)
    action = verify(action, outcome="rejected", note="exit blocked again", clock=CLOCK)
    assert action.status is CorrectiveActionStatus.IN_PROGRESS  # not back to OPEN
    kinds = [entry.kind for entry in action.progress_log]
    assert "resolved" in kinds and "rejected" in kinds  # history intact
    # and it can be resolved again after more work
    action = mark_resolved(action, note="re-cleared", clock=CLOCK)
    assert action.status is CorrectiveActionStatus.RESOLVED


def test_verified_is_terminal():
    action = start(make_action(), note="begun", clock=CLOCK)
    action = mark_resolved(action, note="done", clock=CLOCK)
    action = verify(action, outcome="verified", note="confirmed", clock=CLOCK)
    with pytest.raises(ValueError):
        start(action, note="try restart", clock=CLOCK)
    with pytest.raises(ValueError):
        mark_resolved(action, note="try resolve", clock=CLOCK)
    with pytest.raises(ValueError):
        verify(action, outcome="rejected", note="try reopen", clock=CLOCK)


def test_transitions_refuse_impossible_states():
    action = make_action()
    with pytest.raises(ValueError):
        verify(action, outcome="verified", note="not resolved yet", clock=CLOCK)
    resolved = mark_resolved(action, note="resolved from open", clock=CLOCK)
    assert resolved.status is CorrectiveActionStatus.RESOLVED  # open->resolved allowed
    with pytest.raises(ValueError):
        mark_resolved(resolved, note="already resolved", clock=CLOCK)


def test_progress_entries_are_frozen_records():
    import dataclasses

    entry = ProgressEntry(kind="note", note="waiting on contractor", at=CLOCK.now_datetime())
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.note = "changed"  # type: ignore[misc]


def test_overdue_monitor_never_counts_verified():
    """The portfolio monitor's overdue count must exclude VERIFIED CAPAs —
    the whole point of closure. The CAPA's due date is far in the past."""
    from esg_pipeline.pipeline import DefaultPortfolioMonitor
    from tests.test_repository_contract import _record

    base = _record("SUP-1")
    verified = verify(
        mark_resolved(
            start(base.corrective_actions[0], note="begun", clock=CLOCK), note="done", clock=CLOCK
        ),
        outcome="verified",
        note="confirmed",
        clock=CLOCK,
    )
    record = SupplierRiskRecord(
        audit=base.audit,
        risk=base.risk,
        corrective_actions=(verified,),
        processed_at=base.processed_at,
        source_file=base.source_file,
    )
    monitor = DefaultPortfolioMonitor(today=date(2026, 12, 1))  # past due date
    summary = monitor.summarize((record,))
    assert summary.overdue_capas == 0
    assert summary.open_capas == 0  # VERIFIED is not open either


def test_lifecycle_survives_sqlite_round_trip():
    from esg_pipeline.capa import InMemoryAuditRepository, SqliteAuditRepository
    from tests.test_repository_contract import _record

    base = _record("SUP-1")
    evolved = verify(
        mark_resolved(
            start(base.corrective_actions[0], note="begun", clock=CLOCK), note="done", clock=CLOCK
        ),
        outcome="rejected",
        note="blocked again",
        clock=CLOCK,
    )
    record = SupplierRiskRecord(
        audit=base.audit,
        risk=base.risk,
        corrective_actions=(evolved,),
        processed_at=base.processed_at,
        source_file=base.source_file,
    )

    for repo in (InMemoryAuditRepository(), SqliteAuditRepository(":memory:")):
        repo.save(record)
        loaded = repo.get("SUP-1")
        action = loaded.corrective_actions[0]
        assert action.status is CorrectiveActionStatus.IN_PROGRESS
        kinds = [entry.kind for entry in action.progress_log]
        assert kinds == ["started", "resolved", "rejected"]
        assert action.resolved_at == CLOCK.now_datetime()
        assert action.verified_at is None
        repo.close()
