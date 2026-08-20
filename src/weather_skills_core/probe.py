"""Fetcher ``--probe-latest`` helpers.

A probe is a standalone call: it prints one line on stdout (``YYYY-MM-DD``
or ``none``, e.g. CMIP6) and must not download fields. ``PROBE_LATEST_KWARGS``
sets ``standalone=True`` so the decorator drops required flags and skips
writing. Agents call it on the fetcher; resolve-time does calendar math only.
"""

from __future__ import annotations

from datetime import date

from weather_skills_core.errors import DataError

PROBE_LATEST_KWARGS = {
    "nargs": "?",
    "const": "",
    "default": None,
    "metavar": "IDENT",
    "standalone": True,
    "help": (
        "Print the latest available YYYY-MM-DD (or none) on stdout and exit. "
        "Does not download fields. Optional IDENT selects a product "
        "(dataset id, IMERG late/final, …)."
    ),
}


def parse_probe_stdout(text: str) -> date | None:
    """Parse a probe's stdout (last non-empty line is ``YYYY-MM-DD`` or ``none``)."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise DataError("probe produced no stdout")
    last = lines[-1]
    if last == "none":
        return None
    try:
        return date.fromisoformat(last)
    except ValueError:
        raise DataError(f"probe stdout is not a date or 'none': {last!r}") from None
