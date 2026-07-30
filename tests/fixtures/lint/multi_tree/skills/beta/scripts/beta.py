# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core @ git+https://github.com/rhiza-research/weather-skills-core",
# ]
# ///
"""Lint fixture. Never executed."""

from weather_skills_core import weather_skill

_SKILL_VERSION = "0.1.0"

@weather_skill(
    "beta",
    _SKILL_VERSION,
    inputs=["data"],
    outputs=["data"]
)
@weather_skill.argument("--method", type=str, help="Aggregation method.", default="mean")
def beta(ds, **kwargs):
    """Lint fixture; never executed."""
    return ds

if __name__ == "__main__":
    beta()
