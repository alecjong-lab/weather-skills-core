"""Lint fixture: no PEP 723 block. Never executed."""

from pathlib import Path

from weather_skills_core import Dataset, weather_skill

_SKILL_VERSION = "0.1.0"


@weather_skill(
    name="no-block",
    version=_SKILL_VERSION,
)
@weather_skill.argument("-i", "--input", type=Dataset("observations"), required=True, dest="input")
def no_block(ds):
    return ds


if __name__ == "__main__":
    no_block()
