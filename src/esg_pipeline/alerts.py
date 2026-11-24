"""Alerts — Avetta's workflow triggers ("system-driven alerts when
compliance statuses change") and expiry tracking, as three deterministic
evaluations over repository state and an injected reference date:

- capa_overdue: CAPAs past their due date in non-terminal states
  (VERIFIED = lifecycle closure, CLOSED = the legacy terminal value).
  One alert per (supplier, category) carries the count; zero-tolerance
  categories (child/forced labor) alert at critical severity.
- band_escalation: the supplier's band worsened between its last two
  audits (from the repository history). Unknown band strings rank lowest
  — never a crash.
- reaudit_due: the latest audit is older than the validity window.
  Failed-extraction placeholder records are excluded (they have no real
  audit date to age).

No wall clock, no network, one alert per condition — never duplicates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Protocol

from esg_pipeline.models import (
    ZERO_TOLERANCE_CATEGORIES,
    CorrectiveActionStatus,
    SupplierRiskRecord,
)
from esg_pipeline.pipeline import UNPARSED_SUPPLIER_NAME
from esg_pipeline.risk import BAND_RANK
from esg_pipeline.trend import supplier_trend

if TYPE_CHECKING:
    from collections.abc import Iterable

    from esg_pipeline.models import AuditSummary

TERMINAL_CAPA_STATES = (
    CorrectiveActionStatus.VERIFIED,
    CorrectiveActionStatus.CLOSED,
)


class HistorySource(Protocol):
    def history(self, supplier_id: str) -> tuple[AuditSummary, ...]: ...


@dataclass(frozen=True, slots=True)
class Alert:
    kind: str  # capa_overdue | band_escalation | reaudit_due
    supplier_id: str
    detail: str
    severity: str  # "info" | "warning" | "critical"


def evaluate_alerts(
    records: Iterable[SupplierRiskRecord],
    *,
    today: date,
    history_repo: HistorySource | None = None,
    reaudit_window_days: int = 365,
) -> tuple[Alert, ...]:
    """Evaluate all alert kinds over the current records. `today` is
    injected (deterministic); `history_repo` (optional) supplies audit
    history for band-escalation detection."""
    alerts: list[Alert] = []
    emitted: set[tuple[str, str, str]] = set()

    def emit(kind: str, supplier_id: str, condition: str, detail: str, severity: str) -> None:
        key = (kind, supplier_id, condition)
        if key in emitted:
            return
        emitted.add(key)
        alerts.append(Alert(kind, supplier_id, detail, severity))

    for record in records:
        supplier_id = record.audit.supplier_id
        # group overdue CAPAs by category: one alert, visible count
        overdue_by_category: dict[str, list] = {}
        for action in record.corrective_actions:
            if action.status in TERMINAL_CAPA_STATES:
                continue  # closure (or legacy closed) ends the conversation
            if action.due_date < today:
                overdue_by_category.setdefault(action.category, []).append(action)
        for category, actions in overdue_by_category.items():
            severity = (
                "critical"
                if any(a.category in ZERO_TOLERANCE_CATEGORIES for a in actions)
                else "warning"
            )
            earliest = min(a.due_date for a in actions)
            emit(
                "capa_overdue",
                supplier_id,
                category,
                f"{len(actions)} overdue CAPA{'s' if len(actions) != 1 else ''}"
                f" in {category}, earliest due {earliest.isoformat()}"
                f" (status {actions[0].status.value})",
                severity,
            )
        if history_repo is not None:
            trend = supplier_trend(history_repo.history(supplier_id))
            if (
                trend is not None
                and trend.band_changed
                and BAND_RANK.get(trend.latest.band, -1) > BAND_RANK.get(trend.previous.band, -1)
            ):
                emit(
                    "band_escalation",
                    supplier_id,
                    "band",
                    f"band moved {trend.previous.band} -> {trend.latest.band}"
                    f" (total {trend.previous.total:.1f} -> {trend.latest.total:.1f})",
                    "critical",
                )
        if (
            record.audit.supplier_name != UNPARSED_SUPPLIER_NAME
            and record.audit.audit_date + timedelta(days=reaudit_window_days) < today
        ):
            emit(
                "reaudit_due",
                supplier_id,
                "audit_age",
                f"last audit {record.audit.audit_date.isoformat()},"
                f" older than {reaudit_window_days} days",
                "info",
            )
    return tuple(alerts)


def build_insights(
    repository: HistorySource,
    records: Iterable[SupplierRiskRecord],
    *,
    today: date,
    reaudit_window_days: int = 365,
) -> dict:
    """The dashboard's insights payload: severity-ordered alerts and
    per-supplier trends, all bounded and JSON-friendly."""
    from dataclasses import asdict

    from esg_pipeline.trend import portfolio_trends

    records = list(records)
    alerts = evaluate_alerts(
        records,
        today=today,
        history_repo=repository,
        reaudit_window_days=reaudit_window_days,
    )
    order = {"critical": 0, "warning": 1, "info": 2}
    trends = portfolio_trends(repository)  # type: ignore[arg-type]
    return {
        "alerts": [
            {
                "kind": a.kind,
                "supplier_id": a.supplier_id,
                "detail": a.detail,
                "severity": a.severity,
            }
            for a in sorted(alerts, key=lambda a: order[a.severity])
        ],
        "trends": {supplier_id: asdict(trend) for supplier_id, trend in trends.items()},
    }
