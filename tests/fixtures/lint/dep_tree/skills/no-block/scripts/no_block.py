"""Lint fixture: no PEP 723 script block (WSK402). Never executed."""

from weather_skills_core import weather_skill

_SKILL_VERSION = "0.1.0"


@weather_skill(
    "no-block",
    _SKILL_VERSION,
    input_type="any",
    output_type="same",
)
def no_block(ds):
    """Lint fixture; never executed."""
    return ds


if __name__ == "__main__":
    no_block()
