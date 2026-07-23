import pytest

from weather_skills_core import util
from weather_skills_core.errors import UsageError


class TestIsTransient:
    @pytest.mark.parametrize(
        "text",
        [
            "429 Client Error: Too Many Requests",
            "API request failed with status code 500",
            "502 Bad Gateway",
            "503 Service Unavailable",
            "504 Gateway Timeout",
            "read timed out",
            "ConnectTimeout: request timeout",
            "Connection reset by peer",
        ],
    )
    def test_transient_markers(self, text):
        assert util.is_transient(Exception(text)) is True

    @pytest.mark.parametrize(
        "text",
        [
            "404 Not Found",
            "401 Unauthorized",
            "invalid parameter",
            "",
        ],
    )
    def test_non_transient(self, text):
        assert util.is_transient(Exception(text)) is False

    def test_case_insensitive(self):
        assert util.is_transient(Exception("Timed Out while reading")) is True


class TestRequireEnv:
    def test_returns_values_in_order(self, monkeypatch):
        monkeypatch.setenv("WSC_TEST_USER", "u")
        monkeypatch.setenv("WSC_TEST_PASS", "p")
        assert util.require_env("WSC_TEST_USER", "WSC_TEST_PASS") == ("u", "p")

    def test_default_message_names_only_the_missing(self, monkeypatch):
        monkeypatch.setenv("WSC_TEST_USER", "u")
        monkeypatch.delenv("WSC_TEST_PASS", raising=False)
        with pytest.raises(UsageError) as excinfo:
            util.require_env("WSC_TEST_USER", "WSC_TEST_PASS")
        assert str(excinfo.value) == "missing required env var(s): WSC_TEST_PASS"

    def test_all_missing_listed_in_order(self, monkeypatch):
        monkeypatch.delenv("WSC_TEST_USER", raising=False)
        monkeypatch.delenv("WSC_TEST_PASS", raising=False)
        with pytest.raises(UsageError) as excinfo:
            util.require_env("WSC_TEST_USER", "WSC_TEST_PASS")
        assert str(excinfo.value) == "missing required env var(s): WSC_TEST_USER, WSC_TEST_PASS"

    def test_empty_value_counts_as_missing(self, monkeypatch):
        monkeypatch.setenv("WSC_TEST_USER", "")
        with pytest.raises(UsageError, match="WSC_TEST_USER"):
            util.require_env("WSC_TEST_USER")

    def test_message_override(self, monkeypatch):
        monkeypatch.delenv("WSC_TEST_USER", raising=False)
        with pytest.raises(UsageError) as excinfo:
            util.require_env(
                "WSC_TEST_USER", message="WSC_TEST_USER and WSC_TEST_PASS must be set."
            )
        assert str(excinfo.value) == "WSC_TEST_USER and WSC_TEST_PASS must be set."

    def test_usage_error_exits_2(self, monkeypatch):
        monkeypatch.delenv("WSC_TEST_USER", raising=False)
        with pytest.raises(UsageError) as excinfo:
            util.require_env("WSC_TEST_USER")
        assert excinfo.value.exit_code == 2
