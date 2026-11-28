"""Incremental ETL (cross-pollinated from Uber's incremental data lake).

Processes only records newer than a watermark, returning the new records and
an updated watermark. Pure Python, deterministic.
"""

from __future__ import annotations


def incremental_filter(
    records: list[dict], watermark: str, *, key: str = "updated_at"
) -> tuple[list[dict], str]:
    """Return records with ``key`` > ``watermark`` and the new watermark."""
    new = [r for r in records if str(r.get(key, "")) > watermark]
    new_wm = max((str(r.get(key, "")) for r in new), default=watermark)
    return new, new_wm


def dedup_by_key(records: list[dict], key: str = "id") -> list[dict]:
    """Keep the last record for each key (idempotent merge)."""
    seen: dict[object, dict] = {}
    for r in records:
        seen[r.get(key)] = r
    return list(seen.values())
