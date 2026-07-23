from datetime import UTC, date, datetime, timedelta

import pytest

from weather_skills_core import dates
from weather_skills_core.errors import UsageError


def today():
    return datetime.now(UTC).date()


class TestParseToken:
    def test_absolute(self):
        assert dates.parse_token("2026-01-02") == ("abs", date(2026, 1, 2))

    def test_now_and_today_alias(self):
        assert dates.parse_token("now") == ("base", "now")
        assert dates.parse_token("today") == ("base", "now")

    def test_latest(self):
        assert dates.parse_token("latest") == ("base", "latest")

    def test_day_offset(self):
        assert dates.parse_token("now-3d") == ("offset", "now", 3, "3-day")

    def test_week_offset_is_seven_days(self):
        assert dates.parse_token("latest-2w") == ("offset", "latest", 14, "2-week")
        assert dates.parse_token("now-1w") == ("offset", "now", 7, "1-week")

    def test_cap_boundary_allowed(self):
        assert dates.parse_token("now-36525d")[2] == 36525

    def test_cap_exceeded_rejected(self):
        with pytest.raises(UsageError, match="36525"):
            dates.parse_token("now-36526d")

    def test_cap_exceeded_in_weeks_rejected(self):
        # 5219 weeks = 36533 days, above the cap.
        with pytest.raises(UsageError, match="36525"):
            dates.parse_token("latest-5219w")

    def test_zero_offset_rejected(self):
        with pytest.raises(UsageError, match=">= 1"):
            dates.parse_token("now-0d")

    @pytest.mark.parametrize(
        "value",
        [
            "now+3d",  # future offsets are rejected
            "latest-3m",  # month units are rejected
            "now-1y",  # year units are rejected
            "2026/01/02",
            "20260102",  # compact ISO form is rejected
            "2026-W01-1",  # ISO-week form is rejected
            "yesterday",
            "",
            "latest-",
            "-3d",
        ],
    )
    def test_malformed_rejected(self, value):
        with pytest.raises(UsageError, match="invalid date value"):
            dates.parse_token(value)

    def test_shape_valid_but_impossible_date_rejected(self):
        with pytest.raises(UsageError, match="invalid date value"):
            dates.parse_token("2026-13-40")


class TestResolveWindow:
    def test_absolute_inclusive_no_log(self):
        start, end, log = dates.resolve_window("2026-01-01", "2026-01-10")
        assert (start, end) == (date(2026, 1, 1), date(2026, 1, 10))
        assert log is None

    def test_now_both_ends(self):
        start, end, log = dates.resolve_window("now", "now")
        assert start == end == today()
        assert log is not None

    def test_duration_idiom_now_week(self):
        start, end, log = dates.resolve_window("now-1w", "now")
        assert end == today()
        assert (end - start).days + 1 == 7
        assert "duration mode: 1-week window inclusive of now" in log

    def test_duration_idiom_latest_three_weeks(self):
        latest = date(2026, 6, 30)
        start, end, log = dates.resolve_window("latest-3w", "latest", lambda: latest)
        assert end == latest
        assert start == latest - timedelta(days=20)
        assert (end - start).days + 1 == 21
        assert "(21 days; duration mode: 3-week window inclusive of latest)" in log

    def test_duration_idiom_days(self):
        start, end, _ = dates.resolve_window("now-6d", "now")
        assert (end - start).days + 1 == 6

    def test_offsets_stay_literal_outside_duration_shape(self):
        # Both ends are offsets, so no duration shift: inclusive both ends.
        start, end, log = dates.resolve_window("now-10d", "now-2d")
        assert start == today() - timedelta(days=10)
        assert end == today() - timedelta(days=2)
        assert "(9 days; inclusive both ends)" in log

    def test_mixed_bases_are_not_duration(self):
        latest = today()
        start, end, log = dates.resolve_window("now-3d", "latest", lambda: latest)
        assert start == today() - timedelta(days=3)
        assert end == latest
        assert "inclusive both ends" in log

    def test_reversed_range_rejected(self):
        with pytest.raises(UsageError, match="reversed"):
            dates.resolve_window("2026-01-10", "2026-01-01")

    def test_resolver_called_once_for_double_latest(self):
        calls = []

        def resolver():
            calls.append(1)
            return date(2026, 6, 30)

        dates.resolve_window("latest-3w", "latest", resolver)
        assert len(calls) == 1

    def test_resolver_not_called_for_absolute(self):
        calls = []
        dates.resolve_window("2026-01-01", "2026-01-10", lambda: calls.append(1) or today())
        assert calls == []

    def test_resolver_not_called_for_now_only(self):
        calls = []
        dates.resolve_window("now-3d", "now", lambda: calls.append(1) or today())
        assert calls == []

    def test_latest_without_resolver_rejected(self):
        with pytest.raises(UsageError, match="no 'latest' resolver"):
            dates.resolve_window("latest-3d", "latest")

    def test_log_line_contains_tokens_and_resolved_dates(self):
        latest = date(2026, 6, 30)
        _, _, log = dates.resolve_window("latest-3w", "latest", lambda: latest)
        assert '"latest-3w".."latest"' in log
        assert "2026-06-10..2026-06-30" in log

    def test_mixed_absolute_and_relative_logs(self):
        _, _, log = dates.resolve_window("2026-01-01", "now")
        assert log is not None


class TestResolveDate:
    def test_absolute_no_log(self):
        resolved, log = dates.resolve_date("2026-05-01")
        assert resolved == date(2026, 5, 1)
        assert log is None

    def test_now(self):
        resolved, log = dates.resolve_date("now")
        assert resolved == today()
        assert "single date" in log

    def test_latest_offset(self):
        resolved, log = dates.resolve_date("latest-2d", lambda: date(2026, 6, 30))
        assert resolved == date(2026, 6, 28)
        assert '"latest-2d" -> 2026-06-28' in log

    def test_context_labels_log(self):
        _, log = dates.resolve_date("now", context="single forecast init date")
        assert "single forecast init date" in log

    def test_malformed_rejected(self):
        with pytest.raises(UsageError, match="invalid date value"):
            dates.resolve_date("now+1d")

    def test_latest_without_resolver_rejected(self):
        with pytest.raises(UsageError, match="no 'latest' resolver"):
            dates.resolve_date("latest")


class TestNpToDate:
    def test_day_precision(self):
        import numpy as np

        assert dates.np_to_date(np.datetime64("2026-05-09")) == date(2026, 5, 9)

    def test_time_of_day_truncated(self):
        import numpy as np

        assert dates.np_to_date(np.datetime64("2026-05-09T23:59:58")) == date(2026, 5, 9)

    def test_nanosecond_values(self):
        import numpy as np

        value = np.datetime64("2026-05-09T12:00:00", "ns")
        assert dates.np_to_date(value) == date(2026, 5, 9)


class TestTodayUtc:
    def test_returns_the_current_utc_date(self):
        # Bounds derived from the UTC clock at the assertion site, so the
        # comparison holds in any local timezone.
        before = datetime.now(UTC).date()
        resolved = dates.today_utc()
        after = datetime.now(UTC).date()
        assert before <= resolved <= after

    def test_accepts_and_ignores_resolver_args(self):
        # Callable as latest_resolver(args): one positional argument.
        assert isinstance(dates.today_utc(object()), date)
