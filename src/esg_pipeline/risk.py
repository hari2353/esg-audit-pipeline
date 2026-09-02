"""Risk scoring engine.

Suppliers start at 100 (low risk) and points are deducted per finding:

* per-severity deductions (configurable), with zero-tolerance categories
  (child/forced labor) always treated as critical;
* a base score component derived from the audit's overall compliance score,
  so both the narrative findings and the auditor's score are reflected.

The engine is configuration-driven: thresholds and weights live in
`ScoringConfig` and can be supplied per tenant/market without code changes
(open/closed principle). `RiskScorer` is the strategy interface; the default
implementation is `WeightedDeductionScorer`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Protocol

from esg_pipeline.models import (
    ZERO_TOLERANCE_CATEGORIES,
    FindingSeverity,
    RiskBand,
    RiskFactor,
    RiskScore,
    SupplierAudit,
)


class RiskScorer(Protocol):
    """Strategy interface: score one audit into a `RiskScore`."""

    def score(self, audit: SupplierAudit) -> RiskScore: ...


@dataclass(frozen=True, slots=True)
class ScoringConfig:
    """Weights and thresholds driving the default scorer.

    Attributes:
        severity_deductions: points removed per finding, by severity.
        zero_tolerance_deduction: flat deduction for any zero-tolerance
            finding (applied in addition to the severity deduction).
        base_score_weight: how much of the audit's overall score carries
            over before deductions (0.0 = ignore, 1.0 = fully carry over).
        band_thresholds: total-score upper bounds per band; a total <=
            threshold maps to that band, checked from low to critical.
    """

    severity_deductions: dict[FindingSeverity, float] = field(
        default_factory=lambda: {
            FindingSeverity.MINOR: 4.0,
            FindingSeverity.MAJOR: 12.0,
            FindingSeverity.CRITICAL: 25.0,
        }
    )
    zero_tolerance_deduction: float = 15.0
    base_score_weight: float = 0.6
    band_thresholds: dict[RiskBand, float] = field(
        default_factory=lambda: {
            RiskBand.LOW: 15.0,
            RiskBand.MEDIUM: 35.0,
            RiskBand.HIGH: 60.0,
        }
    )

    def __post_init__(self) -> None:
        if set(self.severity_deductions) != set(FindingSeverity):
            msg = "severity_deductions must cover all severities"
            raise ValueError(msg)
        if not 0.0 <= self.base_score_weight <= 1.0:
            msg = "base_score_weight must be in [0, 1]"
            raise ValueError(msg)
        if self.zero_tolerance_deduction < 0:
            msg = "zero_tolerance_deduction must be >= 0"
            raise ValueError(msg)
        missing = {RiskBand.LOW, RiskBand.MEDIUM, RiskBand.HIGH} - set(self.band_thresholds)
        if missing:
            msg = f"band_thresholds missing: {sorted(b.value for b in missing)}"
            raise ValueError(msg)
        ordered = [
            self.band_thresholds[b]
            for b in (RiskBand.LOW, RiskBand.MEDIUM, RiskBand.HIGH)
        ]
        if ordered != sorted(ordered):
            msg = "band thresholds must be ascending low < medium < high"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class WeightedDeductionScorer:
    """Default scorer: weighted deductions over a score-derived base."""

    config: ScoringConfig = field(default_factory=ScoringConfig)

    def score(self, audit: SupplierAudit) -> RiskScore:
        cfg = self.config
        base = (100 - audit.overall_score) * cfg.base_score_weight

        factors: list[RiskFactor] = []
        total = base
        if base > 0:
            factors.append(
                RiskFactor(
                    category="audit_base",
                    severity=FindingSeverity.MINOR,
                    deduction=round(base, 2),
                )
            )

        for finding in audit.findings:
            severity = (
                FindingSeverity.CRITICAL
                if finding.category in ZERO_TOLERANCE_CATEGORIES
                and finding.severity is not FindingSeverity.CRITICAL
                else finding.severity
            )
            deduction = cfg.severity_deductions[severity]
            if finding.category in ZERO_TOLERANCE_CATEGORIES:
                deduction += cfg.zero_tolerance_deduction
            total += deduction
            factors.append(
                RiskFactor(
                    category=finding.category,
                    severity=severity,
                    deduction=round(deduction, 2),
                )
            )

        total = min(100.0, max(0.0, round(total, 2)))
        return RiskScore(total=total, band=_band_for(cfg, total), factors=tuple(factors))


def _band_for(cfg: ScoringConfig, total: float) -> RiskBand:
    if total <= cfg.band_thresholds[RiskBand.LOW]:
        return RiskBand.LOW
    if total <= cfg.band_thresholds[RiskBand.MEDIUM]:
        return RiskBand.MEDIUM
    if total <= cfg.band_thresholds[RiskBand.HIGH]:
        return RiskBand.HIGH
    return RiskBand.CRITICAL


def with_overrides(config: ScoringConfig, **changes: object) -> ScoringConfig:
    """Return a copy of `config` with given fields replaced (immutability helper)."""
    return replace(config, **changes)  # type: ignore[arg-type]
