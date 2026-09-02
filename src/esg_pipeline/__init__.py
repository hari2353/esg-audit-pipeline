"""Package init - public API surface for the ESG pipeline."""

from esg_pipeline.capa import (
    AuditRepository,
    CapaPlanner,
    CapaPolicy,
    InMemoryAuditRepository,
    SqliteAuditRepository,
)
from esg_pipeline.extraction import (
    AzureDocIntelligenceExtractor,
    DocumentExtractor,
    ExtractionError,
    PyPdfExtractor,
    RawExtraction,
    normalize_extraction,
)
from esg_pipeline.models import (
    CorrectiveAction,
    CorrectiveActionStatus,
    Finding,
    FindingSeverity,
    RiskBand,
    RiskScore,
    SupplierAudit,
    SupplierRiskRecord,
)
from esg_pipeline.pipeline import (
    AuditPipeline,
    DefaultPortfolioMonitor,
    JsonReportExporter,
    PortfolioSummary,
    ProcessResult,
    record_to_dict,
)
from esg_pipeline.risk import RiskScorer, ScoringConfig, WeightedDeductionScorer
from esg_pipeline.synthetic import generate_audit_pdfs, generate_profile, render_audit_pdf

__all__ = [
    "AuditPipeline",
    "AuditRepository",
    "AzureDocIntelligenceExtractor",
    "CapaPlanner",
    "CapaPolicy",
    "CorrectiveAction",
    "CorrectiveActionStatus",
    "DefaultPortfolioMonitor",
    "DocumentExtractor",
    "ExtractionError",
    "Finding",
    "FindingSeverity",
    "InMemoryAuditRepository",
    "JsonReportExporter",
    "PortfolioSummary",
    "ProcessResult",
    "PyPdfExtractor",
    "RawExtraction",
    "RiskBand",
    "RiskScore",
    "RiskScorer",
    "ScoringConfig",
    "SqliteAuditRepository",
    "SupplierAudit",
    "SupplierRiskRecord",
    "WeightedDeductionScorer",
    "generate_audit_pdfs",
    "generate_profile",
    "normalize_extraction",
    "record_to_dict",
    "render_audit_pdf",
]
