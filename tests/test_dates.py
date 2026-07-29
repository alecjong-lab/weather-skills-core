"""Absolute-date parsing tests."""

from datetime import date

import pytest

from weather_skills_core.dates import parse_date, parse_range
from weather_skills_core.errors import UsageError


class TestParseDate:
    def test_absolute(self):
        assert parse_date("2026-01-15") == date(2026, 1, 15)

    def test_rejects_relative(self):
        for value in ("now", "today", "latest", "now-3d", "latest-1w"):
            with pytest.raises(UsageError, match="YYYY-MM-DD"):
                parse_date(value)

    def test_rejects_compact(self):
        with pytest.raises(UsageError, match="YYYY-MM-DD"):
            parse_date("20260115")


class TestParseRange:
    def test_ok(self):
        assert parse_range("2026-01-01", "2026-01-07") == (
            date(2026, 1, 1),
            date(2026, 1, 7),
        )

    def test_reversed(self):
        with pytest.raises(UsageError, match="reversed"):
            parse_range("2026-01-10", "2026-01-01")
