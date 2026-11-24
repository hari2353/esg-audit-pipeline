"""Alerts — Avetta's workflow triggers ('system-driven alerts when
compliance statuses change') and expiry tracking, as three deterministic
evaluations over repository state and the injected clock: overdue CAPAs
(critical when they sit on zero-tolerance categories), band escalations
from the audit history, and re-audit due dates."""

from datetime import date, datetime, timedelta

from esg_pipeline.alerts import evaluate_alerts
from esg_pipeline.capa_lifecycle import mark_resolved, start, verify
from tests.test_repository_contract import _record

TODAY = date(2026, 10, 1)


def _history_repo(*entries):
    class StubRepo:
        def __init__(self, hist):
            self._hist = hist

        def history(self, supplier_id):
            return self._hist.get(supplier_id, ())

    from esg_pipeline.models import AuditSummary

    hist = {}
    for supplier, points in entries:
        hist[supplier] = tuple(
            AuditSummary(audit_date=date.fromisoformat(d), total=t, band=b) for d, t, b in points
        )
    return StubRepo(hist)


def test_overdue_capa_alerts_with_severity_by_category():
    record = _record("SUP-1")  # health_safety CAPA, due 2026-03-01
    alerts = evaluate_alerts((record,), today=TODAY, history_repo=None)
    overdue = [a for a in alerts if a.kind == "capa_overdue"]
    assert overdue and overdue[0].severity == "warning"  # health_safety not zero-tolerance

    # zero-tolerance categories (child/forced labor) escalate to critical
    from dataclasses import replace

    from esg_pipeline.models import CorrectiveAction, FindingSeverity

    zero = replace(
        record,
        corrective_actions=(
            CorrectiveAction(
                supplier_id="SUP-1",
                category="child_labor",
                severity=FindingSeverity.CRITICAL,
                description=" underage worker",
                action="remediate",
                due_date=date(2026, 3, 1),
            ),
        ),
    )
    alerts = evaluate_alerts((zero,), today=TODAY, history_repo=None)
    assert alerts[0].severity == "critical"


def test_verified_capas_never_alert_as_overdue():
    record = _record("SUP-1")
    clock = type(
        "C", (), {"now_date": lambda s: TODAY, "now_datetime": lambda s: datetime(2026, 3, 1)}
    )()
    action = verify(
        mark_resolved(
            start(record.corrective_actions[0], note="b", clock=clock), note="d", clock=clock
        ),
        outcome="verified",
        note="v",
        clock=clock,
    )
    from dataclasses import replace

    closed = replace(record, corrective_actions=(action,))
    alerts = evaluate_alerts((closed,), today=TODAY, history_repo=None)
    assert not [a for a in alerts if a.kind == "capa_overdue"]


def test_band_escalation_alert_uses_history():
    record = _record("SUP-1")  # band HIGH on the latest audit
    repo = _history_repo(("SUP-1", [("2026-01-15", 20.0, "low"), ("2026-07-15", 76.4, "high")]))
    alerts = evaluate_alerts((record,), today=TODAY, history_repo=repo)
    escalations = [a for a in alerts if a.kind == "band_escalation"]
    assert escalations and escalations[0].severity == "critical"
    assert "low" in escalations[0].detail and "high" in escalations[0].detail

    # no escalation when the band did not worsen
    stable = _history_repo(("SUP-1", [("2026-01-15", 76.4, "high"), ("2026-07-15", 30.0, "low")]))
    alerts = evaluate_alerts((record,), today=TODAY, history_repo=stable)
    assert not [a for a in alerts if a.kind == "band_escalation"]


def test_reaudit_due_alert():
    record = _record("SUP-1")  # audit date 2025-02-10
    alerts = evaluate_alerts((record,), today=TODAY, history_repo=None, reaudit_window_days=365)
    due = [a for a in alerts if a.kind == "reaudit_due"]
    assert due and due[0].severity == "info"
    # and a fresh audit does not alert
    from dataclasses import replace

    fresh = replace(record.audit, audit_date=TODAY - timedelta(days=30))
    alerts = evaluate_alerts(
        (replace(record, audit=fresh),), today=TODAY, history_repo=None, reaudit_window_days=365
    )
    assert not [a for a in alerts if a.kind == "reaudit_due"]


def test_no_duplicate_alerts_per_condition():
    record = _record("SUP-1")
    alerts = evaluate_alerts((record,) * 3, today=TODAY, history_repo=None)
    kinds = [(a.kind, a.supplier_id) for a in alerts if a.kind == "capa_overdue"]
    assert kinds == [("capa_overdue", "SUP-1")]  # one per (kind, supplier, condition)
