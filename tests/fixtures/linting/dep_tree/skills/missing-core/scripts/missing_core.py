# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "xarray",
# ]
# ///
"""Lint fixture: PEP 723 missing weather-skills-core. Never executed."""

from pathlib import Path

from weather_skills_core import Dataset, weather_skill

_SKILL_VERSION = "0.1.0"


@weather_skill(
    name="missing-core",
    version=_SKILL_VERSION,
)
@weather_skill.argument("-i", "--input", type=Dataset("observations"), required=True, dest="input")
def missing_core(ds):
    return ds


if __name__ == "__main__":
    missing_core()
