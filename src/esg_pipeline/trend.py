"""Supplier trend scorecards — EcoVadis's core output (comparable results
over time, improvement scorecards) as pure aggregation over the audit
history the repository keeps.

Direction follows this engine's scoring semantics: HIGHER total = HIGHER
risk (the deduction engine starts at 100 and subtracts), so a NEGATIVE
delta is improving. A supplier with fewer than two audits has NO trend —
a single point cannot define one, and inventing 'stable' would be a
fabrication.
"""
from dataclasses import dataclass
from typing import Protocol

from esg_pipeline.models import AuditSummary

STABLE_EPSILON = 1.0


@dataclass(frozen=True, slots=True)
class TrendScorecard:
    latest: AuditSummary
    previous: AuditSummary
    delta: float
    direction: str  # "improving" | "declining" | "stable"
    band_changed: bool


def supplier_trend(summaries) -> TrendScorecard | None:
    """Trend across a supplier's audits, or None with fewer than two.
    The RECENT trend: only the last two dated entries count."""
    ordered = sorted(summaries, key=lambda s: s.audit_date)
    if len(ordered) < 2:
        return None
    previous, latest = ordered[-2], ordered[-1]
    delta = latest.total - previous.total
    if delta <= -STABLE_EPSILON:
        direction = "improving"
    elif delta >= STABLE_EPSILON:
        direction = "declining"
    else:
        direction = "stable"
    return TrendScorecard(
        latest=latest, previous=previous, delta=delta, direction=direction,
        band_changed=latest.band != previous.band,
    )


class _HistoryRepository(Protocol):
    def history(self, supplier_id: str) -> tuple[AuditSummary, ...]: ...


def portfolio_trends(repository: _HistoryRepository) -> dict[str, TrendScorecard]:
    """Trends for every supplier that has at least two audits."""
    # repositories expose all_records for the supplier list
    out: dict[str, TrendScorecard] = {}
    for record in getattr(repository, "all_records")():
        trend = supplier_trend(repository.history(record.audit.supplier_id))
        if trend is not None:
            out[record.audit.supplier_id] = trend
    return out
