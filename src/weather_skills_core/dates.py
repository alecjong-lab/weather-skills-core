"""Relative-or-absolute date grammar for weather-skill CLIs.

Implements the value grammar shared by ``--start``, ``--end``, and ``--date``:

- an absolute ISO date ``YYYY-MM-DD``;
- ``now`` or ``today`` -- the current UTC date;
- ``latest`` -- the newest date with available data, discovered per source
  through a caller-supplied resolver;
- an offset ``now-<int>{d|w}`` or ``latest-<int>{d|w}`` -- the base minus N
  (``w`` = 7 days). The offset count is capped at 36525 days. Future ``+``
  offsets, month/year units, and anything else are rejected with a
  :class:`~weather_skills_core.errors.UsageError` before any network call.

Boundary handling for ``--start``/``--end``: absolute endpoints and ordinary
relative ranges are inclusive of both ends. The one exception is the duration
idiom -- start is ``B-<int>{d|w}`` and end is exactly the same base token ``B``
(both ``now``, or both ``latest``): the window is exactly N days, inclusive of
``B``, with the far edge shifted in by one (``latest-3w .. latest`` resolves to
``[latest-20d, latest]`` = 21 days inclusive of ``latest``; ``now-1w .. now``
is 7 days). Tokens stay literal -- ``latest-3w`` always means ``latest - 21d``;
only the ``B-N .. B`` shape moves the far edge. After resolution,
``start <= end`` or a :class:`UsageError` is raised (pre-network).

The ``latest`` resolver is invoked lazily and at most once per resolution: an
all-absolute or ``now``-only window performs no discovery call, and a window
referencing ``latest`` at both ends discovers once.
"""

import re
from datetime import UTC, date, datetime, timedelta

from weather_skills_core.errors import DataError, UsageError

_REL_OFFSET_RE = re.compile(r"^(?P<base>now|latest)-(?P<n>\d+)(?P<unit>[dw])$")

# Strict absolute-date shape. date.fromisoformat on 3.11+ also accepts compact
# (20260501) and ISO-week (2026-W18-1) forms; the documented grammar is exactly
# YYYY-MM-DD, so we gate on this regex first and reject the looser forms.
_ABS_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Upper bound on a relative offset's resolved day count. 36525 days (~100 years)
# is far beyond any real window yet small enough that the date arithmetic cannot
# raise OverflowError. Rejecting above this cap keeps the failure pre-network.
MAX_OFFSET_DAYS = 36525


def np_to_date(value) -> date:
    """Convert a numpy datetime64 to a calendar date (truncating any time-of-day).

    A NaT (not-a-time) value raises :class:`DataError` with an actionable
    message: ``np.datetime_as_string`` renders NaT as the string ``"NaT"``,
    which ``date.fromisoformat`` would reject with an opaque ``ValueError``.
    """
    import numpy as np

    if np.isnat(value):
        raise DataError(
            "time coordinate value is NaT (not-a-time); the dataset has a missing or "
            "unfilled time entry where a valid date is required."
        )
    return date.fromisoformat(np.datetime_as_string(value, unit="D"))


def today_utc(args=None) -> date:
    """The current UTC date.

    Shaped as a ``latest_resolver`` (it accepts and ignores the parsed args),
    for sources with no cheap day-precise discovery endpoint where ``latest``
    resolves to today and a thin not-yet-published trailing tail is handled as
    a normal partial window.
    """
    return datetime.now(UTC).date()


def parse_token(value: str) -> tuple:
    """Parse a date value into a structured token.

    Returns one of:
      ("abs", date)                              absolute YYYY-MM-DD
      ("base", "now")                            current UTC date
      ("base", "latest")                         newest available date (resolved later)
      ("offset", "now", n_days, unit_phrase)     now minus n_days
      ("offset", "latest", n_days, unit_phrase)  latest minus n_days

    ``unit_phrase`` describes the offset in its requested units for the log
    line (e.g. "3-week", "7-day"). Raises :class:`UsageError` for anything else
    (months/years, future "+", malformed), so the failure happens before any
    network call. "today" is accepted as an alias for "now".
    """
    if value in ("now", "today"):
        return ("base", "now")
    if value == "latest":
        return ("base", "latest")
    m = _REL_OFFSET_RE.match(value)
    if m is not None:
        n = int(m.group("n"))
        if n < 1:
            raise UsageError(
                f"invalid date value {value!r}: offset must be >= 1 (e.g. now-1d, latest-3w)"
            )
        unit = m.group("unit")
        n_days = n * 7 if unit == "w" else n
        if n_days > MAX_OFFSET_DAYS:
            raise UsageError(
                f"invalid date value {value!r}: offset resolves to {n_days} days, "
                f"above the maximum of {MAX_OFFSET_DAYS} days (~100 years)"
            )
        unit_phrase = f"{n}-{'week' if unit == 'w' else 'day'}"
        return ("offset", m.group("base"), n_days, unit_phrase)
    if _ABS_DATE_RE.match(value):
        try:
            return ("abs", date.fromisoformat(value))
        except ValueError:
            pass
    raise UsageError(
        f"invalid date value {value!r}: expected an absolute date YYYY-MM-DD, "
        "'now'/'today', 'latest', or an offset 'now-<int>{d|w}' / "
        "'latest-<int>{d|w}'"
    )


