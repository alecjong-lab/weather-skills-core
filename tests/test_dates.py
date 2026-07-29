"""Absolute YYYY-MM-DD + latest passthrough."""

import datetime

import pytest

from weather_skills_core.dates import parse_date_value
from weather_skills_core.errors import UsageError


def test_absolute():
    assert parse_date_value("2026-01-15") == datetime.date(2026, 1, 15)


def test_latest_passthrough():
    assert parse_date_value("latest") == "latest"


def test_rejects_offsets():
    with pytest.raises(UsageError, match="YYYY-MM-DD"):
        parse_date_value("latest-2w")
