"""Lint-run orchestration and the deterministic score rubric.

Scoring: each skill's score is the mean of its per-rule scores, on a 0-100
scale. A rule with no findings for the skill scores 1.0; otherwise the rule
scores by its worst finding's severity -- ``error`` 0.0, ``warning`` 0.5,
``info`` 0.8. The rules in the denominator are the per-skill rules plus, when
a corpus beyond the target exists, the cross-skill rules; skipped rules are
excluded from the denominator entirely (never silently scored as clean). A
skill whose script could not be analyzed (WSK001) scores 0. The aggregate is
the mean of the per-skill scores. Scores inform; maintainers decide.
"""

from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

from weather_skills_core.lint.corpus import build_corpus
from weather_skills_core.lint.rules import (
    CROSS_SKILL_RULES,
    PER_SKILL_RULES,
    RULES,
    Finding,
    lint_corpus,
)

SEVERITY_RULE_SCORE = {"error": 0.0, "warning": 0.5, "info": 0.8}


@dataclass
class LintReport:
    findings: list[Finding]
    skills: list[dict]  # per target skill: name, file, score, ordered as linted
    aggregate: int
    skipped_rules: list[dict]  # {"rule", "title", "reason"}
    notes: list[str] = field(default_factory=list)


def skill_score(findings: list[Finding], corpus_available: bool) -> int:
    """The rubric score for one skill's findings."""
    if any(f.rule == "WSK001" for f in findings):
        return 0
    applicable = PER_SKILL_RULES + (CROSS_SKILL_RULES if corpus_available else ())
    per_rule = []
    for rule_id in applicable:
        rule_findings = [f for f in findings if f.rule == rule_id]
        if not rule_findings:
            per_rule.append(1.0)
        else:
            per_rule.append(min(SEVERITY_RULE_SCORE[f.severity] for f in rule_findings))
    return round(100 * sum(per_rule) / len(per_rule))


def run_lint(target: Path, against: list[str]) -> LintReport:
    """Lint ``target`` (with optional ``--against`` corpora) and build the report.

    Raises :class:`weather_skills_core.errors.UsageError` for an unlintable
    target path or an unresolvable ``--against`` value (exit 2 at the CLI).
    """
    with ExitStack() as stack:
        corpus, notes = build_corpus(target, against, stack)
        corpus_available = len(corpus) > 1
        findings = lint_corpus(corpus, corpus_available)

    skills = []
    scores = []
    for cs in corpus:
        if not cs.is_target:
            continue
        name = cs.decl.display_name
        skill_findings = [f for f in findings if f.skill == name]
        score = skill_score(skill_findings, corpus_available)
        skills.append({"name": name, "file": str(cs.decl.script), "score": score})
        scores.append(score)

    skipped = []
    if not corpus_available:
        for rule_id in CROSS_SKILL_RULES:
            skipped.append(
                {
                    "rule": rule_id,
                    "title": RULES[rule_id].title,
                    "reason": (
                        "no corpus beyond the target; lint within a skills tree or pass --against"
                    ),
                }
            )

    aggregate = round(sum(scores) / len(scores)) if scores else 0
    return LintReport(
        findings=findings,
        skills=skills,
        aggregate=aggregate,
        skipped_rules=skipped,
        notes=notes,
    )
