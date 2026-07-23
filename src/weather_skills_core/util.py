"""Small runtime helpers shared by skill bodies: transient-error
classification and environment-credential checks."""

import os
import re

from weather_skills_core.errors import UsageError

# Retryable HTTP status codes, matched as whole tokens so a substring such as
# the 429 inside "14290" or a status in a longer number does not trip.
_STATUS_RE = re.compile(r"\b(?:429|500|502|503|504)\b")
# Timeout markers stay substrings: they carry no digits to collide with.
_TIMEOUT_MARKERS = ("timed out", "timeout")
# Specific connection phrases only. Bare "connection" appears in urllib3's
# "HTTPSConnectionPool(...)" prefix on EVERY pool error, permanent ones
# included, so matching it would misclassify a wrapped 404 as transient.
_CONNECTION_MARKERS = (
    "connection error",
    "connection reset",
    "connection refused",
    "connection aborted",
)


def is_transient(exc: Exception) -> bool:
    """Heuristic: does this error look like a retryable transient/rate-limit?

    Matches on the error text: an HTTP 429/5xx status as a whole-number token
    (client libraries that raise bare Exceptions carry the status in the
    text), a timeout marker (``timed out``/``timeout``), or a specific
    connection-failure phrase (connection error/reset/refused/aborted) from
    requests/urllib3-style transport errors. Matching is case-insensitive.
    Retry policy -- how many retries, what backoff -- stays with the caller.
    """
    text = str(exc).lower()
    if _STATUS_RE.search(text):
        return True
    if any(marker in text for marker in _TIMEOUT_MARKERS):
        return True
    return any(marker in text for marker in _CONNECTION_MARKERS)


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
