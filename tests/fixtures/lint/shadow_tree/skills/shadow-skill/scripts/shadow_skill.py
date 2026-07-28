# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core @ git+https://github.com/rhiza-research/weather-skills-core",
# ]
# ///
"""Lint fixture: extra_args entries shadowing standard parameters. Never executed."""

from weather_skills_core import types, weather_skill

_SKILL_VERSION = "0.1.0"


@weather_skill(
    "shadow-skill",
    _SKILL_VERSION,
    output_type=types.GRIDDED,
    extra_args={
        "date": {"help": "Redeclares the standard --date flag (WSK101)."},
        "dims": {"help": "Redeclares the standard --dims flag (WSK101)."},
        "title": {},
        "period": {"choices": ["daily", "weekly"], "help": "A legitimate one-off flag."},
    },
)
def shadow_skill(date, dims, title, period):
    """Lint fixture; never executed."""


if __name__ == "__main__":
    shadow_skill()
