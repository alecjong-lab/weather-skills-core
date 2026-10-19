"""Lint-run orchestration: find the skills under a path, lint them, and score them.

Scoring: each skill's score is the mean of its per-rule scores, on a 0-100
scale. A rule with no findings for the skill scores 1.0; otherwise the rule
scores by its worst finding's severity -- ``error`` 0.0, ``warning`` 0.5,
``info`` 0.8. The rules in the denominator are the active rules other than
WSK001; rules not in the resolved rule set are excluded entirely. A skill
whose script could not be analyzed (WSK001) scores 0. The aggregate is
the mean of the per-skill scores. Scores inform; maintainers decide.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from weather_skills_core.errors import UsageError
from weather_skills_core.linting.extract import extract_skill
from weather_skills_core.linting.rules import (
    RULES,
    Finding,
    default_rule_set,
    expand_selector,
    lint_skills,
)

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


SEVERITY_RULE_SCORE = {"error": 0.0, "warning": 0.5, "info": 0.8}


def resolve_rule_set(
    select: list[str] | None = None,
    extend_select: list[str] | None = None,
    ignore: list[str] | None = None,
) -> set[str]:
    """Resolve the active rule set with ruff's select/extend-select/ignore semantics.

    ``base`` is the codes named by ``select`` if any were given, else the
    default set; then ``extend_select`` is unioned on; then ``ignore`` is
    subtracted. Each selector is a full rule code (``WSK301``) or a category
    prefix (``WSK4`` matches every ``WSK4xx``); all three arguments are lists
    (repeatable flags). A selector matching no known rule code raises
    :class:`weather_skills_core.errors.UsageError` (exit 2 at the CLI); an
    ``ignore`` selector for a rule that is valid but not in the active set is a
    silent no-op.
    """

    def _expand(selectors: list[str] | None, flag: str) -> set[str]:
        codes: set[str] = set()
        for selector in selectors or []:
            matched = expand_selector(selector)
            if not matched:
                raise UsageError(
                    f"unknown lint rule selector {selector!r} for {flag}: "
                    "matches no known rule code."
                )
            codes |= matched
        return codes

    base = _expand(select, "--select") if select else default_rule_set()
    active = base | _expand(extend_select, "--extend-select")
    active -= _expand(ignore, "--ignore")
    return active


@dataclass
class LintReport:
    findings: list[Finding]
    skills: list[dict]  # per target skill: name, file, score, ordered as linted
    aggregate: int
    notes: list[str] = field(default_factory=list)


def skill_score(findings: list[Finding], active_rules: set[str]) -> int:
    """The rubric score for one skill's findings, over the active rules only."""
    if any(f.rule == "WSK001" for f in findings):
        return 0
    applicable = sorted(
        rule_id for rule_id in RULES if rule_id != "WSK001" and rule_id in active_rules
    )
    if not applicable:
        return 100
    per_rule = []
    for rule_id in applicable:
        rule_findings = [f for f in findings if f.rule == rule_id]
        if not rule_findings:
            per_rule.append(1.0)
        else:
            per_rule.append(min(SEVERITY_RULE_SCORE[f.severity] for f in rule_findings))
    return round(100 * sum(per_rule) / len(per_rule))


def run_lint(
    target: Path,
    select: list[str] | None = None,
    extend_select: list[str] | None = None,
    ignore: list[str] | None = None,
) -> LintReport:
    """Lint every skill under ``target`` and build the report.

    ``select``/``extend_select``/``ignore`` are the ruff-style rule selectors
    (see :func:`resolve_rule_set`); with none given, every rule runs.

    Raises :class:`weather_skills_core.errors.UsageError` for an unlintable
    target path or an unknown rule selector (exit 2 at the CLI).
    """
    active_rules = resolve_rule_set(select, extend_select, ignore)
    decls = [decl for skill_dir in resolve_skill_dirs(target) for decl in extract_skill(skill_dir)]
    findings = lint_skills(decls, active_rules)

    skills = []
    scores = []
    notes = []
    for decl in decls:
        # Group findings by the script path, not the display name: two scripts
        # in one skill directory can share a name, and the file path is their
        # collision-proof identity (it is what _finding records).
        skill_findings = [f for f in findings if f.file == str(decl.script)]
        score = skill_score(skill_findings, active_rules)
        skills.append(
            {"name": decl.display_name, "key": decl.key, "file": str(decl.script), "score": score}
        )
        scores.append(score)
        # Surface extraction notes (a dynamic argument, a skipped second
        # decorated function, a duplicate PEP 723 block) where render prints them.
        if decl.error is None:
            notes += [f"{decl.key}: {note}" for note in decl.notes]

    aggregate = round(sum(scores) / len(scores)) if scores else 0
    return LintReport(findings=findings, skills=skills, aggregate=aggregate, notes=notes)
