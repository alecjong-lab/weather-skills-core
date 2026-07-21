"""Typed exceptions mapped to the weather-skills exit-code conventions.

The CLI wrapper built by ``@weather_skill`` catches these, prints
``Error: <message>`` to stderr (exactly ``<message>`` when raised with
``prefix=False``), and exits with the exception's ``exit_code``. Raising them
from library code (or from a wrapped skill function) is the supported way to
fail: usage/validation problems exit 2 and must occur before any network or
data work; data-availability and hard runtime failures exit 1.
"""


class SkillError(Exception):
    """Base class for errors converted to a clean CLI exit.

    ``prefix=False`` suppresses the ``Error: `` prefix on the printed stderr
    line, for skills whose stderr text or exit code is machine-consumed and
    must appear exactly as given.
    """

    exit_code = 1

    def __init__(self, *args, prefix: bool = True):
        super().__init__(*args)
        self.prefix = prefix


class UsageError(SkillError):
    """Usage or validation failure. Exits 2, before any network call."""

    exit_code = 2


class DataError(SkillError):
    """Data-availability or hard runtime failure. Exits 1."""

    exit_code = 1
