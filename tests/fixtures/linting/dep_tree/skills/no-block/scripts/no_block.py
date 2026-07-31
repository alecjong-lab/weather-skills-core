"""Lint fixture: no PEP 723 block. Never executed."""

from weather_skills_core import weather_skill

_SKILL_VERSION = "0.1.0"


@weather_skill(name="no-block", version=_SKILL_VERSION, inputs=["data"], outputs=["data"])
def no_block(ds):
    return ds


if __name__ == "__main__":
    no_block()
