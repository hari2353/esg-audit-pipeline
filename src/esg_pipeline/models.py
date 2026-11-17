"""Domain models for the ESG supplier-audit pipeline.

These models are the core of the system (the "S" in SOLID): every other
module depends on these types, never the other way around.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum


class FindingSeverity(str, Enum):
    """Severity of a compliance finding raised during an audit."""

    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class RiskBand(str, Enum):
    """Portfolio-level risk band assigned after scoring."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CorrectiveActionStatus(str, Enum):
    """Lifecycle state of a corrective and preventive action (CAPA).

    Sedex-CAR style: OPEN -> IN_PROGRESS (work started, progress recorded)
    -> RESOLVED (supplier claims done) -> VERIFIED (auditor confirmed;
    terminal). A rejected verification reopens to IN_PROGRESS — progress
    already made is never erased.
    """

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    VERIFIED = "verified"
    CLOSED = "closed"


# Categories follow common social-compliance audit schemes (e.g. SMETA/SA8000 style).
FINDING_CATEGORIES = (
    "child_labor",
    "forced_labor",
    "health_safety",
    "working_hours",
    "wages_benefits",
    "discrimination",
    "disciplinary_practices",
    "freedom_of_association",
    "environment",
)

ZERO_TOLERANCE_CATEGORIES = frozenset({"child_labor", "forced_labor"})


@dataclass(frozen=True, slots=True)
class Finding:
    """A single non-compliance finding from an audit report."""

    category: str
    severity: FindingSeverity
    description: str

    def __post_init__(self) -> None:
        if self.category not in FINDING_CATEGORIES:
            msg = f"unknown finding category: {self.category!r}"
            raise ValueError(msg)
        if not self.description or not self.description.strip():
            msg = "finding description must be non-empty"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class SupplierAudit:
    """Structured, normalized result of extracting one audit report."""

    supplier_id: str
    supplier_name: str
    country: str
    audit_date: date
    auditor: str
    scheme: str
    overall_score: int
    findings: tuple[Finding, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.supplier_id or not self.supplier_id.strip():
            msg = "supplier_id must be non-empty"
            raise ValueError(msg)
        if not self.supplier_name or not self.supplier_name.strip():
            msg = "supplier_name must be non-empty"
            raise ValueError(msg)
        if not 0 <= self.overall_score <= 100:
            msg = f"overall_score must be in [0, 100], got {self.overall_score}"
            raise ValueError(msg)

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity is FindingSeverity.CRITICAL)

    @property
    def major_count(self) -> int:
        return sum(1 for f in self.findings if f.severity is FindingSeverity.MAJOR)


@dataclass(frozen=True, slots=True)
class RiskFactor:
    """One weighted contribution to a supplier's total risk score."""

    category: str
    severity: FindingSeverity
    deduction: float


@dataclass(frozen=True, slots=True)
class RiskScore:
    """Outcome of risk scoring for one supplier audit."""

    total: float
    band: RiskBand
    factors: tuple[RiskFactor, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not 0.0 <= self.total <= 100.0:
            msg = f"risk total must be in [0, 100], got {self.total}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class ProgressEntry:
    """One recorded step in a CAPA's remediation (Sedex CAR: suppliers
    record progress and blockers; auditors verify each step)."""

    kind: str  # started | note | resolved | verified | rejected
    note: str
    at: datetime


@dataclass(frozen=True, slots=True)
class CorrectiveAction:
    """A corrective and preventive action raised against a finding."""

    supplier_id: str
    category: str
    severity: FindingSeverity
    description: str
    action: str
    due_date: date
    status: CorrectiveActionStatus = CorrectiveActionStatus.OPEN
    progress_log: tuple[ProgressEntry, ...] = ()
    resolved_at: datetime | None = None
    verified_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AuditSummary:
    """One point in a supplier's audit history (trend input)."""

    audit_date: date
    total: float
    band: str


@dataclass(frozen=True, slots=True)
class SupplierRiskRecord:
    """Everything the pipeline knows about one supplier after processing."""

    audit: SupplierAudit
    risk: RiskScore
    corrective_actions: tuple[CorrectiveAction, ...] = field(default_factory=tuple)
    processed_at: datetime | None = None
    source_file: str | None = None
