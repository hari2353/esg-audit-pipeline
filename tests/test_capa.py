"""Unit tests for CAPA planning."""

from __future__ import annotations

from datetime import date

import pytest

from esg_pipeline.capa import CapaPlanner, CapaPolicy
from esg_pipeline.models import FindingSeverity
from esg_pipeline.risk import WeightedDeductionScorer


@pytest.fixture
def planner() -> CapaPlanner:
    return CapaPlanner(clock=lambda: date(2026, 9, 2))


class TestCapaPolicy:
    def test_defaults_valid(self) -> None:
        CapaPolicy()

    def test_due_days_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            CapaPolicy(due_days_by_severity={
                FindingSeverity.MINOR: 0,
                FindingSeverity.MAJOR: 30,
                FindingSeverity.CRITICAL: 7,
            })

    def test_action_text_mentions_category(self) -> None:
        text = CapaPolicy().action_text("child_labor", FindingSeverity.CRITICAL)
        assert "Child Labor" in text


class TestCapaPlanner:
    def test_no_findings_no_actions(self, clean_audit, planner: CapaPlanner) -> None:
        risk = WeightedDeductionScorer().score(clean_audit)
        assert planner.plan(clean_audit, risk) == ()

    def test_due_dates_by_severity(self, poor_audit, planner: CapaPlanner) -> None:
        risk = WeightedDeductionScorer().score(poor_audit)
        actions = planner.plan(poor_audit, risk)
        by_sev = {a.severity: a for a in actions}
        assert by_sev[FindingSeverity.CRITICAL].due_date == date(2026, 9, 9)   # +7
        assert by_sev[FindingSeverity.MAJOR].due_date == date(2026, 10, 2)     # +30
        assert by_sev[FindingSeverity.MINOR].due_date == date(2026, 11, 1)     # +60

    def test_actions_sorted_critical_first(self, poor_audit, planner: CapaPlanner) -> None:
        risk = WeightedDeductionScorer().score(poor_audit)
        actions = planner.plan(poor_audit, risk)
        severities = [a.severity for a in actions]
        order = {FindingSeverity.CRITICAL: 0, FindingSeverity.MAJOR: 1, FindingSeverity.MINOR: 2}
        assert severities == sorted(severities, key=lambda s: order[s])

    def test_threshold_excludes_minor(self, poor_audit, planner: CapaPlanner) -> None:
        policy = CapaPolicy(raise_capa_from_severity=FindingSeverity.MAJOR)
        planner_majors = CapaPlanner(policy=policy, clock=lambda: date(2026, 9, 2))
        risk = WeightedDeductionScorer().score(poor_audit)
        actions = planner_majors.plan(poor_audit, risk)
        assert all(a.severity is not FindingSeverity.MINOR for a in actions)
        assert len(actions) == 2

    def test_deterministic_clock_injection(self, poor_audit) -> None:
        """Same injected date -> identical due dates across planners."""
        risk = WeightedDeductionScorer().score(poor_audit)
        a = CapaPlanner(clock=lambda: date(2026, 9, 2)).plan(poor_audit, risk)
        b = CapaPlanner(clock=lambda: date(2026, 9, 2)).plan(poor_audit, risk)
        assert a == b
