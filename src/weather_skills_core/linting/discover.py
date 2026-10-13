"""Lint-target layout detection: which skill directories a path holds."""

import re
from pathlib import Path

from weather_skills_core.errors import UsageError
from weather_skills_core.linting.extract import SkillDeclaration, extract_skill

#: A ``@weather_skill`` decorator application, matched on source text (not the
#: AST) so a script that does not parse still marks its directory as a skill.
_DECORATOR_RE = re.compile(r"@\s*(?:[A-Za-z_][\w.]*\.)?weather_skill\s*\(")


def _is_skill_dir(path: Path) -> bool:
    """A skill directory holds ``scripts/*.py`` plus evidence of skill-ness.

    The evidence is a ``SKILL.md`` manifest or a script applying the
    ``@weather_skill`` decorator; a bare ``scripts/*.py`` directory (an
    ``examples/`` folder, a tools directory) is not a skill and must not
    shadow a real ``skills/`` tree during layout detection.
    """
    scripts_dir = path / "scripts"
    if not scripts_dir.is_dir() or not any(scripts_dir.glob("*.py")):
        return False
    if (path / "SKILL.md").is_file():
        return True
    for script in scripts_dir.glob("*.py"):
        try:
            source = script.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _DECORATOR_RE.search(source):
            return True
    return False


def _skill_children(path: Path) -> list[Path]:
    if not path.is_dir():
        return []
    return [child for child in sorted(path.iterdir()) if _is_skill_dir(child)]


def resolve_skill_dirs(path: Path) -> list[Path]:
    """Auto-detect the layout at ``path`` and return its skill directories.

    Recognized layouts, checked in order: a repo root holding a ``skills/``
    tree; a directory of skill directories (a ``skills/`` tree passed
    directly); a single skill directory (``scripts/*.py`` plus a SKILL.md or
    a ``@weather_skill``-decorated script); a ``scripts`` directory (the
    skill is its parent). A ``skills/`` tree wins over the other layouts, so
    a repo root that also carries its own ``scripts/*.py`` (or non-skill
    directories that do) resolves to the tree rather than misdetecting as
    one skill. Anything else raises :class:`UsageError` (exit 2).
    """
    p = path.resolve()
    if not p.is_dir():
        raise UsageError(
            f"{path} is not a directory; lint a skill directory, a scripts directory, "
            "a skills/ tree, or a repo root holding one."
        )
    try:
        nested = _skill_children(p / "skills")
        if nested:
            return nested
        children = _skill_children(p)
        if children:
            return children
        if _is_skill_dir(p):
            return [p]
        if p.name == "scripts" and any(p.glob("*.py")):
            return [p.parent]
    except OSError as exc:
        raise UsageError(f"could not scan {path} for skill layouts: {exc}") from exc
    raise UsageError(
        f"{path} does not match any skill layout (no scripts/*.py alongside a SKILL.md "
        "or a @weather_skill script, no */scripts/*.py, no skills/*)."
    )


def discover_skills(target: Path) -> list[SkillDeclaration]:
    """Every skill script declaration under ``target``, in directory order."""
    return [decl for skill_dir in resolve_skill_dirs(target) for decl in extract_skill(skill_dir)]