def _memoize(latest_fn):
    """Wrap a zero-arg ``latest`` resolver so it runs at most once."""
    cache: dict = {}

    def resolver() -> date:
        if "value" not in cache:
            cache["value"] = latest_fn()
        return cache["value"]

    return resolver


def _require_latest_fn(latest_fn):
    if latest_fn is None:
        raise UsageError(
            "invalid date value 'latest': this skill has no 'latest' resolver; "
            "use an absolute date or 'now'"
        )
    return latest_fn


def _token_base_date(tok: tuple, now: date, latest_fn) -> date:
    """Resolve a parsed token's base date.

    ``now`` is the current UTC date. ``latest_fn`` is a zero-arg callable
    returning the newest available date; invoked only when a token references
    ``latest``.
    """
    kind = tok[0]
    if kind == "abs":
        return tok[1]
    base = tok[1]
    base_date = now if base == "now" else _require_latest_fn(latest_fn)()
    if kind == "base":
        return base_date
    return base_date - timedelta(days=tok[2])


def resolve_window(start_value: str, end_value: str, latest_fn=None) -> tuple:
    """Resolve ``--start``/``--end`` values to concrete inclusive (start, end) dates.

    Applies the value grammar and the boundary rules:
      - absolute endpoints and ordinary relative ranges are inclusive both ends;
      - the DURATION IDIOM (start is ``B-<int>{d|w}`` and end is exactly the
        same base token ``B``, both ``now`` or both ``latest``) yields an N-day
        window inclusive of the base, with the far edge shifted in by one.

    ``latest_fn`` is a zero-arg callable returning the newest available date;
    it is memoized here and invoked at most once, and only when a token
    references ``latest``.

    Returns ``(start_date, end_date, log_line)`` where ``log_line`` is a stderr
    message to print before fetching when any relative token is present, else
    None. Raises :class:`UsageError` (pre-network) on a malformed token or a
    reversed range.
    """
    start_tok = parse_token(start_value)
    end_tok = parse_token(end_value)

    if latest_fn is not None:
        latest_fn = _memoize(latest_fn)

    relative_used = start_tok[0] != "abs" or end_tok[0] != "abs"
    now = datetime.now(UTC).date()

    # Duration idiom: start is an offset off base B, end is exactly base B.
    duration = start_tok[0] == "offset" and end_tok[0] == "base" and start_tok[1] == end_tok[1]

    start_date = _token_base_date(start_tok, now, latest_fn)
    end_date = _token_base_date(end_tok, now, latest_fn)

    if duration:
        # Window is exactly N days, inclusive of the base end, far edge shifted
        # in by one: start moves forward one day so [end-(N-1), end] spans N days.
        n_days = start_tok[2]
        start_date = end_date - timedelta(days=n_days - 1)
        reason = f"duration mode: {start_tok[3]} window inclusive of {start_tok[1]}"
    else:
        reason = "inclusive both ends"

    if start_date > end_date:
        raise UsageError(
            f"resolved --start {start_date.isoformat()} is after resolved "
            f"--end {end_date.isoformat()}; the range is reversed."
        )

    log_line = None
    if relative_used:
        span = (end_date - start_date).days + 1
        log_line = (
            f'resolved "{start_value}".."{end_value}" -> '
            f"{start_date.isoformat()}..{end_date.isoformat()} "
            f"({span} days; {reason})"
        )
    return start_date, end_date, log_line


def resolve_date(value: str, latest_fn=None, context: str = "single date") -> tuple:
    """Resolve a single ``--date`` value to a concrete date.

    Applies the value grammar; both ends being inclusive is moot for a single
    date. ``latest_fn`` is invoked only when the token references ``latest``,
    and at most once. ``context`` labels the resolution in the log line (e.g.
    "single forecast init date").

    Returns ``(resolved_date, log_line)`` where ``log_line`` is a stderr
    message to print before fetching when a relative token is used, else None.
    Raises :class:`UsageError` (pre-network) on a malformed token.
    """
    tok = parse_token(value)

    if tok[0] == "abs":
        return tok[1], None

    now = datetime.now(UTC).date()
    base = tok[1]
    base_date = now if base == "now" else _require_latest_fn(latest_fn)()
    resolved = base_date if tok[0] == "base" else base_date - timedelta(days=tok[2])
    log_line = f'resolved "{value}" -> {resolved.isoformat()} ({context})'
    return resolved, log_line
