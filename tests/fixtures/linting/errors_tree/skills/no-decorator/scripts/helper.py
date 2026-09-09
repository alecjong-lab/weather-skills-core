# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = []
# ///
"""Lint fixture: a script with no weather_skill decorator call (WSK001)."""


def helper():
    """A plain function; nothing declares a skill here."""
    return 0


if __name__ == "__main__":
    helper()
