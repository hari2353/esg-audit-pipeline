"""Review fix pack: the Critical history-duplication bug (any re-save of a
record appended a phantom audit-history point, flipping real
deterioration to 'stable' and killing band-escalation alerts), the legacy
CLOSED status treated as open, idempotent start(), alert dedup with
counts, and the smaller spec-compliance items."""

from dataclasses import replace
from datetime import date, datetime

import pytest

from esg_pipeline.alerts import evaluate_alerts
from esg_pipeline.capa import InMemoryAuditRepository, SqliteAuditRepository
from esg_pipeline.capa_lifecycle import start, verify
from esg_pipeline.models import CorrectiveActionStatus, RiskBand
from esg_pipeline.pipeline import UNPARSED_SUPPLIER_NAME, _error_risk, _placeholder_audit
from esg_pipeline.trend import portfolio_trends
from tests.test_repository_contract import _record

TODAY = date(2026, 10, 1)


def _worse(record):
    return replace(
        record,
        audit=replace(record.audit, audit_date=date(2026, 8, 1)),
        risk=replace(record.risk, total=90.0, band=RiskBand.CRITICAL),
    )


def test_resaving_a_record_does_not_duplicate_history():
    """The Critical: saving an evolved record back (the lifecycle's own
    persistence workflow) must not append a phantom point for the same
    audit. Deterioration must survive a re-save."""
    for repo in (InMemoryAuditRepository(), SqliteAuditRepository(":memory:")):
        first = _record("SUP-1")
        repo.save(first)
        repo.save(_worse(first))
        # re-save the current record unchanged (a CAPA lifecycle update)
        current = repo.get("SUP-1")
        repo.save(current)
        history = repo.history("SUP-1")
        assert len(history) == 2, f"{type(repo).__name__}: phantom history point"
        assert [s.band for s in history] == ["high", "critical"]
        trends = portfolio_trends(repo)
        assert trends["SUP-1"].direction == "declining"  # not 'stable'
        alerts = evaluate_alerts((repo.get("SUP-1"),), today=TODAY, history_repo=repo)
        assert "band_escalation" in {a.kind for a in alerts}  # alert survives
        repo.close()


def test_legacy_closed_status_is_terminal_not_open():
    """Pre-branch databases store CAPAs as 'closed'; the lifecycle made
    everything non-VERIFIED count as open — resurrecting closed actions
    as open AND overdue. CLOSED is terminal, like VERIFIED."""
    record = _record("SUP-1")
    closed = replace(
        record,
        corrective_actions=(
            replace(record.corrective_actions[0], status=CorrectiveActionStatus.CLOSED),
        ),
    )
    alerts = evaluate_alerts((closed,), today=TODAY, history_repo=None)
    assert not [a for a in alerts if a.kind == "capa_overdue"]

    from esg_pipeline.pipeline import DefaultPortfolioMonitor

    summary = DefaultPortfolioMonitor(today=TODAY).summarize((closed,))
    assert summary.open_capas == 0 and summary.overdue_capas == 0


def test_start_is_idempotent_for_double_clicks():
    """Spec: a second start() appends a note, never raises — a UI
    double-click must not crash the caller."""
    record = _record("SUP-1")
    clock = type(
        "C",
        (),
        {
            "now_date": lambda s: TODAY,
            "now_datetime": lambda s: datetime(2026, 3, 1),
        },
    )()
    action = start(record.corrective_actions[0], note="begun", clock=clock)
    action = start(action, note="resumed (double-click)", clock=clock)
    assert action.status is CorrectiveActionStatus.IN_PROGRESS
    kinds = [entry.kind for entry in action.progress_log]
    assert kinds == ["started", "note"]


def test_two_overdue_capas_same_category_one_alert_with_count():

    record = _record("SUP-1")
    doubled = replace(
        record,
        corrective_actions=(
            record.corrective_actions[0],
            replace(
                record.corrective_actions[0],
                description="second blocked exit",
                action="clear it too",
            ),
        ),
    )
    alerts = evaluate_alerts((doubled,), today=TODAY, history_repo=None)
    overdue = [a for a in alerts if a.kind == "capa_overdue"]
    assert len(overdue) == 1  # one alert, but...
    assert "2 overdue CAPAs" in overdue[0].detail  # the count is visible


def test_unknown_band_in_history_does_not_crash_alerts():
    from esg_pipeline.models import AuditSummary

    class StubRepo:
        def history(self, supplier_id):
            return (
                AuditSummary(audit_date=date(2026, 1, 1), total=10.0, band="low"),
                AuditSummary(audit_date=date(2026, 7, 1), total=50.0, band="bogus"),
            )

    record = _record("SUP-1")
    alerts = evaluate_alerts((record,), today=TODAY, history_repo=StubRepo())
    assert isinstance(alerts, tuple)  # no crash; unknown bands simply don't rank


def test_failed_extractions_do_not_alert_as_reaudit_due():
    from pathlib import Path

    placeholder = _placeholder_audit(Path("x.pdf"))
    from esg_pipeline.models import SupplierRiskRecord

    failed_record = SupplierRiskRecord(audit=placeholder, risk=_error_risk())
    alerts = evaluate_alerts((failed_record,), today=TODAY, history_repo=None)
    assert not [a for a in alerts if a.kind == "reaudit_due"]
    assert placeholder.supplier_name == UNPARSED_SUPPLIER_NAME  # the guard's target


def test_terminal_guard_precedes_outcome_validation():
    """verify(verified_action, outcome='banana') must say the CAPA is
    closed, not complain about the outcome string."""
    record = _record("SUP-1")
    clock = type(
        "C",
        (),
        {
            "now_date": lambda s: TODAY,
            "now_datetime": lambda s: datetime(2026, 3, 1),
        },
    )()
    action = record.corrective_actions[0]
    from esg_pipeline.capa_lifecycle import mark_resolved

    closed = verify(
        mark_resolved(start(action, note="b", clock=clock), note="d", clock=clock),
        outcome="verified",
        note="v",
        clock=clock,
    )
    with pytest.raises(ValueError, match="closed"):
        verify(closed, outcome="banana", note="x", clock=clock)
