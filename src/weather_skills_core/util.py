"""Small runtime helpers shared by skill bodies: transient-error
classification and environment-credential checks."""

import os

from weather_skills_core.errors import UsageError

_TRANSIENT_MARKERS = ("429", "500", "502", "503", "504", "timed out", "timeout", "connection")


def is_transient(exc: Exception) -> bool:
    """Heuristic: does this error look like a retryable transient/rate-limit?

    Matches on the error text: an HTTP 429/5xx status anywhere in the message
    (client libraries that raise bare Exceptions carry the status in the
    text), or a timeout/connection marker from requests/urllib3-style
    transport errors. Retry policy -- how many retries, what backoff -- stays
    with the caller.
    """
    text = str(exc).lower()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def require_env(*names: str, message: str | None = None) -> tuple:
    """Return the values of the named environment variables, in order.

    A variable that is unset or empty is missing; when any are, raises
    :class:`UsageError` with ``message``, or with the default
    ``missing required env var(s): <the missing names, comma-separated>``.
    The values are returned for the caller to hand to its auth library --
    never print, log, or echo them.
    """
    values = [os.environ.get(name) for name in names]
    missing = [name for name, value in zip(names, values, strict=True) if not value]
    if missing:
        raise UsageError(message or f"missing required env var(s): {', '.join(missing)}")
    return tuple(values)
