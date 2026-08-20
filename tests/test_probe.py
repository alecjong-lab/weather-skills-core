"""Correctness tests for weather_skills_core.probe."""

from datetime import date

import pytest

from weather_skills_core.errors import DataError
from weather_skills_core.probe import (
    argv_has_probe_latest,
    parse_probe_stdout,
)


def test_argv_has_probe_latest():
    assert argv_has_probe_latest(["--probe-latest"]) is True
    assert argv_has_probe_latest(["--probe-latest", "final"]) is True
    assert argv_has_probe_latest(["--probe-latest=final"]) is True
    assert argv_has_probe_latest(["--start-time", "2026-01-01"]) is False


def test_parse_probe_stdout_date():
    assert parse_probe_stdout("2026-08-15\n") == date(2026, 8, 15)


def test_parse_probe_stdout_uses_last_nonempty_line():
    assert parse_probe_stdout("noise\n2026-08-18\n") == date(2026, 8, 18)


def test_parse_probe_stdout_none():
    assert parse_probe_stdout("none") is None


def test_parse_probe_stdout_rejects_garbage():
    with pytest.raises(DataError, match="not a date"):
        parse_probe_stdout("soon")


def test_parse_probe_stdout_rejects_empty():
    with pytest.raises(DataError, match="no stdout"):
        parse_probe_stdout("  \n")
