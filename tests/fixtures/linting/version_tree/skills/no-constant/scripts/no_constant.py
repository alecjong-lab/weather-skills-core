# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core @ git+https://github.com/rhiza-research/weather-skills-core",
# ]
# ///
"""Lint fixture: no _SKILL_VERSION constant. Never executed."""

from pathlib import Path

from weather_skills_core import Dataset, weather_skill


@weather_skill(
    name="no-constant",
    version="0.1.0",
)
@weather_skill.argument("-i", "--input", type=Dataset("observations"), required=True, dest='ds')
def no_constant(ds, **kwargs):
    """Lint fixture; never executed."""
    return ds


if __name__ == "__main__":
    no_constant()
