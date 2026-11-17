"""Run the ESG pipeline over a folder of audit PDFs.

Usage:
    python scripts/run_pipeline.py --samples data/samples
        --db data/audit.db --json data/results.json

Set AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT / _KEY to use the Azure extractor;
otherwise the offline pypdf extractor is used.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from esg_pipeline.capa import CapaPlanner, InMemoryAuditRepository, SqliteAuditRepository
from esg_pipeline.extraction import PyPdfExtractor
from esg_pipeline.pipeline import (
    AuditPipeline,
    DefaultPortfolioMonitor,
    JsonReportExporter,
)
from esg_pipeline.risk import WeightedDeductionScorer


def build_extractor():
    endpoint = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
    key = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY")
    if endpoint and key:
        from esg_pipeline.extraction import AzureDocIntelligenceExtractor

        print("Using Azure AI Document Intelligence extractor")
        return AzureDocIntelligenceExtractor(endpoint=endpoint, key=key)
    print("Using offline pypdf extractor")
    return PyPdfExtractor()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, default=Path("data/samples"))
    parser.add_argument("--db", type=Path, default=Path("data/audit.db"))
    parser.add_argument("--json", type=Path, default=Path("data/results.json"))
    parser.add_argument("--insights", type=Path, default=Path("data/insights.json"),
                        help="alerts + trends payload for the dashboard")
    parser.add_argument("--memory", action="store_true", help="skip persistence (dry run)")
    args = parser.parse_args()

    paths = sorted(args.samples.glob("*.pdf"))
    if not paths:
        raise SystemExit(f"no PDFs found in {args.samples}")

    repository = InMemoryAuditRepository() if args.memory else SqliteAuditRepository(args.db)
    pipeline = AuditPipeline(
        extractor=build_extractor(),
        scorer=WeightedDeductionScorer(),
        capa_planner=CapaPlanner(),
        repository=repository,
    )

    results = pipeline.process_documents(paths)
    ok = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]

    exporter = JsonReportExporter(args.json)
    exporter.export([r.record for r in results])

    from esg_pipeline.alerts import build_insights

    insights = build_insights(
        repository,
        [r.record for r in results],
        today=datetime.now(tz=timezone.utc).date(),
    )
    args.insights.parent.mkdir(parents=True, exist_ok=True)
    args.insights.write_text(json.dumps(insights, indent=2, default=str))

    monitor = DefaultPortfolioMonitor()
    summary = monitor.summarize([r.record for r in results])

    print()
    print(f"Processed {len(results)} documents: {len(ok)} ok, {len(failed)} failed")
    print(f"Results JSON : {args.json}")
    print(f"Database     : {'(in-memory)' if args.memory else args.db}")
    print()
    print("== Portfolio Summary ==")
    print(f"Health                 : {summary.health}")
    print(f"Suppliers              : {summary.total_suppliers}")
    print(f"Failed extractions     : {summary.failed_extractions}")
    print(f"Bands                  : {summary.band_counts}")
    print(f"Open CAPAs             : {summary.open_capas} ({summary.overdue_capas} overdue)")
    print(f"Top risk categories    : {summary.top_risk_categories}")
    if summary.suppliers_requiring_attention:
        print(f"Needs attention         : {', '.join(summary.suppliers_requiring_attention)}")

    # Insights (from the same payload the dashboard reads)
    trends = insights["trends"]
    if trends:
        print()
        print("== Supplier Trends ==")
        for supplier_id, trend in sorted(
            trends.items(), key=lambda kv: -kv[1]["delta"]
        )[:5]:
            print(
                f"{supplier_id}: {trend['previous']['band']} ->"
                f" {trend['latest']['band']}"
                f" ({trend['direction']}, delta {trend['delta']:+.1f})"
            )
    if insights["alerts"]:
        print()
        print("== Alerts ==")
        for alert in insights["alerts"]:
            print(f"[{alert['severity'].upper():8}] {alert['supplier_id']}: {alert['detail']}")
    print(f"Insights JSON : {args.insights}")

    if failed:
        print()
        for r in failed:
            print(f"FAILED {r.record.source_file}: {r.error}")


if __name__ == "__main__":
    main()
