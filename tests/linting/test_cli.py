"""The weather-skills-core CLI: argument handling, output formats, exit codes."""

import json
from pathlib import Path

import pytest

from weather_skills_core.linting.cli import main

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "linting"


def test_exit_codes_findings_still_exit_zero(capsys):
    assert main(["lint", str(FIXTURES / "shadow_tree")]) == 0
    assert "WSK101" in capsys.readouterr().out


def test_exit_codes_clean_run_exits_zero(capsys):
    assert main(["lint", str(FIXTURES / "clean_tree")]) == 0
    assert "no findings" in capsys.readouterr().out


def test_exit_codes_unlintable_path_is_a_usage_error(tmp_path, capsys):
    assert main(["lint", str(tmp_path)]) == 2
    assert "does not match any skill layout" in capsys.readouterr().err


def test_exit_codes_missing_subcommand_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_exit_codes_strict_exits_one_at_or_above_the_threshold(capsys):
    assert main(["lint", str(FIXTURES / "shadow_tree"), "--strict", "warning"]) == 1
    capsys.readouterr()
    assert main(["lint", str(FIXTURES / "shadow_tree"), "--strict", "error"]) == 0


def test_rule_selection_unknown_selector_exits_two_naming_it(capsys):
    assert main(["lint", str(FIXTURES / "clean_tree"), "--select", "WSK999"]) == 2
    assert "WSK999" in capsys.readouterr().err


def test_default_path_no_argument_lints_the_current_directory(capsys, monkeypatch):
    monkeypatch.chdir(FIXTURES / "clean_tree" / "skills" / "clean-skill")
    assert main(["lint"]) == 0
    out = capsys.readouterr().out
    assert "clean-skill — score 100/100" in out


def test_text_format_grouped_by_skill_with_score_lines(capsys):
    main(["lint", str(FIXTURES / "multi_tree")])
    out = capsys.readouterr().out
    assert "alpha — score" in out
    assert "beta — score" in out
    assert "Aggregate score:" in out


def test_json_format_stable_schema(capsys):
    main(["lint", str(FIXTURES / "multi_tree"), "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"findings", "score", "notes"}
    assert set(payload["score"]) == {"aggregate", "per_skill"}
    assert payload["score"]["per_skill"].keys() == {
        "alpha/scripts/alpha.py",
        "beta/scripts/beta.py",
        "gamma/scripts/gamma.py",
    }
    main(["lint", str(FIXTURES / "shadow_tree"), "--format", "json"])
    finding = json.loads(capsys.readouterr().out)["findings"][0]
    assert set(finding) == {"rule", "severity", "skill", "flag", "file", "message"}
