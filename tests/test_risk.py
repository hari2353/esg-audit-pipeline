"""Unit tests for the risk scoring engine."""

from __future__ import annotations

import pytest

from esg_pipeline.models import Finding, FindingSeverity, RiskBand
from esg_pipeline.risk import ScoringConfig, WeightedDeductionScorer, with_overrides


class TestScoringConfig:
    def test_defaults_valid(self) -> None:
        ScoringConfig()

    def test_missing_severity_rejected(self) -> None:
        with pytest.raises(ValueError, match="severity_deductions"):
            ScoringConfig(
                severity_deductions={FindingSeverity.MINOR: 1.0, FindingSeverity.MAJOR: 2.0}
            )

    def test_base_weight_bounds(self) -> None:
        with pytest.raises(ValueError, match="base_score_weight"):
            ScoringConfig(base_score_weight=1.5)

    def test_thresholds_must_ascending(self) -> None:
        with pytest.raises(ValueError, match="ascending"):
            ScoringConfig(
                band_thresholds={
                    RiskBand.LOW: 20.0,
                    RiskBand.MEDIUM: 10.0,  # out of order
                    RiskBand.HIGH: 60.0,
                }
            )

    def test_with_overrides_immutable(self) -> None:
        base = ScoringConfig()
        custom = with_overrides(base, base_score_weight=0.9)
        assert base.base_score_weight == 0.6
        assert custom.base_score_weight == 0.9


class TestWeightedDeductionScorer:
    def test_clean_audit_is_low_risk(self, clean_audit) -> None:
        score = WeightedDeductionScorer().score(clean_audit)
        assert score.total == pytest.approx(3.0)  # (100-95) * 0.6
        assert score.band is RiskBand.LOW

    def test_zero_tolerance_escalates_to_critical(self, poor_audit) -> None:
        """child_labor is zero-tolerance: minor findings count as critical."""
        from datetime import date

        from esg_pipeline.models import FindingSeverity, SupplierAudit

        audit = SupplierAudit(
            supplier_id="SUP-ZT", supplier_name="Z", country="Z",
            audit_date=date(2025, 1, 1), auditor="A", scheme="S", overall_score=70,
            findings=(Finding("child_labor", FindingSeverity.MINOR, "Records gap."),),
        )
        scorer = WeightedDeductionScorer()
        base = scorer.score(
            SupplierAudit(
                supplier_id="SUP-ZT", supplier_name="Z", country="Z",
                audit_date=date(2025, 1, 1), auditor="A", scheme="S", overall_score=70,
                findings=(),
            )
        )
        score = scorer.score(audit)
        cfg = ScoringConfig()
        expected = (
            base.total
            + cfg.severity_deductions[FindingSeverity.CRITICAL]
            + cfg.zero_tolerance_deduction
        )
        assert score.total == pytest.approx(expected)
        assert score.band is RiskBand.HIGH

    def test_score_clamped_to_100(self, poor_audit) -> None:
        score = WeightedDeductionScorer().score(poor_audit)
        assert score.total <= 100.0

    def test_band_thresholds_respected(self, clean_audit) -> None:
        cfg = ScoringConfig()
        scorer = WeightedDeductionScorer(cfg)
        # Base = (100 - overall_score) * 0.6, so a finding-free audit can
        # reach at most 60.0 (HIGH); CRITICAL requires actual findings.
        from datetime import date

        from esg_pipeline.models import SupplierAudit

        def audit_with_score(overall: int) -> SupplierAudit:
            return SupplierAudit(
                supplier_id="SUP-T", supplier_name="T", country="T",
                audit_date=date(2025, 1, 1), auditor="A", scheme="S",
                overall_score=overall, findings=(),
            )

        # Band boundaries are inclusive: total <= threshold.
        assert scorer.score(audit_with_score(100)).band is RiskBand.LOW    # 0.0
        assert scorer.score(audit_with_score(75)).band is RiskBand.LOW     # 15.0
        assert scorer.score(audit_with_score(70)).band is RiskBand.MEDIUM   # 18.0
        assert scorer.score(audit_with_score(41)).band is RiskBand.HIGH     # 35.4
        assert scorer.score(audit_with_score(0)).band is RiskBand.HIGH      # 60.0

        critical_audit = SupplierAudit(
            supplier_id="SUP-T2", supplier_name="T", country="T",
            audit_date=date(2025, 1, 1), auditor="A", scheme="S",
            overall_score=50,
            findings=tuple(
                Finding(c, FindingSeverity.CRITICAL, "x")
                for c in ("environment", "wages_benefits", "health_safety")
            ),
        )
        assert scorer.score(critical_audit).band is RiskBand.CRITICAL

    def test_factors_explain_total(self, poor_audit) -> None:
        """Sum of factor deductions + base == total (clamped)."""
        score = WeightedDeductionScorer().score(poor_audit)
        assert score.total == min(100.0, sum(f.deduction for f in score.factors))

    def test_poor_audit_is_critical_or_high(self, poor_audit) -> None:
        score = WeightedDeductionScorer().score(poor_audit)
        assert score.band in (RiskBand.HIGH, RiskBand.CRITICAL)
