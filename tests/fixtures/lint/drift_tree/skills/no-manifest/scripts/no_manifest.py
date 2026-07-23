# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core @ git+https://github.com/rhiza-research/weather-skills-core",
# ]
# ///
"""Lint fixture: a skill directory without a SKILL.md. Never executed."""

from weather_skills_core import weather_skill

_SKILL_VERSION = "0.1.0"


@weather_skill(
    "no-manifest",
    _SKILL_VERSION,
    input_type="any",
    output_type="same",
)
def no_manifest(ds):
    """Lint fixture; never executed."""
    return ds


if __name__ == "__main__":
    no_manifest()
