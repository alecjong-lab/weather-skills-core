# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core @ git+https://github.com/rhiza-research/weather-skills-core",
# ]
# ///
"""Lint fixture. Never executed."""

from pathlib import Path

from weather_skills_core import Dataset, weather_skill

_SKILL_VERSION = "0.1.0"


@weather_skill(
    name="no-manifest",
    version=_SKILL_VERSION,
)
@weather_skill.argument("-i", "--input", type=Dataset("observations"), required=True, dest="ds")
@weather_skill.argument("--bbox")
@weather_skill.argument("--smoothing", type=int, help="Smoothing window width in grid cells.")
def no_manifest(ds, **kwargs):
    """Lint fixture; never executed."""
    return ds


if __name__ == "__main__":
    no_manifest()
