"""Corrective and preventive action (CAPA) planning + persistence.

* `CapaPlanner` turns scored findings into `CorrectiveAction` items with
  severity-scaled due dates (time injected as a `Clock`, so tests and the
  demo are fully deterministic).
* `AuditRepository` is the persistence protocol; `SqliteAuditRepository` is
  the default implementation. Higher layers depend only on the protocol
  (dependency inversion), so swapping SQLite for Postgres/SharePoint later
  touches exactly one class.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from esg_pipeline.models import (
    CorrectiveAction,
    CorrectiveActionStatus,
    FindingSeverity,
    RiskScore,
    SupplierAudit,
    SupplierRiskRecord,
)

Clock = Callable[[], date]


def system_clock() -> date:
    """Default clock: current UTC date."""
    return datetime.now(tz=timezone.utc).date()


@dataclass(frozen=True, slots=True)
class CapaPolicy:
    """How findings map to corrective actions and deadlines."""

    due_days_by_severity: dict[FindingSeverity, int] = field(
        default_factory=lambda: {
            FindingSeverity.MINOR: 60,
            FindingSeverity.MAJOR: 30,
            FindingSeverity.CRITICAL: 7,
        }
    )
    raise_capa_from_severity: FindingSeverity = FindingSeverity.MINOR

    def __post_init__(self) -> None:
        if set(self.due_days_by_severity) != set(FindingSeverity):
            msg = "due_days_by_severity must cover all severities"
            raise ValueError(msg)
        if any(d <= 0 for d in self.due_days_by_severity.values()):
            msg = "due days must be positive"
            raise ValueError(msg)

    def action_text(self, category: str, severity: FindingSeverity) -> str:
        category_label = category.replace("_", " ").title()
        if severity is FindingSeverity.CRITICAL:
            return (
                f"Immediate containment for {category_label}: root-cause analysis, "
                "remediation plan, and verification audit within the deadline."
            )
        if severity is FindingSeverity.MAJOR:
            return (
                f"Submit a documented {category_label} improvement plan, train staff, "
                "and evidence implementation to the compliance office."
            )
        return (
            f"Correct the {category_label} gap, update procedures, and provide "
            "photo/record evidence to the compliance office."
        )


@dataclass(frozen=True, slots=True)
class CapaPlanner:
    """Creates corrective actions for findings at/above the policy threshold."""

    policy: CapaPolicy = field(default_factory=CapaPolicy)
    clock: Clock = system_clock

    def plan(self, audit: SupplierAudit, risk: RiskScore) -> tuple[CorrectiveAction, ...]:
        today = self.clock()
        order = {
            FindingSeverity.MINOR: 0,
            FindingSeverity.MAJOR: 1,
            FindingSeverity.CRITICAL: 2,
        }
        actions: list[CorrectiveAction] = []
        for finding in sorted(audit.findings, key=lambda f: (-order[f.severity], f.category)):
            if order[finding.severity] < order[self.policy.raise_capa_from_severity]:
                continue
            due = today + timedelta(days=self.policy.due_days_by_severity[finding.severity])
            actions.append(
                CorrectiveAction(
                    supplier_id=audit.supplier_id,
                    category=finding.category,
                    severity=finding.severity,
                    description=finding.description,
                    action=self.policy.action_text(finding.category, finding.severity),
                    due_date=due,
                )
            )
        return tuple(actions)


class AuditRepository(Protocol):
    """Persistence boundary for processed supplier risk records."""

    def save(self, record: SupplierRiskRecord) -> None: ...

    def get(self, supplier_id: str) -> SupplierRiskRecord | None: ...

    def all_records(self) -> Sequence[SupplierRiskRecord]: ...

    def close(self) -> None: ...


class InMemoryAuditRepository:
    """Trivial repository for tests and quick experiments."""

    def __init__(self) -> None:
        self._records: dict[str, SupplierRiskRecord] = {}

    def save(self, record: SupplierRiskRecord) -> None:
        self._records[record.audit.supplier_id] = record

    def get(self, supplier_id: str) -> SupplierRiskRecord | None:
        return self._records.get(supplier_id)

    def all_records(self) -> Sequence[SupplierRiskRecord]:
        return tuple(self._records.values())

    def close(self) -> None:
        pass


_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    supplier_id   TEXT PRIMARY KEY,
    supplier_name TEXT NOT NULL,
    country       TEXT NOT NULL,
    audit_date    TEXT NOT NULL,
    auditor       TEXT NOT NULL,
    scheme        TEXT NOT NULL,
    overall_score INTEGER NOT NULL,
    findings_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS risk_score (
    supplier_id TEXT PRIMARY KEY REFERENCES audit(supplier_id),
    total       REAL NOT NULL,
    band        TEXT NOT NULL,
    factors_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS corrective_action (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id TEXT NOT NULL REFERENCES audit(supplier_id),
    category    TEXT NOT NULL,
    severity    TEXT NOT NULL,
    description TEXT NOT NULL,
    action      TEXT NOT NULL,
    due_date    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS processing_run (
    supplier_id TEXT PRIMARY KEY,
    source_file TEXT,
    processed_at TEXT NOT NULL
);
"""


