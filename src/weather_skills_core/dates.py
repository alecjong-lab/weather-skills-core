"""CLI date values: YYYY-MM-DD or the string 'latest'."""

from __future__ import annotations

import re
from datetime import date

from weather_skills_core.errors import DataError, UsageError

_ABS = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def np_to_date(value) -> date:
    import numpy as np

    if np.isnat(value):
        raise DataError("time coordinate value is NaT")
    return date.fromisoformat(np.datetime_as_string(value, unit="D"))


def parse_date_value(value: str, *, flag: str = "date") -> date | str:
    if value == "latest":
        return "latest"
    if _ABS.match(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise UsageError(f"invalid {flag} value {value!r}: expected YYYY-MM-DD or 'latest'")
