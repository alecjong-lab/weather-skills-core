# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core @ git+https://github.com/rhiza-research/weather-skills-core",
# ]
# ///
"""Lint fixture: declares --method and --window with divergent shapes. Never executed."""

from weather_skills_core import weather_skill

_SKILL_VERSION = "0.1.0"


@weather_skill(
    "gamma",
    _SKILL_VERSION,
    input_type="any",
    output_type="same",
    extra_args={
        "method": {"choices": ["nearest", "linear"], "help": "Interpolator."},
        "window": {"type": float, "help": "Window width in degrees."},
    },
)
def gamma(ds, method, window):
    """Lint fixture; never executed."""
    return ds


if __name__ == "__main__":
    gamma()
