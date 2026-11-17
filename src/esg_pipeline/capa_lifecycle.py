"""CAPA lifecycle — Sedex's 2026-27 SMETA CAR redesign as a deterministic
state machine. Findings carry recorded progress steps and blockers;
suppliers mark them resolved; only a separate VERIFICATION closes them,
and a rejected verification reopens with the progress log intact.

Every transition takes the injected clock — no wall time in library code.
All functions are pure: they return a NEW frozen CorrectiveAction via
dataclasses.replace.
"""
from dataclasses import replace
from datetime import date, datetime
from typing import Protocol

from esg_pipeline.models import CorrectiveAction, CorrectiveActionStatus, ProgressEntry


class Clock(Protocol):
    def now_date(self) -> date: ...

    def now_datetime(self) -> datetime: ...


def _guard_transition(action: CorrectiveAction, target: CorrectiveActionStatus) -> None:
    if action.status is CorrectiveActionStatus.VERIFIED:
        msg = "verified CAPAs are closed — no further transitions"
        raise ValueError(msg)
    if action.status is target:
        msg = f"CAPA is already {target.value}"
        raise ValueError(msg)


def start(action: CorrectiveAction, *, note: str, clock: Clock) -> CorrectiveAction:
    """OPEN -> IN_PROGRESS, recording the first progress step."""
    _guard_transition(action, CorrectiveActionStatus.IN_PROGRESS)
    if action.status is not CorrectiveActionStatus.OPEN:
        msg = f"cannot start a {action.status.value} CAPA"
        raise ValueError(msg)
    entry = ProgressEntry(kind="started", note=note, at=clock.now_datetime())
    return replace(action, status=CorrectiveActionStatus.IN_PROGRESS,
                   progress_log=(*action.progress_log, entry))


def record_progress(action: CorrectiveAction, *, note: str, clock: Clock) -> CorrectiveAction:
    """Append a progress note or blocker to an IN_PROGRESS CAPA."""
    if action.status is not CorrectiveActionStatus.IN_PROGRESS:
        msg = f"progress can only be recorded on an in_progress CAPA, got {action.status.value}"
        raise ValueError(msg)
    entry = ProgressEntry(kind="note", note=note, at=clock.now_datetime())
    return replace(action, progress_log=(*action.progress_log, entry))


def mark_resolved(action: CorrectiveAction, *, note: str, clock: Clock) -> CorrectiveAction:
    """OPEN or IN_PROGRESS -> RESOLVED: the supplier claims the work is done.
    Closure still requires a separate verification."""
    if action.status is CorrectiveActionStatus.VERIFIED:
        _guard_transition(action, CorrectiveActionStatus.RESOLVED)
    if action.status is CorrectiveActionStatus.RESOLVED:
        msg = "CAPA is already resolved — verify it instead"
        raise ValueError(msg)
    entry = ProgressEntry(kind="resolved", note=note, at=clock.now_datetime())
    return replace(action, status=CorrectiveActionStatus.RESOLVED,
                   progress_log=(*action.progress_log, entry),
                   resolved_at=clock.now_datetime())


def verify(
    action: CorrectiveAction,
    *,
    outcome: str,
    note: str,
    clock: Clock,
) -> CorrectiveAction:
    """RESOLVED -> VERIFIED (auditor confirmed; terminal) or RESOLVED ->
    IN_PROGRESS (rejected: reopened, progress preserved)."""
    if outcome not in ("verified", "rejected"):
        msg = f"outcome must be 'verified' or 'rejected', got {outcome!r}"
        raise ValueError(msg)
    if action.status is CorrectiveActionStatus.VERIFIED:
        msg = "verified CAPAs are closed — no further transitions"
        raise ValueError(msg)
    if action.status is not CorrectiveActionStatus.RESOLVED:
        msg = f"only resolved CAPAs can be verified, got {action.status.value}"
        raise ValueError(msg)
    at = clock.now_datetime()
    if outcome == "verified":
        entry = ProgressEntry(kind="verified", note=note, at=at)
        return replace(action, status=CorrectiveActionStatus.VERIFIED,
                       progress_log=(*action.progress_log, entry),
                       verified_at=at)
    entry = ProgressEntry(kind="rejected", note=note, at=at)
    return replace(action, status=CorrectiveActionStatus.IN_PROGRESS,
                   progress_log=(*action.progress_log, entry))
