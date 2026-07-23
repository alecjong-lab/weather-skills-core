"""Layout detection, corpus resolution, and the --against fetch paths."""

import shutil
import subprocess
from pathlib import Path

import pytest

from weather_skills_core.errors import UsageError
from weather_skills_core.lint import corpus as corpus_module
from weather_skills_core.lint.corpus import resolve_skill_dirs, sibling_skills
from weather_skills_core.lint.run import run_lint

FIXTURES = Path(__file__).parent / "fixtures" / "lint"


class TestLayoutDetection:
    def test_single_skill_dir(self):
        skill = FIXTURES / "clean_tree" / "skills" / "clean-skill"
        dirs, single = resolve_skill_dirs(skill)
        assert dirs == [skill.resolve()] and single

    def test_scripts_dir_resolves_to_its_skill(self):
        scripts = FIXTURES / "clean_tree" / "skills" / "clean-skill" / "scripts"
        dirs, single = resolve_skill_dirs(scripts)
        assert dirs == [scripts.parent.resolve()] and single

    def test_skills_tree_passed_directly(self):
        tree = FIXTURES / "multi_tree" / "skills"
        dirs, single = resolve_skill_dirs(tree)
        assert [d.name for d in dirs] == ["alpha", "beta", "gamma"] and not single

    def test_repo_root_with_a_skills_tree(self):
        dirs, single = resolve_skill_dirs(FIXTURES / "multi_tree")
        assert [d.name for d in dirs] == ["alpha", "beta", "gamma"] and not single

    def test_non_layout_is_a_usage_error(self, tmp_path):
        with pytest.raises(UsageError, match="does not match any skill layout"):
            resolve_skill_dirs(tmp_path)

    def test_file_path_is_a_usage_error(self):
        target = FIXTURES / "clean_tree" / "skills" / "clean-skill" / "SKILL.md"
        with pytest.raises(UsageError, match="not a directory"):
            resolve_skill_dirs(target)

    def test_sibling_discovery(self):
        alpha = FIXTURES / "multi_tree" / "skills" / "alpha"
        assert [d.name for d in sibling_skills(alpha)] == ["beta", "gamma"]


class TestAgainstLocalPath:
    def test_local_tree_joins_the_corpus_with_its_label(self):
        target = FIXTURES / "multi_tree" / "skills" / "alpha"
        against = str(FIXTURES / "against_tree")
        report = run_lint(target, [against])
        dupe = next(f for f in report.findings if f.rule == "WSK201" and f.flag == "--method")
        assert f"remote-skill (--against {against})" in dupe.message
        divergence = next(f for f in report.findings if f.rule == "WSK202" and f.flag == "--method")
        assert "remote-skill" in divergence.message
        # Findings stay scoped to the target.
        assert {f.skill for f in report.findings} == {"alpha"}

    def test_missing_local_path_that_is_not_a_github_ref_is_a_usage_error(self):
        with pytest.raises(UsageError, match="not an existing local path"):
            run_lint(FIXTURES / "clean_tree", ["/no/such/path/anywhere"])


def make_git_repo(tmp_path, source_tree):
    """A local git repository holding the given fixture tree, for file:// clones."""
    repo = tmp_path / "remote-repo"
    shutil.copytree(source_tree, repo)
    env_args = ["-c", "user.email=fixture@example.invalid", "-c", "user.name=Fixture"]
    subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", *env_args, "-C", str(repo), "commit", "--quiet", "-m", "fixture"],
        check=True,
    )
    return repo


class TestAgainstGitHub:
    def test_github_reference_fetched_via_clone(self, tmp_path, monkeypatch):
        repo = make_git_repo(tmp_path, FIXTURES / "against_tree")
        monkeypatch.setattr(corpus_module, "github_clone_url", lambda reference: f"file://{repo}")
        report = run_lint(
            FIXTURES / "multi_tree" / "skills" / "alpha", ["fixture-org/fixture-repo"]
        )
        dupe = next(f for f in report.findings if f.rule == "WSK201" and f.flag == "--method")
        assert "remote-skill (--against fixture-org/fixture-repo)" in dupe.message

    def test_no_clone_left_behind(self, tmp_path, monkeypatch):
        repo = make_git_repo(tmp_path, FIXTURES / "against_tree")
        created = []
        real_fetch = corpus_module._fetch_github

        def spy_fetch(reference, dest):
            created.append(dest)
            return real_fetch(reference, dest)

        monkeypatch.setattr(corpus_module, "github_clone_url", lambda reference: f"file://{repo}")
        monkeypatch.setattr(corpus_module, "_fetch_github", spy_fetch)
        run_lint(FIXTURES / "multi_tree" / "skills" / "alpha", ["fixture-org/fixture-repo"])
        assert created and all(not dest.exists() for dest in created)

    def test_unreachable_reference_is_a_usage_error_naming_it(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            corpus_module,
            "github_clone_url",
            lambda reference: f"file://{tmp_path}/does-not-exist",
        )
        with pytest.raises(UsageError, match="fixture-org/fixture-repo"):
            run_lint(FIXTURES / "clean_tree", ["fixture-org/fixture-repo"])

    def test_malformed_reference_is_a_usage_error(self):
        with pytest.raises(UsageError, match="not an existing local path"):
            run_lint(FIXTURES / "clean_tree", ["no-slash-and-no-such-path"])
