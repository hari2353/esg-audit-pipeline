"""Alerts — Avetta's workflow triggers ("system-driven alerts when
compliance statuses change") and expiry tracking, as three deterministic
evaluations over repository state and an injected reference date:

- capa_overdue: a CAPA past its due date that is not VERIFIED (closure is
  the only non-overdue state). Zero-tolerance categories (child/forced
  labor) alert at critical severity, everything else at warning.
- band_escalation: the supplier's band worsened between its last two
  audits (from the repository history).
- reaudit_due: the latest audit is older than the validity window.

No wall clock, no network, one alert per condition — never duplicates.
"""
from dataclasses import dataclass
from datetime import date, timedelta

from esg_pipeline.models import CorrectiveActionStatus, RiskBand
from esg_pipeline.trend import supplier_trend

ZERO_TOLERANCE_CATEGORIES = frozenset({"child_labor", "forced_labor"})

# band severity order: later enum declaration = riskier
BAND_RANK = {band.value: rank for rank, band in enumerate(RiskBand)}


@dataclass(frozen=True, slots=True)
class Alert:
    kind: str  # capa_overdue | band_escalation | reaudit_due
    supplier_id: str
    detail: str
    severity: str  # "info" | "warning" | "critical"


def evaluate_alerts(
    records,
    *,
    today: date,
    history_repo=None,
    reaudit_window_days: int = 365,
) -> tuple[Alert, ...]:
    """Evaluate all alert kinds over the current records. `today` is
    injected (deterministic); `history_repo` (optional) supplies audit
    history for band-escalation detection."""
    alerts: list[Alert] = []
    seen: set[tuple[str, str, str]] = set()

    def emit(kind: str, supplier_id: str, condition: str, detail: str, severity: str) -> None:
        key = (kind, supplier_id, condition)
        if key in seen:
            return
        seen.add(key)
        alerts.append(Alert(kind, supplier_id, detail, severity))

    for record in records:
        supplier_id = record.audit.supplier_id
        for action in record.corrective_actions:
            if action.status is CorrectiveActionStatus.VERIFIED:
                continue  # closure ends the overdue conversation
            if action.due_date < today:
                severity = (
                    "critical" if action.category in ZERO_TOLERANCE_CATEGORIES
                    else "warning"
                )
                emit(
                    "capa_overdue", supplier_id, action.category,
                    f"{action.category} CAPA due {action.due_date.isoformat()}"
                    f" (status {action.status.value})",
                    severity,
                )
        if history_repo is not None:
            trend = supplier_trend(history_repo.history(supplier_id))
            if (
                trend is not None
                and trend.band_changed
                and BAND_RANK[trend.latest.band] > BAND_RANK[trend.previous.band]
            ):
                emit(
                    "band_escalation", supplier_id, "band",
                    f"band moved {trend.previous.band} -> {trend.latest.band}"
                    f" (total {trend.previous.total:.1f} -> {trend.latest.total:.1f})",
                    "critical",
                )
        if record.audit.audit_date + timedelta(days=reaudit_window_days) < today:
            emit(
                "reaudit_due", supplier_id, "audit_age",
                f"last audit {record.audit.audit_date.isoformat()},"
                f" older than {reaudit_window_days} days",
                "info",
            )
    return tuple(alerts)