def _dt_to_json_iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def _json_iso_to_dt(value: str) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass
class SqliteAuditRepository:
    """Default repository: a single SQLite file, JSON side-fields.

    Kept deliberately simple (no ORM): schema is created on open, writes are
    transactional per record, and every value round-trips back into the same
    domain objects (verified by the repository contract tests).
    """

    db_path: Path
    connection: sqlite3.Connection = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.db_path = Path(self.db_path)
        if self.db_path.parent != Path():
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(_SCHEMA)
        self.connection.commit()

    def save(self, record: SupplierRiskRecord) -> None:
        a = record.audit
        with self.connection:
            self.connection.execute(
                "INSERT INTO audit(supplier_id, supplier_name, country, audit_date,"
                " auditor, scheme, overall_score, findings_json)"
                " VALUES(?,?,?,?,?,?,?,?)"
                " ON CONFLICT(supplier_id) DO UPDATE SET"
                "  supplier_name=excluded.supplier_name,"
                "  country=excluded.country,"
                "  audit_date=excluded.audit_date,"
                "  auditor=excluded.auditor,"
                "  scheme=excluded.scheme,"
                "  overall_score=excluded.overall_score,"
                "  findings_json=excluded.findings_json",
                (
                    a.supplier_id,
                    a.supplier_name,
                    a.country,
                    a.audit_date.isoformat(),
                    a.auditor,
                    a.scheme,
                    a.overall_score,
                    json.dumps(
                        [
                            {
                                "category": f.category,
                                "severity": f.severity.value,
                                "description": f.description,
                            }
                            for f in a.findings
                        ]
                    ),
                ),
            )
            self.connection.execute(
                "INSERT INTO risk_score(supplier_id, total, band, factors_json)"
                " VALUES(?,?,?,?)"
                " ON CONFLICT(supplier_id) DO UPDATE SET"
                "  total=excluded.total, band=excluded.band,"
                "  factors_json=excluded.factors_json",
                (
                    a.supplier_id,
                    record.risk.total,
                    record.risk.band.value,
                    json.dumps(
                        [
                            {
                                "category": f.category,
                                "severity": f.severity.value,
                                "deduction": f.deduction,
                            }
                            for f in record.risk.factors
                        ]
                    ),
                ),
            )
            self.connection.execute(
                "DELETE FROM corrective_action WHERE supplier_id=?", (a.supplier_id,)
            )
            for ca in record.corrective_actions:
                self.connection.execute(
                    "INSERT INTO corrective_action(supplier_id, category, severity,"
                    " description, action, due_date, status)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (
                        ca.supplier_id,
                        ca.category,
                        ca.severity.value,
                        ca.description,
                        ca.action,
                        ca.due_date.isoformat(),
                        ca.status.value,
                    ),
                )
            self.connection.execute(
                "INSERT INTO processing_run(supplier_id, source_file, processed_at)"
                " VALUES(?,?,?)"
                " ON CONFLICT(supplier_id) DO UPDATE SET"
                "  source_file=excluded.source_file,"
                "  processed_at=excluded.processed_at",
                (
                    a.supplier_id,
                    record.source_file or "",
                    _dt_to_json_iso(record.processed_at),
                ),
            )

    def get(self, supplier_id: str) -> SupplierRiskRecord | None:
        row = self.connection.execute(
            "SELECT a.*, r.total, r.band, r.factors_json, p.source_file, p.processed_at"
            " FROM audit a"
            " JOIN risk_score r ON r.supplier_id = a.supplier_id"
            " LEFT JOIN processing_run p ON p.supplier_id = a.supplier_id"
            " WHERE a.supplier_id=?",
            (supplier_id,),
        ).fetchone()
        if row is None:
            return None
        actions = self.connection.execute(
            "SELECT * FROM corrective_action WHERE supplier_id=? ORDER BY due_date, id",
            (supplier_id,),
        ).fetchall()
        return _row_to_record(row, actions)

    def all_records(self) -> Sequence[SupplierRiskRecord]:
        rows = self.connection.execute(
            "SELECT a.*, r.total, r.band, r.factors_json, p.source_file, p.processed_at"
            " FROM audit a"
            " JOIN risk_score r ON r.supplier_id = a.supplier_id"
            " LEFT JOIN processing_run p ON p.supplier_id = a.supplier_id"
            " ORDER BY r.total DESC"
        ).fetchall()
        all_actions: dict[str, list[sqlite3.Row]] = {}
        for action_row in self.connection.execute(
            "SELECT * FROM corrective_action ORDER BY due_date, id"
        ).fetchall():
            all_actions.setdefault(action_row["supplier_id"], []).append(action_row)
        return tuple(
            _row_to_record(row, all_actions.get(row["supplier_id"], [])) for row in rows
        )

    def close(self) -> None:
        self.connection.close()


