"""Human-readable dataset labels for plots and legends."""

from __future__ import annotations

import json
import re
from pathlib import Path

from weather_skills_core.errors import UsageError
from weather_skills_core.provenance import origin_skill

_DATE_TAIL = re.compile(r"[_-]\d{4}-\d{2}-\d{2}(?:[_-]\d{2}-\d{2}-\d{2})?$")
_VAR_TAIL = re.compile(r"[_-](?:precip|tp)$", re.I)
_ACRONYMS = frozenset({"ecmwf", "s2s", "ghcn", "cmip6", "gefs", "oisst", "imerg", "smap", "chirps"})


def _prettify_token(text: str) -> str:
    parts = [p for p in re.split(r"[_-]+", text.strip()) if p]
    return " ".join(p.upper() if p.lower() in _ACRONYMS else p.capitalize() for p in parts) or text


def label_from_history(ds) -> str | None:
    raw = ds.attrs.get("weather_skills_history")
    if not raw:
        return None
    try:
        history = json.loads(raw) if isinstance(raw, str) else list(raw)
    except (TypeError, ValueError):
        return None
    skill = origin_skill(history)
    return _prettify_token(skill.removesuffix("-fetch")) if skill else None


def label_from_source_token(token: str) -> str:
    """Product name from a source token: ``scheme:product/…``, a path, or a bare id."""
    text = token.strip()
    if ":" in text:
        head, _, tail = text.partition(":")
        # The product id is the first path segment after the scheme.
        text = re.split(r"[/\\]", (tail or head).strip())[0].removesuffix(".zarr")
    elif "/" in text or "\\" in text or text.endswith(".zarr"):
        text = Path(text).stem
    text = _VAR_TAIL.sub("", _DATE_TAIL.sub("", text))
    return _prettify_token(text)


def dataset_display_label(ds, fallback) -> str:
    """Short product name from ``weather_skills_source``, provenance, or ``fallback``."""
    for token in (ds.attrs.get("weather_skills_source"), ds.encoding.get("source")):
        if isinstance(token, str) and (label := label_from_source_token(token)):
            return label
    return label_from_history(ds) or (fallback() if callable(fallback) else str(fallback))


def resolve_input_labels(
    labels: list[str] | None,
    n: int,
    *,
    input_flag: str = "--input",
) -> list[str | None]:
    """Map optional ``--label`` values to *n* inputs; ``None`` slots use auto labels."""
    if not labels or all(label is None for label in labels):
        return [None] * n
    if len(labels) != n:
        raise UsageError(f"expected {n} --label values (one per {input_flag}), got {len(labels)}")
    return list(labels)


def combine_display_labels(labels: list[str]) -> str:
    """Join unique labels for a shared row title (e.g. one forecast row)."""
    return " / ".join(dict.fromkeys(label for label in labels if label))
