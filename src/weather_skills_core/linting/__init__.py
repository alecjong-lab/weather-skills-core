"""Skill-conformance linter.

Lints weather-skill declarations against the ecosystem's conventions: the
standard parameter surface is enumerated by introspecting the decorator
(:func:`weather_skills_core.standard_args.standard_parameters`), and skill declarations are
read from the scripts by AST -- a linted script is never imported or run.

Advisory only: findings carry stable rule IDs and severities, a deterministic
rubric scores each skill, and the process exits 0 whether or not findings
exist (exit 2 is reserved for usage errors). The ``--strict`` flag makes
findings at or above a chosen severity exit 1 for callers that opt in;
nothing depends on it.
"""

from weather_skills_core.linting.run import run_lint

__all__ = ["run_lint"]