def _row_to_record(
    row: sqlite3.Row, action_rows: Iterable[sqlite3.Row]
) -> SupplierRiskRecord:
    from esg_pipeline.models import Finding, RiskBand, RiskFactor

    findings = tuple(
        Finding(
            category=f["category"],
            severity=FindingSeverity(f["severity"]),
            description=f["description"],
        )
        for f in json.loads(row["findings_json"])
    )
    risk = RiskScore(
        total=row["total"],
        band=RiskBand(row["band"]),
        factors=tuple(
            RiskFactor(
                category=f["category"],
                severity=FindingSeverity(f["severity"]),
                deduction=f["deduction"],
            )
            for f in json.loads(row["factors_json"])
        ),
    )
    actions = tuple(
        CorrectiveAction(
            supplier_id=a["supplier_id"],
            category=a["category"],
            severity=FindingSeverity(a["severity"]),
            description=a["description"],
            action=a["action"],
            due_date=date.fromisoformat(a["due_date"]),
            status=CorrectiveActionStatus(a["status"]),
        )
        for a in action_rows
    )
    return SupplierRiskRecord(
        audit=SupplierAudit(
            supplier_id=row["supplier_id"],
            supplier_name=row["supplier_name"],
            country=row["country"],
            audit_date=date.fromisoformat(row["audit_date"]),
            auditor=row["auditor"],
            scheme=row["scheme"],
            overall_score=row["overall_score"],
            findings=findings,
        ),
        risk=risk,
        corrective_actions=actions,
        processed_at=_json_iso_to_dt(row["processed_at"]) if row["processed_at"] else None,
        source_file=row["source_file"] or None,
    )
