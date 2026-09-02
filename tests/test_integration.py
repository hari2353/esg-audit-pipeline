"""Integration tests: full pipeline over generated PDFs -> SQLite -> JSON -> summary.

These run the exact same code paths as `scripts/run_pipeline.py` (marked
`integration`). They require no network, no Azure credentials, no randomness
(deterministic seed) and clean up after themselves.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def pipeline_run(tmp_path: Path):
    """Run the whole pipeline over 8 synthetic audits; return artifacts."""
    from esg_pipeline.capa import CapaPlanner, SqliteAuditRepository
    from esg_pipeline.extraction import PyPdfExtractor
    from esg_pipeline.pipeline import (
        AuditPipeline,
        DefaultPortfolioMonitor,
        JsonReportExporter,
    )
    from esg_pipeline.risk import WeightedDeductionScorer
    from esg_pipeline.synthetic import generate_audit_pdfs

    samples = tmp_path / "samples"
    generate_audit_pdfs(samples, count=8, seed=99)

    db_path = tmp_path / "audit.db"
    json_path = tmp_path / "results.json"
    repo = SqliteAuditRepository(db_path)
    pipeline = AuditPipeline(
        extractor=PyPdfExtractor(),
        scorer=WeightedDeductionScorer(),
        capa_planner=CapaPlanner(clock=lambda: date(2026, 9, 2)),
        repository=repo,
    )
    results = pipeline.process_documents(sorted(samples.glob("*.pdf")))
    JsonReportExporter(json_path).export([r.record for r in results])
    summary = DefaultPortfolioMonitor(today=date(2026, 9, 2)).summarize(
        [r.record for r in results]
    )
    repo.close()
    return {"results": results, "db": db_path, "json": json_path, "summary": summary}


class TestEndToEnd:
    def test_all_documents_processed_successfully(self, pipeline_run) -> None:
        results = pipeline_run["results"]
        assert len(results) == 8
        assert all(r.ok for r in results), [
            f"{r.record.source_file}: {r.error}" for r in results if not r.ok
        ]

    def test_sqlite_persistence_round_trip(self, pipeline_run, tmp_path: Path) -> None:
        from esg_pipeline.capa import SqliteAuditRepository

        db: Path = pipeline_run["db"]
        assert db.exists()
        repo = SqliteAuditRepository(db)
        records = repo.all_records()
        repo.close()
        assert len(records) == 8
        by_id = {r.audit.supplier_id: r for r in records}
        processed = {r.record.audit.supplier_id: r.record for r in pipeline_run["results"]}
        for supplier_id, original in processed.items():
            persisted = by_id[supplier_id]
            assert persisted.audit == original.audit
            assert persisted.risk.total == pytest.approx(original.risk.total)
            assert persisted.risk.band is original.risk.band
            assert persisted.corrective_actions == original.corrective_actions

    def test_json_export_consistent_with_db(self, pipeline_run) -> None:
        import json

        from esg_pipeline.capa import SqliteAuditRepository

        payload = json.loads(pipeline_run["json"].read_text(encoding="utf-8"))
        assert len(payload) == 8
        repo = SqliteAuditRepository(pipeline_run["db"])
        db_records = {r.audit.supplier_id: r for r in repo.all_records()}
        repo.close()
        for entry in payload:
            record = db_records[entry["supplier_id"]]
            assert entry["risk"]["total"] == pytest.approx(record.risk.total)
            assert entry["risk"]["band"] == record.risk.band.value
            assert len(entry["corrective_actions"]) == len(record.corrective_actions)

    def test_portfolio_summary_agrees_with_records(self, pipeline_run) -> None:
        summary = pipeline_run["summary"]
        results = pipeline_run["results"]
        assert summary.total_suppliers == len(results) == 8
        assert summary.failed_extractions == 0
        expected_open = sum(len(r.record.corrective_actions) for r in results)
        assert summary.open_capas == expected_open

    def test_every_audit_has_band_and_reasonable_score(self, pipeline_run) -> None:
        for r in pipeline_run["results"]:
            assert 0.0 <= r.record.risk.total <= 100.0
            assert r.record.risk.band.value in {"low", "medium", "high", "critical"}
            # deterministic seed: same audit -> same score on rerun
            from esg_pipeline.risk import WeightedDeductionScorer

            rerun = WeightedDeductionScorer().score(r.record.audit)
            assert rerun.total == pytest.approx(r.record.risk.total)
            assert rerun.band is r.record.risk.band

    def test_capa_due_dates_respect_policy(self, pipeline_run) -> None:
        from datetime import timedelta

        from esg_pipeline.models import FindingSeverity

        policy_days = {
            FindingSeverity.MINOR: 60,
            FindingSeverity.MAJOR: 30,
            FindingSeverity.CRITICAL: 7,
        }
        today = date(2026, 9, 2)
        for r in pipeline_run["results"]:
            for action in r.record.corrective_actions:
                expected = today + timedelta(days=policy_days[action.severity])
                assert action.due_date == expected

    def test_repository_reopen_after_close(self, pipeline_run) -> None:
        """SQLite file must be fully written even after the first handle closes."""
        from esg_pipeline.capa import SqliteAuditRepository

        repo = SqliteAuditRepository(pipeline_run["db"])
        assert len(repo.all_records()) == 8
        assert repo.get(next(iter(repo.all_records())).audit.supplier_id) is not None
        repo.close()
