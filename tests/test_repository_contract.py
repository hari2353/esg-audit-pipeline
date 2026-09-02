"""Contract tests: any AuditRepository implementation must satisfy these.

Adding a new repository (Postgres, SharePoint-backed, ...) only requires
pointing `repo_factory` at it here - the whole persistence contract is
verified against the same suite.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from esg_pipeline.capa import InMemoryAuditRepository, SqliteAuditRepository
from esg_pipeline.models import (
    CorrectiveAction,
    CorrectiveActionStatus,
    Finding,
    FindingSeverity,
    RiskBand,
    RiskFactor,
    RiskScore,
    SupplierAudit,
    SupplierRiskRecord,
)

RepoFactory = Callable[[], InMemoryAuditRepository | SqliteAuditRepository]


def _record(supplier_id: str = "SUP-CT1") -> SupplierRiskRecord:
    audit = SupplierAudit(
        supplier_id=supplier_id,
        supplier_name="Contract Textiles Ltd.",
        country="Vietnam",
        audit_date=date(2025, 2, 10),
        auditor="SGS",
        scheme="SLCP",
        overall_score=61,
        findings=(
            Finding("health_safety", FindingSeverity.CRITICAL, "No fire alarm in dormitory."),
            Finding("environment", FindingSeverity.MINOR, "Waste labels missing."),
        ),
    )
    risk = RiskScore(
        total=76.4,
        band=RiskBand.HIGH,
        factors=(RiskFactor("health_safety", FindingSeverity.CRITICAL, 25.0),),
    )
    return SupplierRiskRecord(
        audit=audit,
        risk=risk,
        corrective_actions=(
            CorrectiveAction(
                supplier_id=supplier_id,
                category="health_safety",
                severity=FindingSeverity.CRITICAL,
                description="No fire alarm in dormitory.",
                action="Immediate containment.",
                due_date=date(2026, 9, 9),
                status=CorrectiveActionStatus.OPEN,
            ),
        ),
        processed_at=datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc),
        source_file="SUP-CT1.pdf",
    )


@pytest.fixture(params=["memory", "sqlite"])
def repo_factory(request, tmp_path: Path) -> RepoFactory:
    if request.param == "memory":
        return InMemoryAuditRepository
    return lambda: SqliteAuditRepository(tmp_path / "contract.db")


class TestAuditRepositoryContract:
    def test_save_then_get_round_trip(self, repo_factory: RepoFactory) -> None:
        repo = repo_factory()
        original = _record()
        repo.save(original)
        loaded = repo.get("SUP-CT1")
        assert loaded is not None
        assert loaded == original
        repo.close()

    def test_get_missing_returns_none(self, repo_factory: RepoFactory) -> None:
        repo = repo_factory()
        assert repo.get("SUP-NOPE") is None
        repo.close()

    def test_all_records_returns_everything(self, repo_factory: RepoFactory) -> None:
        repo = repo_factory()
        repo.save(_record("SUP-A"))
        repo.save(_record("SUP-B"))
        repo.save(_record("SUP-C"))
        ids = {r.audit.supplier_id for r in repo.all_records()}
        assert ids == {"SUP-A", "SUP-B", "SUP-C"}
        repo.close()

    def test_save_is_idempotent_upsert(self, repo_factory: RepoFactory) -> None:
        from dataclasses import replace

        repo = repo_factory()
        repo.save(_record())
        updated = replace(_record(), risk=RiskScore(total=50.0, band=RiskBand.MEDIUM))
        repo.save(updated)
        records = repo.all_records()
        assert len(records) == 1
        assert records[0].risk.total == 50.0
        repo.close()

    def test_all_records_sorted_by_risk_desc(self, repo_factory: RepoFactory) -> None:
        repo = repo_factory()
        repo.save(_record("SUP-A"))
        repo.save(_record("SUP-B"))
        repo.save(_record("SUP-C"))
        all_ids = [r.audit.supplier_id for r in repo.all_records()]
        totals = [r.risk.total for r in repo.all_records()]
        assert totals == sorted(totals, reverse=True)
        assert set(all_ids) == {"SUP-A", "SUP-B", "SUP-C"}
        repo.close()
