"""Product-agnostic publication lag / embargo calendars.

Catalog instances (skill names, notes, variants) live on each fetcher's
SKILL.md ``metadata.availability``. This module is the math: a typed spec and
``available_through(spec, as_of)``.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from weather_skills_core.errors import UsageError

SHAPES = ("date", "range", "either")
POLICIES = ("lag", "embargo", "none")
SCHEDULES = ("pentad", "ecmwf-s2s")

# S2S real-time inits are daily from IFS Cycle 48r1; Mon/Thu only before that.
ECMWF_S2S_DAILY_SINCE = date(2023, 6, 27)
PENTAD_PUBLISH_DELAY_DAYS = 2


def _is_pentad_end(d: date) -> bool:
    last = calendar.monthrange(d.year, d.month)[1]
    return d.day in {5, 10, 15, 20, 25, last}


def pentad_available_through(as_of: date, *, delay_days: int = PENTAD_PUBLISH_DELAY_DAYS) -> date:
    """Latest day of a pentad whose files have typically been published.

    Pentads close on the 5th, 10th, 15th, 20th, 25th, and last day of the month.
    Per-day files appear ``delay_days`` after that close (best-case lag 2 days;
    worst case ~7 days — the day after a pentad, waiting for the next batch).
    """
    d = as_of - timedelta(days=delay_days)
    while not _is_pentad_end(d):
        d -= timedelta(days=1)
    return d


def ecmwf_s2s_available_through(as_of: date, *, lag_days: int = 2) -> date:
    """Latest S2S init outside a real-time embargo (Mon/Thu before 2023-06-27)."""
    d = as_of - timedelta(days=lag_days)
    if d >= ECMWF_S2S_DAILY_SINCE:
        return d
    while d.weekday() not in (0, 3):  # Monday, Thursday
        d -= timedelta(days=1)
    return d


def ecmwf_s2s_valid_init(d: date) -> bool:
    """True if ``d`` is a published S2S real-time init day (ignoring embargo)."""
    if d >= ECMWF_S2S_DAILY_SINCE:
        return True
    return d.weekday() in (0, 3)


@dataclass(frozen=True)
class Availability:
    """One product's coverage clock: shape, lag/schedule, policy, optional start."""

    shape: str
    policy: str
    note: str = ""
    lag_days: int | None = None
    schedule: str | None = None
    earliest: date | None = None

    def __post_init__(self):
        if self.shape not in SHAPES:
            raise UsageError(f"availability.shape must be one of {SHAPES}; got {self.shape!r}.")
        if self.policy not in POLICIES:
            raise UsageError(f"availability.policy must be one of {POLICIES}; got {self.policy!r}.")
        if self.schedule is not None and self.schedule not in SCHEDULES:
            raise UsageError(
                f"availability.schedule must be one of {SCHEDULES}; got {self.schedule!r}."
            )
        if self.lag_days is not None and self.lag_days < 0:
            raise UsageError(f"availability.lag_days must be >= 0; got {self.lag_days}.")
        if self.schedule is None and self.policy != "none" and self.lag_days is None:
            raise UsageError(
                f"availability requires lag_days or schedule when policy is {self.policy!r}."
            )

    @classmethod
    def from_dict(cls, data: dict) -> Availability:
        """Build from a JSON/YAML mapping (SKILL.md or the generated snapshot)."""
        if not isinstance(data, dict):
            raise UsageError(f"availability must be a mapping; got {type(data).__name__}.")
        unknown = set(data) - {
            "shape",
            "policy",
            "note",
            "lag_days",
            "schedule",
            "earliest",
            "variants",
        }
        if unknown:
            raise UsageError("availability has unknown keys: " + ", ".join(sorted(unknown)) + ".")
        if "shape" not in data or "policy" not in data:
            raise UsageError("availability requires shape and policy.")
        earliest = data.get("earliest")
        if isinstance(earliest, date):
            pass
        elif earliest is not None:
            if not isinstance(earliest, str):
                raise UsageError("availability.earliest must be YYYY-MM-DD.")
            try:
                earliest = date.fromisoformat(earliest)
            except ValueError:
                raise UsageError(
                    f"availability.earliest {data['earliest']!r} is not YYYY-MM-DD."
                ) from None
        lag = data.get("lag_days")
        if lag is not None and not isinstance(lag, int):
            raise UsageError(f"availability.lag_days must be an int; got {lag!r}.")
        note = data.get("note", "")
        if not isinstance(note, str):
            raise UsageError("availability.note must be a string.")
        schedule = data.get("schedule")
        if schedule is not None and not isinstance(schedule, str):
            raise UsageError("availability.schedule must be a string.")
        return cls(
            shape=str(data["shape"]),
            policy=str(data["policy"]),
            note=note,
            lag_days=lag,
            schedule=schedule,
            earliest=earliest,
        )

    def to_dict(self) -> dict:
        """JSON-ready mapping (no variants; those are expanded by the catalog builder)."""
        out = {"shape": self.shape, "policy": self.policy, "note": self.note}
        if self.lag_days is not None:
            out["lag_days"] = self.lag_days
        if self.schedule is not None:
            out["schedule"] = self.schedule
        if self.earliest is not None:
            out["earliest"] = self.earliest.isoformat()
        return out


def available_through(spec: Availability, as_of: date) -> date | None:
    """Latest date the spec can fill, or None when there is no realtime cap."""
    if spec.schedule == "pentad":
        delay = spec.lag_days if spec.lag_days is not None else PENTAD_PUBLISH_DELAY_DAYS
        return pentad_available_through(as_of, delay_days=delay)
    if spec.schedule == "ecmwf-s2s":
        lag = spec.lag_days if spec.lag_days is not None else 2
        return ecmwf_s2s_available_through(as_of, lag_days=lag)
    if spec.lag_days is None:
        return None
    return as_of - timedelta(days=spec.lag_days)
