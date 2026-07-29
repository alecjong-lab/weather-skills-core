"""Absolute YYYY-MM-DD date parsing for weather-skill CLIs."""

import re
from datetime import date

from weather_skills_core.errors import DataError, UsageError

_ABS_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def np_to_date(value) -> date:
    """Convert a numpy datetime64 to a calendar date (truncating time-of-day)."""
    import numpy as np

    if np.isnat(value):
        raise DataError(
            "time coordinate value is NaT (not-a-time); the dataset has a missing or "
            "unfilled time entry where a valid date is required."
        )
    return date.fromisoformat(np.datetime_as_string(value, unit="D"))


def parse_date(value: str) -> date:
    """Parse an absolute ``YYYY-MM-DD`` date string."""
    if not _ABS_DATE_RE.match(value):
        raise UsageError(f"invalid date value {value!r}: expected an absolute date YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise UsageError(
            f"invalid date value {value!r}: expected an absolute date YYYY-MM-DD"
        ) from None


def parse_range(start_value: str, end_value: str) -> tuple[date, date]:
    """Parse ``--start``/``--end`` as absolute dates and require ``start <= end``."""
    start = parse_date(start_value)
    end = parse_date(end_value)
    if start > end:
        raise UsageError(
            f"resolved --start {start.isoformat()} is after resolved "
            f"--end {end.isoformat()}; the range is reversed."
        )
    return start, end
