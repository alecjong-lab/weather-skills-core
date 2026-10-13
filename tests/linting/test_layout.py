"""Lint-target layout detection."""

from pathlib import Path

import pytest

from weather_skills_core.errors import UsageError
from weather_skills_core.linting.run import resolve_skill_dirs

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "linting"
DECORATED_SCRIPT = "from weather_skills_core import weather_skill\n_SKILL_VERSION = '0.1.0'\n@weather_skill(name='s', version=_SKILL_VERSION)\ndef s():\n    ...\n"


def make_skill(parent, name, *, skill_md=True, decorated=True):
    """A skill directory under ``parent`` with a script and optional SKILL.md."""
    skill = parent / name
    scripts = skill / "scripts"
    scripts.mkdir(parents=True)
    body = DECORATED_SCRIPT if decorated else "x = 1\n"
    (scripts / f"{name.replace('-', '_')}.py").write_text(body)
    if skill_md:
        (skill / "SKILL.md").write_text(f"# {name}\n")
    return skill


def test_layout_detection_single_skill_dir():
    skill = FIXTURES / "clean_tree" / "skills" / "clean-skill"
    dirs = resolve_skill_dirs(skill)
    assert dirs == [skill.resolve()]


def test_layout_detection_scripts_dir_resolves_to_its_skill():
    scripts = FIXTURES / "clean_tree" / "skills" / "clean-skill" / "scripts"
    dirs = resolve_skill_dirs(scripts)
    assert dirs == [scripts.parent.resolve()]


def test_layout_detection_skills_tree_passed_directly():
    tree = FIXTURES / "multi_tree" / "skills"
    dirs = resolve_skill_dirs(tree)
    assert [d.name for d in dirs] == ["alpha", "beta", "gamma"]


def test_layout_detection_repo_root_with_a_skills_tree():
    dirs = resolve_skill_dirs(FIXTURES / "multi_tree")
    assert [d.name for d in dirs] == ["alpha", "beta", "gamma"]


def test_layout_detection_non_layout_is_a_usage_error(tmp_path):
    with pytest.raises(UsageError, match="does not match any skill layout"):
        resolve_skill_dirs(tmp_path)


def test_layout_detection_file_path_is_a_usage_error():
    target = FIXTURES / "clean_tree" / "skills" / "clean-skill" / "SKILL.md"
    with pytest.raises(UsageError, match="not a directory"):
        resolve_skill_dirs(target)


def test_layout_detection_skills_tree_wins_over_a_root_that_also_has_scripts(tmp_path):
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "tool.py").write_text(DECORATED_SCRIPT)
    make_skill(root / "skills", "alpha")
    make_skill(root / "skills", "beta")
    dirs = resolve_skill_dirs(root)
    assert [d.name for d in dirs] == ["alpha", "beta"]


def test_layout_detection_non_skill_dir_with_scripts_does_not_shadow_the_skills_tree(tmp_path):
    root = tmp_path / "repo"
    examples = root / "examples"
    (examples / "scripts").mkdir(parents=True)
    (examples / "scripts" / "demo.py").write_text("x = 1\n")
    make_skill(root / "skills", "alpha")
    dirs = resolve_skill_dirs(root)
    assert [d.name for d in dirs] == ["alpha"]


def test_layout_detection_bare_scripts_dir_without_skill_md_or_decorator_is_not_a_skill(tmp_path):
    skill = make_skill(tmp_path, "bare", skill_md=False, decorated=False)
    with pytest.raises(UsageError, match="does not match any skill layout"):
        resolve_skill_dirs(skill)


def test_layout_detection_decorated_script_without_skill_md_is_a_skill(tmp_path):
    skill = make_skill(tmp_path, "decorated", skill_md=False, decorated=True)
    dirs = resolve_skill_dirs(skill)
    assert dirs == [skill.resolve()]
