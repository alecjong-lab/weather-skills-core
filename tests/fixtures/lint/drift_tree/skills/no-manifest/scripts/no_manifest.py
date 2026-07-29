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
    "no-manifest",
    _SKILL_VERSION,
    inputs=["data"],
    outputs=["data"],
    region="optional",
    extra_args=[
        (("--smoothing",), {"type": int, "help": "Smoothing window width in grid cells."}),
    ],
)
def no_manifest(ds, bbox, smoothing):
    """Lint fixture; never executed."""
    return ds


if __name__ == "__main__":
    no_manifest()
