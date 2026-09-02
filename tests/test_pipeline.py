"""Unit tests for the pipeline orchestrator and portfolio monitoring."""

from __future__ import annotations

from datetime import date, timezone
from pathlib import Path

import pytest

from esg_pipeline.capa import CapaPlanner, InMemoryAuditRepository
from esg_pipeline.models import RiskBand, SupplierRiskRecord
from esg_pipeline.pipeline import (
    AuditPipeline,
    DefaultPortfolioMonitor,
    JsonReportExporter,
    record_to_dict,
)
from esg_pipeline.risk import WeightedDeductionScorer
from tests.test_repository_contract import _record


@pytest.fixture
def pipeline() -> AuditPipeline:
    from esg_pipeline.extraction import PyPdfExtractor

    return AuditPipeline(
        extractor=PyPdfExtractor(),
        scorer=WeightedDeductionScorer(),
        capa_planner=CapaPlanner(clock=lambda: date(2026, 9, 2)),
        repository=InMemoryAuditRepository(),
    )


class TestAuditPipeline:
    def test_process_document_happy_path(self, pipeline: AuditPipeline, pdf_dir: Path) -> None:
        path = sorted(pdf_dir.glob("*.pdf"))[0]
        result = pipeline.process_document(path)
        assert result.ok
        assert result.record.risk.band in RiskBand
        assert result.record.corrective_actions is not None
        assert result.record.source_file == path.name

    def test_process_documents_resilient_to_bad_files(
        self, pipeline: AuditPipeline, pdf_dir: Path, tmp_path: Path
    ) -> None:
        bad = tmp_path / "broken.pdf"
        bad.write_bytes(b"not a pdf at all")
        paths = [*sorted(pdf_dir.glob("*.pdf")), bad]
        results = pipeline.process_documents(paths)
        assert len(results) == len(paths)
        ok = [r for r in results if r.ok]
        failed = [r for r in results if not r.ok]
        assert len(ok) == len(paths) - 1
        assert len(failed) == 1
        assert failed[0].record.source_file == "broken.pdf"
        assert failed[0].error and "failed to read PDF" in failed[0].error

    def test_processed_records_saved_to_repository(
        self, pipeline: AuditPipeline, pdf_dir: Path
    ) -> None:
        paths = sorted(pdf_dir.glob("*.pdf"))
        results = pipeline.process_documents(paths)
        repo = pipeline._repository
        saved = {r.audit.supplier_id for r in repo.all_records()}
        expected = {r.record.audit.supplier_id for r in results if r.ok}
        assert saved == expected

    def test_record_timestamped(self, pipeline: AuditPipeline, pdf_dir: Path) -> None:
        result = pipeline.process_document(sorted(pdf_dir.glob("*.pdf"))[0])
        assert result.record.processed_at is not None
        assert result.record.processed_at.tzinfo is timezone.utc


class TestPortfolioMonitor:
    def test_empty_portfolio(self) -> None:
        summary = DefaultPortfolioMonitor(today=date(2026, 9, 2)).summarize([])
        assert summary.total_suppliers == 0
        assert summary.health == "no data"
        assert summary.open_capas == 0

    def test_summary_counts(self) -> None:
        records = [_record("SUP-A"), _record("SUP-B"), _record("SUP-C")]
        monitor = DefaultPortfolioMonitor(today=date(2026, 9, 2))
        summary = monitor.summarize(records)
        assert summary.total_suppliers == 3
        assert summary.band_counts == {RiskBand.HIGH.value: 3}
        assert summary.open_capas == 3  # one CAPA per record
        assert summary.overdue_capas == 0  # due 2026-09-09 > today 2026-09-02
        assert set(summary.suppliers_requiring_attention) == {"SUP-A", "SUP-B", "SUP-C"}

    def test_overdue_detection(self) -> None:
        records = [_record("SUP-A")]
        # due 2026-09-09; evaluate as of 2026-10-01 -> overdue
        monitor = DefaultPortfolioMonitor(today=date(2026, 10, 1))
        summary = monitor.summarize(records)
        assert summary.overdue_capas == 1

    def test_failed_extraction_counted(self) -> None:
        from esg_pipeline.pipeline import _error_risk, _placeholder_audit

        placeholder = _placeholder_audit(Path("x.pdf"))
        record = SupplierRiskRecord(audit=placeholder, risk=_error_risk())
        monitor = DefaultPortfolioMonitor(today=date(2026, 9, 2))
        summary = monitor.summarize([record])
        assert summary.failed_extractions == 1

    def test_summarize_from_dicts_agrees_with_summarize(self) -> None:
        """The dashboard (JSON) path must show the same numbers as the core path."""
        records = [_record("SUP-A"), _record("SUP-B"), _record("SUP-C")]
        monitor = DefaultPortfolioMonitor(today=date(2026, 9, 2))
        from_dicts = monitor.summarize_from_dicts([record_to_dict(r) for r in records])
        assert from_dicts == monitor.summarize(records)

    def test_health_thresholds(self) -> None:
        records = [_record(f"SUP-{i:03d}") for i in range(8)]
        monitor = DefaultPortfolioMonitor(today=date(2026, 9, 2))
        summary = monitor.summarize(records)
        # 8/8 critical (record risk is HIGH band) -> needs attention list non-empty
        assert summary.health in {"healthy", "needs review", "at risk"}

    def test_health_labels(self) -> None:
        from dataclasses import replace

        from esg_pipeline.models import RiskScore

        monitor = DefaultPortfolioMonitor(today=date(2026, 9, 2))
        # all low risk -> healthy
        low = [
            replace(_record(f"SUP-L{i}"), risk=RiskScore(total=5.0, band=RiskBand.LOW))
            for i in range(10)
        ]
        assert monitor.summarize(low).health == "healthy"
        # 2/10 critical -> needs review
        critical = [
            replace(_record(f"SUP-C{i}"), risk=RiskScore(total=90.0, band=RiskBand.CRITICAL))
            for i in range(2)
        ]
        assert monitor.summarize(low[:8] + critical).health == "needs review"
        # 5/10 critical -> at risk
        assert monitor.summarize(low[:5] + critical + critical[:3]).health == "at risk"


class TestJsonExport:
    def test_export_round_trips(self, tmp_path: Path) -> None:
        import json

        records = [_record("SUP-X"), _record("SUP-Y")]
        out = tmp_path / "results.json"
        JsonReportExporter(out).export(records)
        data = json.loads(out.read_text(encoding="utf-8"))
        assert len(data) == 2
        assert {d["supplier_id"] for d in data} == {"SUP-X", "SUP-Y"}
        first = data[0]
        assert first["risk"]["band"] == "high"
        assert first["corrective_actions"][0]["status"] == "open"

    def test_record_to_dict_shape(self) -> None:
        d = record_to_dict(_record("SUP-Z"))
        for key in (
            "supplier_id", "supplier_name", "country", "audit_date", "scheme",
            "overall_score", "findings", "risk", "corrective_actions", "processed_at",
        ):
            assert key in d, f"missing {key}"
        for key in ("total", "band", "factors"):
            assert key in d["risk"]
