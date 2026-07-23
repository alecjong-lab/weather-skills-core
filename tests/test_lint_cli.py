"""The weather-skills-core CLI: argument handling, output formats, exit codes."""

import json
from pathlib import Path

import pytest

from weather_skills_core.cli import main

FIXTURES = Path(__file__).parent / "fixtures" / "lint"


class TestExitCodes:
    def test_findings_still_exit_zero(self, capsys):
        assert main(["lint", str(FIXTURES / "shadow_tree")]) == 0
        assert "WSK101" in capsys.readouterr().out

    def test_clean_run_exits_zero(self, capsys):
        assert main(["lint", str(FIXTURES / "clean_tree")]) == 0
        assert "no findings" in capsys.readouterr().out

    def test_unlintable_path_is_a_usage_error(self, tmp_path, capsys):
        assert main(["lint", str(tmp_path)]) == 2
        assert "does not match any skill layout" in capsys.readouterr().err

    def test_bad_against_value_is_a_usage_error(self, capsys):
        code = main(["lint", str(FIXTURES / "clean_tree"), "--against", "/no/such/path"])
        assert code == 2
        assert "--against /no/such/path" in capsys.readouterr().err

    def test_missing_subcommand_is_a_usage_error(self):
        with pytest.raises(SystemExit) as exc:
            main([])
        assert exc.value.code == 2

    def test_strict_exits_one_at_or_above_the_threshold(self, capsys):
        assert main(["lint", str(FIXTURES / "shadow_tree"), "--strict", "warning"]) == 1
        capsys.readouterr()
        # Warnings only: an error-level threshold does not trip.
        assert main(["lint", str(FIXTURES / "shadow_tree"), "--strict", "error"]) == 0


class TestDefaultPath:
    def test_no_argument_lints_the_current_directory(self, capsys, monkeypatch):
        monkeypatch.chdir(FIXTURES / "clean_tree" / "skills" / "clean-skill")
        assert main(["lint"]) == 0
        out = capsys.readouterr().out
        assert "clean-skill — score 100/100" in out


class TestTextFormat:
    def test_grouped_by_skill_with_score_lines(self, capsys):
        main(["lint", str(FIXTURES / "multi_tree")])
        out = capsys.readouterr().out
        assert "alpha — score" in out
        assert "beta — score" in out
        assert "Aggregate score:" in out

    def test_skipped_rules_visible_in_text(self, capsys):
        main(["lint", str(FIXTURES / "clean_tree" / "skills" / "clean-skill")])
        out = capsys.readouterr().out
        assert "Skipped: WSK201" in out
        assert "Skipped: WSK202" in out


class TestJsonFormat:
    def test_stable_schema(self, capsys):
        main(["lint", str(FIXTURES / "multi_tree"), "--format", "json"])
        payload = json.loads(capsys.readouterr().out)
        assert set(payload) == {"findings", "score", "skipped_rules", "notes"}
        assert set(payload["score"]) == {"aggregate", "per_skill"}
        finding = payload["findings"][0]
        assert set(finding) == {"rule", "severity", "skill", "flag", "file", "message"}
        assert payload["score"]["per_skill"].keys() == {"alpha", "beta", "gamma"}

    def test_skipped_rules_visible_in_json(self, capsys):
        main(
            [
                "lint",
                str(FIXTURES / "clean_tree" / "skills" / "clean-skill"),
                "--format",
                "json",
            ]
        )
        payload = json.loads(capsys.readouterr().out)
        assert [s["rule"] for s in payload["skipped_rules"]] == ["WSK201", "WSK202"]
        assert payload["findings"] == []
        assert payload["score"]["per_skill"] == {"clean-skill": 100}
