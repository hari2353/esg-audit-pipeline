# ESG Supplier-Audit Intelligence Pipeline

**Public portfolio replica of an enterprise ESG compliance solution** — the kind I built at Altria: automated extraction of supplier social-compliance audit reports, supplier risk scoring, corrective-action (CAPA) tracking, and portfolio monitoring — with a Streamlit dashboard on top.

All documents are **synthetic and deterministic** (seeded generator included), so the entire pipeline runs offline with zero confidential data.

## Dashboard

![ESG Supplier Risk Dashboard](docs/dashboard.png)

*Run it yourself: `streamlit run scripts/dashboard.py -- data/results.json` (after `pip install -e .[dashboard]` and generating results).*

## What it does

```
synthetic audit PDFs ──▶ extraction ──▶ normalization ──▶ risk scoring ──▶ CAPA planning ──▶ persistence ──▶ dashboard
      (reportlab)      (pypdf / Azure     (validation +       (weighted            (severity-scaled     (SQLite +           (Streamlit +
                         Document Intel)   typed models)       deductions,          due dates via        JSON export)         plotly)
                                                              zero-tolerance        injected clock)
                                                              rules)
```

1. **Document extraction** — parses audit PDFs into structured fields + findings tables (offline `pypdf` implementation for CI/demo; an Azure AI Document Intelligence adapter is included for scanned/real-world documents — both satisfy the same `DocumentExtractor` protocol).
2. **Normalization & validation** — every field validated, all errors reported at once with actionable messages; output is a typed, frozen domain model.
3. **Risk scoring** — configurable weighted-deduction engine (severity weights, zero-tolerance categories like child/forced labor, band thresholds). Same audit always yields the same score.
4. **CAPA planning** — findings become corrective actions with severity-scaled deadlines (critical: 7 days, major: 30, minor: 60) — fully deterministic via an injected clock.
5. **Persistence** — SQLite repository with full round-trip fidelity (contract-tested), plus JSON export.
6. **Portfolio monitoring** — band distribution, open/overdue CAPAs, top risk categories, suppliers needing attention, portfolio health.

## Verified results (run locally, 12 synthetic audits, seed 42)

```
Processed 12 documents: 12 ok, 0 failed
Bands     : {'low': 4, 'medium': 1, 'high': 2, 'critical': 5}
Open CAPAs: 27 (0 overdue)
Extraction accuracy: 12/12 audits round-trip exactly equal to ground truth
```

**Test suite:** 93 tests (unit + contract + integration), 93% coverage, ruff + mypy clean, CI on Python 3.10/3.11/3.12 with a live end-to-end pipeline check.

## Design (SOLID, deliberately)

| Principle | Where |
|---|---|
| **S**ingle responsibility | `models` / `synthetic` / `extraction` / `risk` / `capa` / `pipeline` each own one concern |
| **O**pen/closed | New extractors, scorers, repositories, exporters = new class implementing a protocol; zero pipeline edits |
| **L**iskov | Repository contract tests run unchanged against both SQLite and in-memory implementations |
| **I**nterface segregation | Small protocols: `DocumentExtractor`, `RiskScorer`, `AuditRepository`, `PortfolioMonitor`, `ReportExporter` |
| **D**ependency inversion | `AuditPipeline` depends only on protocols; concrete implementations are injected at composition root |

Other deliberate choices: frozen dataclasses everywhere (immutable domain), injected `Clock` (deterministic CAPA dates in tests and demo), seeded PDF generator (byte-identical rebuilds), batch processing that never dies on one bad file.

## Quickstart

```bash
pip install -e .[dev]                # core + tests
pytest                              # 93 tests
python scripts/generate_samples.py --count 12 --out data/samples --seed 42
python scripts/run_pipeline.py --samples data/samples --db data/audit.db --json data/results.json

pip install -e .[dashboard]
streamlit run scripts/dashboard.py -- data/results.json
```

### Using Azure AI Document Intelligence

```bash
pip install -e .[azure]
export AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT="https://<resource>.cognitiveservices.azure.com"
export AZURE_DOCUMENT_INTELLIGENCE_KEY="<key>"
python scripts/run_pipeline.py ...   # auto-detects and uses the Azure extractor
```

## Repository layout

```
src/esg_pipeline/
  models.py       # typed, frozen domain models (single source of truth)
  synthetic.py    # deterministic synthetic audit PDF generator (reportlab)
  extraction.py   # DocumentExtractor protocol + pypdf + Azure adapter + normalizer
  risk.py         # RiskScorer strategy + configurable WeightedDeductionScorer
  capa.py         # CAPA planner + AuditRepository protocol + SQLite/in-memory repos
  pipeline.py     # AuditPipeline orchestrator + portfolio monitoring + JSON export
scripts/
  generate_samples.py  # generate synthetic audit PDFs (+ ground-truth JSON)
  run_pipeline.py      # end-to-end CLI run
  dashboard.py         # Streamlit dashboard
tests/
  test_models.py             # domain model validation
  test_synthetic.py         # generator determinism & consistency
  test_extraction.py        # parsing + normalization + PDF round-trip
  test_risk.py              # scoring engine, thresholds, zero-tolerance
  test_capa.py              # CAPA planning, due dates, clock injection
  test_repository_contract.py   # contract tests (SQLite + in-memory)
  test_extractor_contract.py    # extractor contract tests
  test_pipeline.py          # orchestrator + monitoring + export
  test_integration.py      # full end-to-end: PDFs -> SQLite -> JSON -> summary
```

## Why synthetic data?

Enterprise ESG work (Altria's supplier-audit program) runs on confidential supplier documents that cannot be published. This repo demonstrates the same architecture end-to-end — extraction, scoring, CAPA, monitoring — on generated documents, with the cloud extractor adapter included for real deployments.

## License

MIT
