# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core @ git+https://github.com/rhiza-research/weather-skills-core",
# ]
# ///
"""Lint fixture: version passed as a string literal. Never executed."""

from weather_skills_core import weather_skill

_SKILL_VERSION = "0.1.0"


@weather_skill(
    name="literal-version",
    version="0.1.0",
    inputs=["data"],
    outputs=["data"],
)
def literal_version(ds, **kwargs):
    """Lint fixture; never executed."""
    return ds


if __name__ == "__main__":
    literal_version()
