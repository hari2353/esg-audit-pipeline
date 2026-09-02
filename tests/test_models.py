"""Unit tests for domain models."""

from __future__ import annotations

from datetime import date

import pytest

from esg_pipeline.models import (
    Finding,
    FindingSeverity,
    RiskBand,
    RiskScore,
    SupplierAudit,
)


class TestFinding:
    def test_valid_finding(self) -> None:
        f = Finding("health_safety", FindingSeverity.MAJOR, "Blocked exit.")
        assert f.category == "health_safety"
        assert f.severity is FindingSeverity.MAJOR

    def test_unknown_category_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown finding category"):
            Finding("money_laundering", FindingSeverity.MINOR, "desc")

    def test_blank_description_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            Finding("environment", FindingSeverity.MINOR, "   ")

    def test_frozen(self) -> None:
        f = Finding("environment", FindingSeverity.MINOR, "desc")
        with pytest.raises(AttributeError):
            f.category = "wages_benefits"  # type: ignore[misc]


class TestSupplierAudit:
    def test_valid_audit(self, clean_audit: SupplierAudit) -> None:
        assert clean_audit.critical_count == 0
        assert clean_audit.major_count == 0

    def test_counts(self, poor_audit: SupplierAudit) -> None:
        assert poor_audit.critical_count == 1
        assert poor_audit.major_count == 1

    def test_score_bounds_enforced(self, clean_audit: SupplierAudit) -> None:
        with pytest.raises(ValueError, match="overall_score"):
            SupplierAudit(
                supplier_id="X", supplier_name="Y", country="Z",
                audit_date=date(2025, 1, 1), auditor="A", scheme="S",
                overall_score=101, findings=(),
            )

    def test_empty_supplier_id_rejected(self, clean_audit: SupplierAudit) -> None:
        with pytest.raises(ValueError, match="supplier_id"):
            SupplierAudit(
                supplier_id="  ", supplier_name="Y", country="Z",
                audit_date=date(2025, 1, 1), auditor="A", scheme="S",
                overall_score=50, findings=(),
            )


class TestRiskScore:
    def test_valid_risk_score(self) -> None:
        rs = RiskScore(total=42.0, band=RiskBand.MEDIUM)
        assert rs.total == 42.0

    def test_total_bounds(self) -> None:
        with pytest.raises(ValueError, match="risk total"):
            RiskScore(total=100.5, band=RiskBand.CRITICAL)
        with pytest.raises(ValueError, match="risk total"):
            RiskScore(total=-0.1, band=RiskBand.LOW)
