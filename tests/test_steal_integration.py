"""End-to-end integration of the market-steal additions: audit history →
trend → escalation alert, and the CAPA lifecycle excluding verified
actions from overdue alerts — over the real SQLite repository, same
composition the pipeline runner uses."""

from dataclasses import replace
from datetime import date, datetime

from esg_pipeline.alerts import evaluate_alerts
from esg_pipeline.capa import SqliteAuditRepository
from esg_pipeline.capa_lifecycle import mark_resolved, start, verify
from esg_pipeline.models import RiskBand
from esg_pipeline.pipeline import DefaultPortfolioMonitor
from esg_pipeline.trend import portfolio_trends
from tests.test_repository_contract import _record


class FixedClock:
    def now_date(self) -> date:
        return date(2026, 10, 1)

    def now_datetime(self) -> datetime:
        return datetime(2026, 10, 1, 12, 0, 0)


def test_history_trend_escalation_and_verified_exclusion_end_to_end():
    repo = SqliteAuditRepository(":memory:")
    try:
        first = _record("SUP-1")  # HIGH band, audit 2025-02-10
        repo.save(first)

        # a second, worse audit arrives (same supplier, later date)
        second = replace(
            _record("SUP-1"),
            audit=replace(first.audit, audit_date=date(2026, 8, 1)),
            risk=replace(first.risk, total=90.0, band=RiskBand.CRITICAL),
        )
        repo.save(second)

        history = repo.history("SUP-1")
        assert [s.band for s in history] == ["high", "critical"]

        trends = portfolio_trends(repo)
        assert trends["SUP-1"].direction == "declining"
        assert trends["SUP-1"].band_changed is True

        current = repo.get("SUP-1")
        alerts = evaluate_alerts((current,), today=date(2026, 10, 1), history_repo=repo)
        kinds = {a.kind for a in alerts}
        assert "band_escalation" in kinds  # high -> critical across the two audits
        assert "reaudit_due" not in kinds  # latest audit 2026-08-01 is recent
        assert "capa_overdue" in kinds  # due 2026-03-01, still open

        # drive the CAPA through the lifecycle to VERIFIED
        clock = FixedClock()
        action = verify(
            mark_resolved(
                start(current.corrective_actions[0], note="supplier began", clock=clock),
                note="exit cleared, evidence sent",
                clock=clock,
            ),
            outcome="verified",
            note="auditor confirmed on site",
            clock=clock,
        )
        repo.save(replace(current, corrective_actions=(action,)))

        current = repo.get("SUP-1")
        alerts = evaluate_alerts((current,), today=date(2026, 10, 1), history_repo=repo)
        assert "capa_overdue" not in {a.kind for a in alerts}  # verified = closed

        monitor = DefaultPortfolioMonitor(today=date(2026, 10, 1))
        summary = monitor.summarize((current,))
        assert summary.overdue_capas == 0
        assert summary.open_capas == 0
    finally:
        repo.close()
