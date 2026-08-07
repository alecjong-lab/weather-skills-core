# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core @ git+https://github.com/rhiza-research/weather-skills-core",
# ]
# ///
"""Lint fixture: non-canonical spelling of a standard flag. Never executed."""

from pathlib import Path

from weather_skills_core import Dataset, weather_skill

_SKILL_VERSION = "0.1.0"

@weather_skill(
    name="shadow-skill",
    version=_SKILL_VERSION,
)
@weather_skill.argument("-i", "--input", type=Dataset("observations"), required=True, dest="ds")
@weather_skill.argument("-b", dest="bbox", help="Non-canonical bbox spelling (WSK101).")
@weather_skill.argument("--date", help="Canonical date flag (not a shadow).")
@weather_skill.argument("--period", choices=["daily", "weekly"], help="A legitimate one-off flag.")
def shadow_skill(ds, output, bbox=None, date=None, period=None, **kwargs):
    """Lint fixture; never executed."""
    return ds

if __name__ == "__main__":
    shadow_skill()
