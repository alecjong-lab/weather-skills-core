# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core @ git+https://github.com/rhiza-research/weather-skills-core",
# ]
# ///
"""Lint fixture: arguments shadowing standard I/O flags. Never executed."""

from weather_skills_core import weather_skill

_SKILL_VERSION = "0.1.0"

@weather_skill(
    name="shadow-skill",
    version=_SKILL_VERSION,
    inputs=["data"],
    outputs=["data"]
)
@weather_skill.argument("--input", help="Redeclares the standard --input flag (WSK101).")
@weather_skill.argument("--output", help="Redeclares the standard --output flag (WSK101).")
@weather_skill.argument("--date", help="Canonical date flag (not a shadow).")
@weather_skill.argument("--bbox", help="Canonical bbox flag (not a shadow).")
@weather_skill.argument("--period", choices=["daily", "weekly"], help="A legitimate one-off flag.")
def shadow_skill(ds, **kwargs):
    """Lint fixture; never executed."""
    return ds

if __name__ == "__main__":
    shadow_skill()
