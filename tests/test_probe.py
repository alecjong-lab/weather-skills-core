"""Correctness tests for weather_skills_core.probe."""

from datetime import date

import pytest

from weather_skills_core.errors import DataError
from weather_skills_core.probe import parse_probe_stdout


def test_parse_probe_stdout_date():
    """A YYYY-MM-DD line parses to a date."""
    assert parse_probe_stdout("2026-08-15\n") == date(2026, 8, 15)


def test_parse_probe_stdout_uses_last_nonempty_line():
    """Trailing noise is ignored; the last nonempty line is the date."""
    assert parse_probe_stdout("noise\n2026-08-18\n") == date(2026, 8, 18)


def test_parse_probe_stdout_none():
    """'none' means the fetcher has no latest date."""
    assert parse_probe_stdout("none") is None


def test_parse_probe_stdout_rejects_garbage():
    """Non-date stdout is a DataError."""
    with pytest.raises(DataError, match="not a date"):
        parse_probe_stdout("soon")


def test_parse_probe_stdout_rejects_empty():
    """Blank stdout is a DataError."""
    with pytest.raises(DataError, match="no stdout"):
        parse_probe_stdout("  \n")
