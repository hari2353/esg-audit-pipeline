"""Data-centric extraction evaluation with per-slice support guards.

Cross-pollinated from harvard-edge/dataperf-speech-example: hold the model
fixed and judge the data, but report every slice with its support so a small
slice cannot masquerade as a reliable win.
"""
from __future__ import annotations

from dataclasses import dataclass

from esg_pipeline.models import SupplierAudit

_SLICE_FIELDS = ("scheme", "country")


@dataclass(frozen=True, slots=True)
class SliceScore:
    name: str
    support: int
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float
    low_support: bool


def _finding_key(finding) -> tuple[str, str]:
    return (finding.category, finding.severity.value)


def _score(name: str, expected, predicted, min_support: int) -> SliceScore:
    tp = len(expected & predicted)
    fp = len(predicted - expected)
    fn = len(expected - predicted)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return SliceScore(
        name=name,
        support=len(expected),
        tp=tp,
        fp=fp,
        fn=fn,
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
        low_support=len(expected) < min_support,
    )


def evaluate_findings(
    cases: list[tuple[SupplierAudit, SupplierAudit]],
    *,
    slice_by: str = "scheme",
    min_support: int = 3,
) -> dict:
    """Score predicted audits against gold audits, globally and by slice.

    ``cases`` is [(gold, predicted)]. Findings are compared as
    (category, severity) sets. Slices below ``min_support`` gold findings are
    reported but excluded from the macro average, so one lucky finding cannot
    move the headline number."""
    if not cases:
        raise ValueError("cases must be non-empty")
    if slice_by not in _SLICE_FIELDS:
        raise ValueError(f"slice_by must be one of {_SLICE_FIELDS}")
    if min_support < 1:
        raise ValueError("min_support must be >= 1")

    overall_expected: set[tuple[str, str]] = set()
    overall_predicted: set[tuple[str, str]] = set()
    grouped: dict[str, tuple[set, set]] = {}
    for gold, predicted in cases:
        gold_set = {_finding_key(f) for f in gold.findings}
        pred_set = {_finding_key(f) for f in predicted.findings}
        overall_expected |= gold_set
        overall_predicted |= pred_set
        key = str(getattr(gold, slice_by))
        bucket = grouped.setdefault(key, (set(), set()))
        bucket[0].update(gold_set)
        bucket[1].update(pred_set)

    slices = {
        name: _score(name, expected, predicted, min_support)
        for name, (expected, predicted) in sorted(grouped.items())
    }
    reliable = [s for s in slices.values() if not s.low_support]
    micro = _score("micro", overall_expected, overall_predicted, min_support)

    return {
        "micro": micro,
        "macro_f1": round(
            sum(s.f1 for s in reliable) / len(reliable), 4
        ) if reliable else 0.0,
        "slices": slices,
        "low_support_slices": sorted(s.name for s in slices.values() if s.low_support),
    }
