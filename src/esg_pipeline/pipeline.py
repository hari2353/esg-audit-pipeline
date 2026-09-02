"""Portfolio monitoring and pipeline orchestration.

`AuditPipeline` wires the four stages together (extract -> normalize ->
score -> plan CAPAs -> persist) and depends only on protocols, so every
stage can be swapped/mocked independently (dependency inversion).
`PortfolioSummary` aggregates processed records into the numbers shown on
the dashboard.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Protocol

from esg_pipeline.capa import AuditRepository, CapaPlanner
from esg_pipeline.extraction import (
    DocumentExtractor,
    ExtractionError,
    normalize_extraction,
)
from esg_pipeline.models import (
    CorrectiveAction,
    CorrectiveActionStatus,
    RiskBand,
    RiskScore,
    SupplierAudit,
    SupplierRiskRecord,
)
from esg_pipeline.risk import RiskScorer

UNPARSED_SUPPLIER_NAME = "(unparsed document)"


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """Outcome of processing a single document."""

    record: SupplierRiskRecord
    ok: bool
    error: str | None = None


class AuditPipeline:
    """End-to-end processor for supplier audit documents."""

    def __init__(
        self,
        extractor: DocumentExtractor,
        scorer: RiskScorer,
        capa_planner: CapaPlanner,
        repository: AuditRepository,
    ) -> None:
        self._extractor = extractor
        self._scorer = scorer
        self._capa_planner = capa_planner
        self._repository = repository

    def process_document(self, path: Path) -> ProcessResult:
        """Process one audit PDF; failures return ok=False with a reason."""
        try:
            raw = self._extractor.extract(path)
            audit = normalize_extraction(raw)
        except ExtractionError as exc:
            placeholder = _placeholder_audit(path)
            record = SupplierRiskRecord(
                audit=placeholder, risk=_error_risk(), source_file=path.name
            )
            return ProcessResult(record=record, ok=False, error=str(exc))

        return self.process_audit(audit, source_file=path.name)

    def process_audit(
        self, audit: SupplierAudit, *, source_file: str | None = None
    ) -> ProcessResult:
        """Score, plan CAPAs and persist an already-extracted audit."""
        risk = self._scorer.score(audit)
        actions = self._capa_planner.plan(audit, risk)
        record = SupplierRiskRecord(
            audit=audit,
            risk=risk,
            corrective_actions=actions,
            processed_at=datetime.now(tz=timezone.utc),
            source_file=source_file,
        )
        self._repository.save(record)
        return ProcessResult(record=record, ok=True)

    def process_documents(self, paths: list[Path]) -> list[ProcessResult]:
        """Process many documents; one bad file never stops the batch."""
        return [self.process_document(p) for p in paths]


def _placeholder_audit(path: Path) -> SupplierAudit:
    return SupplierAudit(
        supplier_id=f"UNKNOWN-{path.stem}",
        supplier_name=UNPARSED_SUPPLIER_NAME,
        country="unknown",
        audit_date=date(1970, 1, 1),
        auditor="unknown",
        scheme="unknown",
        overall_score=0,
    )


def _error_risk() -> RiskScore:
    return RiskScore(total=100.0, band=RiskBand.CRITICAL)


@dataclass(frozen=True, slots=True)
class PortfolioSummary:
    """Aggregate portfolio health metrics for the dashboard."""

    total_suppliers: int
    failed_extractions: int
    band_counts: dict[str, int]
    open_capas: int
    overdue_capas: int
    top_risk_categories: tuple[tuple[str, int], ...]
    suppliers_requiring_attention: tuple[str, ...]

    @property
    def health(self) -> str:
        """Simple portfolio health label derived from critical share."""
        if self.total_suppliers == 0:
            return "no data"
        critical_share = self.band_counts.get(RiskBand.CRITICAL.value, 0) / self.total_suppliers
        if critical_share > 0.25:
            return "at risk"
        if critical_share > 0.10:
            return "needs review"
        return "healthy"


class PortfolioMonitor(Protocol):
    """Anything that can aggregate records into a portfolio view."""

    def summarize(self, records: list[SupplierRiskRecord]) -> PortfolioSummary: ...


class DefaultPortfolioMonitor:
    """Aggregates over repository records as of a reference date (injectable)."""

    def __init__(self, *, today: date | None = None) -> None:
        self._today = today or datetime.now(tz=timezone.utc).date()

    def summarize(self, records: list[SupplierRiskRecord]) -> PortfolioSummary:
        failed = sum(1 for r in records if r.audit.supplier_name == UNPARSED_SUPPLIER_NAME)
        bands = Counter(r.risk.band.value for r in records)
        open_capas = 0
        overdue = 0
        category_counter: Counter[str] = Counter()
        attention: list[str] = []
        for record in records:
            if record.risk.band in (RiskBand.HIGH, RiskBand.CRITICAL):
                attention.append(record.audit.supplier_id)
            for action in record.corrective_actions:
                if action.status is CorrectiveActionStatus.OPEN:
                    open_capas += 1
                    if action.due_date < self._today:
                        overdue += 1
                    category_counter[action.category] += 1
        return PortfolioSummary(
            total_suppliers=len(records),
            failed_extractions=failed,
            band_counts=dict(bands),
            open_capas=open_capas,
            overdue_capas=overdue,
            top_risk_categories=tuple(category_counter.most_common(5)),
            suppliers_requiring_attention=tuple(sorted(attention)),
        )

    def summarize_from_dicts(self, entries: list[dict]) -> PortfolioSummary:
        """Aggregate over `record_to_dict` output (dashboard/JSON path)."""
        from datetime import date as date_type

        bands: Counter[str] = Counter(
            entry["risk"]["band"] for entry in entries
        )
        open_capas = 0
        overdue = 0
        category_counter: Counter[str] = Counter()
        attention: list[str] = []
        for entry in entries:
            band = entry["risk"]["band"]
            if band in (RiskBand.HIGH.value, RiskBand.CRITICAL.value):
                attention.append(entry["supplier_id"])
            for action in entry["corrective_actions"]:
                if action["status"] == CorrectiveActionStatus.OPEN.value:
                    open_capas += 1
                    if date_type.fromisoformat(action["due_date"]) < self._today:
                        overdue += 1
                    category_counter[action["category"]] += 1
        return PortfolioSummary(
            total_suppliers=len(entries),
            failed_extractions=sum(
                1 for e in entries if e["supplier_name"] == UNPARSED_SUPPLIER_NAME
            ),
            band_counts=dict(bands),
            open_capas=open_capas,
            overdue_capas=overdue,
            top_risk_categories=tuple(category_counter.most_common(5)),
            suppliers_requiring_attention=tuple(sorted(attention)),
        )


class ReportExporter(Protocol):
    """Export sink for pipeline results (JSON, CSV, SharePoint, ...)."""

    def export(self, records: list[SupplierRiskRecord]) -> Path: ...


class JsonReportExporter:
    """Writes all records to a single JSON file for the dashboard/tests."""

    def __init__(self, out_path: Path) -> None:
        self._out_path = Path(out_path)

    def export(self, records: list[SupplierRiskRecord]) -> Path:
        payload = [record_to_dict(r) for r in records]
        self._out_path.parent.mkdir(parents=True, exist_ok=True)
        with self._out_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        return self._out_path


def record_to_dict(record: SupplierRiskRecord) -> dict:
    """Serialize one record to a JSON-friendly dict (dashboard contract)."""

    def capa_to_dict(a: CorrectiveAction) -> dict:
        return {
            "category": a.category,
            "severity": a.severity.value,
            "action": a.action,
            "due_date": a.due_date.isoformat(),
            "status": a.status.value,
        }

    a = record.audit
    return {
        "supplier_id": a.supplier_id,
        "supplier_name": a.supplier_name,
        "country": a.country,
        "audit_date": a.audit_date.isoformat(),
        "scheme": a.scheme,
        "overall_score": a.overall_score,
        "findings": [
            {"category": f.category, "severity": f.severity.value, "description": f.description}
            for f in a.findings
        ],
        "risk": {
            "total": record.risk.total,
            "band": record.risk.band.value,
            "factors": [
                {"category": fa.category, "severity": fa.severity.value, "deduction": fa.deduction}
                for fa in record.risk.factors
            ],
        },
        "corrective_actions": [capa_to_dict(ca) for ca in record.corrective_actions],
        "processed_at": record.processed_at.isoformat() if record.processed_at else None,
        "source_file": record.source_file,
    }
