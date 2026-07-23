"""Lint-target layout detection and cross-skill corpus resolution.

The lint target resolves to one or more skill directories by layout
auto-detection. The cross-skill corpus is the target's own tree, plus upward
discovery when the target is a single skill inside an enclosing skills tree
(siblings are context only: findings are reported for the target alone), plus
every ``--against`` value -- a local path or a GitHub repository reference.

A GitHub reference is fetched with a shallow, blob-filtered, sparse ``git
clone`` into a temporary directory that only the declaration files are read
from and that is removed when the lint run ends; no clone is retained and no
credentials are used (public repositories only).
"""

import os
import re
import subprocess
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from weather_skills_core.errors import UsageError
from weather_skills_core.lint.extract import SkillDeclaration, extract_skill

_GITHUB_REF_RE = re.compile(
    r"^(?:https?://github\.com/)?"
    r"(?P<org>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)"
    r"(?:\.git)?(?:@(?P<rev>[^@/]+))?$"
)


@dataclass
class CorpusSkill:
    """One declaration in the corpus, labeled with where it came from."""

    decl: SkillDeclaration
    source: str  # "target", the enclosing tree path, or "--against <ref>"
    is_target: bool


def _is_skill_dir(path: Path) -> bool:
    scripts_dir = path / "scripts"
    return scripts_dir.is_dir() and any(scripts_dir.glob("*.py"))


def _skill_children(path: Path) -> list[Path]:
    if not path.is_dir():
        return []
    return [child for child in sorted(path.iterdir()) if _is_skill_dir(child)]


def resolve_skill_dirs(path: Path) -> tuple[list[Path], bool]:
    """Auto-detect the layout at ``path`` and return ``(skill dirs, single_skill)``.

    Recognized layouts, checked in order: a single skill directory
    (``scripts/*.py``, normally alongside a SKILL.md -- a missing SKILL.md is
    a lint finding, not a layout failure); a ``scripts`` directory (the skill
    is its parent); a directory of skill directories (a ``skills/`` tree
    passed directly); a repo root holding a ``skills/`` tree. Anything else
    raises :class:`UsageError` (exit 2).
    """
    p = path.resolve()
    if not p.is_dir():
        raise UsageError(
            f"{path} is not a directory; lint a skill directory, a scripts directory, "
            "a skills/ tree, or a repo root holding one."
        )
    if _is_skill_dir(p):
        return [p], True
    if p.name == "scripts" and any(p.glob("*.py")):
        return [p.parent], True
    children = _skill_children(p)
    if children:
        return children, False
    nested = _skill_children(p / "skills")
    if nested:
        return nested, False
    raise UsageError(
        f"{path} does not match any skill layout (no scripts/*.py, no */scripts/*.py, no skills/*)."
    )


def sibling_skills(skill_dir: Path) -> list[Path]:
    """Skill directories that share the target's enclosing tree (upward discovery)."""
    parent = skill_dir.resolve().parent
    return [child for child in _skill_children(parent) if child != skill_dir.resolve()]


def github_clone_url(reference: str) -> str:
    """The public HTTPS clone URL for a GitHub repository reference."""
    m = _GITHUB_REF_RE.match(reference)
    if m is None:
        raise UsageError(f"--against {reference}: not a GitHub repository reference.")
    return f"https://github.com/{m['org']}/{m['repo']}.git"


def _run_git(args: list[str], reference: str) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise UsageError(
            f"--against {reference}: could not fetch the repository"
            + (f" ({detail[-1]})" if detail else "")
        )
    return result


def _fetch_github(reference: str, dest: Path) -> None:
    """Shallow-fetch just the declaration files of a public GitHub repository.

    ``git clone --depth 1 --filter=blob:none --sparse`` transfers the latest
    commit only, with file contents fetched on demand; the sparse checkout is
    then narrowed to ``skills/`` so only the skill declarations are
    materialized. A repository without a ``skills/`` directory falls back to a
    full (still shallow) checkout so layout detection can run on it.
    """
    m = _GITHUB_REF_RE.match(reference)
    rev = m["rev"] if m else None
    clone_args = ["clone", "--quiet", "--depth", "1", "--filter=blob:none", "--sparse"]
    if rev:
        clone_args += ["--branch", rev]
    clone_args += [github_clone_url(reference), str(dest)]
    _run_git(clone_args, reference)
    _run_git(["-C", str(dest), "sparse-checkout", "set", "skills"], reference)
    if not (dest / "skills").is_dir():
        _run_git(["-C", str(dest), "sparse-checkout", "disable"], reference)


def resolve_against(value: str, stack: ExitStack) -> list[Path]:
    """Skill directories for one ``--against`` value.

    An existing local path is layout-detected in place. Anything else must be
    a GitHub repository reference (``org/repo``, ``org/repo@rev``, or an
    ``https://github.com/...`` URL), fetched into a temporary directory
    registered on ``stack`` for removal when the lint run ends.
    """
    local = Path(value)
    if local.exists():
        dirs, _ = resolve_skill_dirs(local)
        return dirs
    if _GITHUB_REF_RE.match(value) and "/" in value:
        tmpdir = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="wsk-against-")))
        _fetch_github(value, tmpdir / "repo")
        dirs, _ = resolve_skill_dirs(tmpdir / "repo")
        return dirs
    raise UsageError(
        f"--against {value}: not an existing local path or a GitHub repository "
        "reference (org/repo[@rev])."
    )


def build_corpus(
    target_path: Path, against: list[str], stack: ExitStack
) -> tuple[list[CorpusSkill], list[str]]:
    """Resolve the full corpus for a lint run.

    Returns the corpus (target declarations first) and corpus notes (context
    skills that could not be analyzed and were excluded). Target declarations
    keep their extraction errors -- those become per-skill findings.
    """
    notes: list[str] = []
    target_dirs, single_skill = resolve_skill_dirs(target_path)
    corpus: list[CorpusSkill] = [
        CorpusSkill(decl=decl, source="target", is_target=True)
        for skill_dir in target_dirs
        for decl in extract_skill(skill_dir)
    ]

    context_dirs: list[tuple[Path, str]] = []
    if single_skill:
        tree = target_dirs[0].resolve().parent
        context_dirs += [(sibling, str(tree)) for sibling in sibling_skills(target_dirs[0])]
    for value in against:
        context_dirs += [
            (skill_dir, f"--against {value}") for skill_dir in resolve_against(value, stack)
        ]

    for skill_dir, source in context_dirs:
        for decl in extract_skill(skill_dir):
            if decl.error is not None:
                notes.append(
                    f"corpus skill {decl.display_name} ({source}) could not be analyzed "
                    f"and was excluded: {decl.error}"
                )
                continue
            corpus.append(CorpusSkill(decl=decl, source=source, is_target=False))
    return corpus, notes
