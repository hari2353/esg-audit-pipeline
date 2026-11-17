"""Supplier trend scorecards — EcoVadis's core output (comparable results
over time, improvement scorecards) computed from the audit history the
repository now keeps. A supplier with one audit has NO trend — inventing
'stable' from a single point is a fabrication, and the dashboard must not
do it."""
from datetime import date

import pytest

from esg_pipeline.models import AuditSummary, RiskBand
from esg_pipeline.trend import supplier_trend


def summaries(*entries: tuple[str, str]) -> tuple[AuditSummary, ...]:
    """(iso-date, band) pairs; total derived from the band midpoint."""
    mid = {"low": 20.0, "medium": 45.0, "high": 70.0, "critical": 90.0}
    return tuple(
        AuditSummary(audit_date=date.fromisoformat(d), total=mid[b], band=b)
        for d, b in entries
    )


def test_single_audit_has_no_trend():
    assert supplier_trend(summaries(("2026-01-15", "medium"))) is None


def test_improving_and_declining_directions():
    improving = supplier_trend(summaries(("2026-01-15", "high"), ("2026-07-15", "low")))
    assert improving.direction == "improving"
    assert improving.delta < 0
    assert improving.band_changed is True

    declining = supplier_trend(summaries(("2026-01-15", "low"), ("2026-07-15", "high")))
    assert declining.direction == "declining"
    assert declining.delta > 0
    assert declining.band_changed is True


def test_stable_within_one_point():
    flat = supplier_trend(summaries(("2026-01-15", "medium"), ("2026-07-15", "medium")))
    assert flat.direction == "stable"
    assert flat.band_changed is False


def test_three_audits_use_the_last_two():
    """The recent trend, not the lifetime: low -> critical -> high.
    The trend is critical->high (improving, 90->70), not low->high."""
    trend = supplier_trend(
        summaries(("2025-07-15", "low"), ("2026-01-15", "critical"), ("2026-07-15", "high"))
    )
    assert trend.previous.band == "critical"     # last two, not first+last
    assert trend.direction == "improving"        # 90 -> 70


def test_entries_are_ordered_by_audit_date_not_input_order():
    trend = supplier_trend(summaries(("2026-07-15", "low"), ("2026-01-15", "high")))
    assert trend.direction == "improving"


def test_audit_summary_is_frozen():
    entry = AuditSummary(audit_date=date(2026, 1, 1), total=50.0, band="medium")
    with pytest.raises((AttributeError, TypeError)):
        entry.total = 99.0  # type: ignore[misc]


def test_repository_keeps_audit_history():
    from esg_pipeline.capa import InMemoryAuditRepository, SqliteAuditRepository
    from esg_pipeline.trend import portfolio_trends
    from tests.test_repository_contract import _record

    repo = SqliteAuditRepository(":memory:")
    first = _record("SUP-1")
    repo.save(first)
    second = _record("SUP-1")
    from dataclasses import replace

    from esg_pipeline.models import RiskBand as Band

    worse = replace(second.audit, audit_date=date(2026, 8, 1))
    repo.save(
        replace(second, audit=worse,
                risk=replace(second.risk, total=90.0, band=Band.CRITICAL))
    )
    history = repo.history("SUP-1")
    assert len(history) == 2
    assert [s.band for s in history] == [RiskBand.HIGH.value, RiskBand.CRITICAL.value]
    trends = portfolio_trends(repo)
    assert trends["SUP-1"].direction == "declining"
    repo.close()

    # in-memory repository keeps the same contract
    mem = InMemoryAuditRepository()
    mem.save(first)
    mem.save(replace(second, audit=worse,
                     risk=replace(second.risk, total=90.0, band=Band.CRITICAL)))
    assert len(mem.history("SUP-1")) == 2
    mem.close()
