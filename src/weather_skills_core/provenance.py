"""Provenance chain handling for weather-skill artifacts.

The canonical provenance attr is ``weather_skills_history``: a JSON-encoded
append-only array of entries, ordered oldest first along the pipeline. Each
entry carries ``skill``, ``version``, ``args`` (the argparse namespace minus
input/output path strings, with resolved absolute dates), and ``input``:

- fetchers: ``None`` (no upstream zarr);
- single-input transformers: ``{"basename": ..., "hash": ...}`` where ``hash``
  is a sha256 over the upstream zarr's stored bytes (the hash may be deferred
  until after a cheap cache check);
- multi-input transformers: a list of ``{"basename", "hash", "history"}``
  dicts in input order, where ``history`` is that input's full chain;
- ``reference_inputs``: an optional sibling key on the entry listing
  ``{"basename", "hash"}`` for secondary reference stores (e.g. a reference
  grid) whose content must enter the cache key.

Legacy artifacts carry the same data under the ``rhiza_`` attr prefix
(``rhiza_history``, ``rhiza_source``, ``rhiza_forecast_init``); reads fall
back to it and writes migrate it forward.
"""

import hashlib
import json
import sys
from pathlib import Path

HISTORY_ATTR = "weather_skills_history"
SOURCE_ATTR = "weather_skills_source"

# compatibility read/migration for the rhiza_ attr prefix; scheduled for removal
LEGACY_HISTORY_ATTR = "rhiza_history"
LEGACY_ATTRS = ("rhiza_history", "rhiza_source", "rhiza_forecast_init")

DEFAULT_SOFTWARE = "forecasting-skills"


def hash_zarr(zarr_path: Path) -> str:
    """Stable content hash of a zarr's stored bytes. Walks the zarr dir
    deterministically and hashes relative-path bytes + each file's
    content. Returns sha256 hex digest."""
    zarr_path = Path(zarr_path)
    h = hashlib.sha256()
    for p in sorted(zarr_path.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(zarr_path)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def load_history(zarr_path: Path) -> list:
    """Read an artifact's provenance chain, tolerating absence and malformation.

    Falls back to the legacy ``rhiza_history`` attr. A not-yet-existing or
    unreadable store is a silent miss (empty chain). A present-but-non-array
    value is malformed under the ``weather_skills_history`` contract; it is
    treated as no history with a one-line stderr warning pointing at
    ``provenance --check``. The coercion is array-level only: an array whose
    individual entries are imperfect is passed through unchanged.
    """
    zarr_path = Path(zarr_path)
    try:
        import xarray as xr

        with xr.open_zarr(zarr_path, consolidated=False) as ds:
            raw = ds.attrs.get(HISTORY_ATTR) or ds.attrs.get(LEGACY_HISTORY_ATTR)
    except (FileNotFoundError, KeyError, ValueError):
        # A not-yet-existing or unreadable output during a cache check is a miss.
        return []
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    if not isinstance(parsed, list):
        print(
            f"ignoring malformed weather_skills_history on {zarr_path}; "
            "run `provenance --check` for details",
            file=sys.stderr,
        )
        return []
    return parsed


def input_ref(path: Path, *, include_hash: bool = True) -> dict:
    """Build a single-input ``input`` value: ``{basename[, hash]}``.

    With ``include_hash=False`` the (expensive) content hash is omitted so a
    cheap cache pre-check can run first; complete the entry with a hashed ref
    on a miss before stamping.
    """
    path = Path(path)
    ref = {"basename": path.name}
    if include_hash:
        ref["hash"] = hash_zarr(path)
    return ref


def multi_input_ref(paths, histories) -> list:
    """Build a multi-input ``input`` value: per-input ``{basename, hash, history}``.

    ``histories`` holds each input's full chain (``[]`` when the input had
    none), in the same order as ``paths``.
    """
    paths = [Path(p) for p in paths]
    return [
        {"basename": p.name, "hash": hash_zarr(p), "history": h}
        for p, h in zip(paths, histories, strict=True)
    ]


def reference_ref(paths) -> list:
    """Build a ``reference_inputs`` sibling value: per-reference ``{basename, hash}``."""
    return [{"basename": Path(p).name, "hash": hash_zarr(Path(p))} for p in paths]


def build_entry(skill: str, version: str, args: dict, input, reference_inputs=None) -> dict:
    """Assemble a provenance entry.

    ``input`` is ``None`` for a fetcher, an :func:`input_ref` dict for a
    single-input skill, or a :func:`multi_input_ref` list. ``reference_inputs``
    (a :func:`reference_ref` list), when given, is attached as a sibling key.
    """
    entry = {"skill": skill, "version": version, "args": args, "input": input}
    if reference_inputs:
        entry["reference_inputs"] = reference_inputs
    return entry


def _chained_input_match(last_input, entry_input, *, compare_hash: bool) -> bool:
    """Compare the recorded ``input`` of the output's last entry to the candidate's."""
    if isinstance(entry_input, list):
        # Multi-input: per-item basename + hash + history, in order.
        if not isinstance(last_input, list) or len(last_input) != len(entry_input):
            return False
        return all(
            isinstance(li, dict)
            and li.get("basename") == ei["basename"]
            and li.get("hash") == ei["hash"]
            and li.get("history") == ei["history"]
            for li, ei in zip(last_input, entry_input, strict=True)
        )
    last_input = last_input or {}
    entry_input = entry_input or {}
    if last_input.get("basename") != entry_input.get("basename"):
        return False
    if compare_hash and last_input.get("hash") != entry_input.get("hash"):
        return False
    return True


def cache_hit(
    out: Path,
    entry: dict,
    upstream: list | None = None,
    *,
    fetcher: bool = False,
    compare_hash: bool = True,
    completeness_probe=None,
) -> bool:
    """Return True when the store at ``out`` was produced by this same entry.

    Two chain positions are supported:

    - ``fetcher=True``: the candidate entry is the chain's FIRST entry
      (``history[0]``); ``skill``/``version``/``args``/``input`` are compared
      wholesale. ``completeness_probe``, when given, is a
      ``callable(Path) -> bool`` invoked only after the entry matches; a False
      result rejects the hit (a partial prior write can leave a matching
      history attr over truncated arrays) and prints a stderr note.
    - chained (default): the candidate entry is the chain's LAST entry on top
      of ``upstream`` (the input's chain); the output chain must be exactly
      ``upstream + [entry]``. The recorded input is compared by basename (and
      by content hash unless ``compare_hash=False``, for skills that defer the
      expensive hash until after this check). ``reference_inputs`` is always
      compared, so an in-place change to a secondary reference forces a
      recompute; entries without references compare equal on absence.
    """
    out = Path(out)
    if not out.exists():
        return False
    history = load_history(out)
    if fetcher:
        if not history:
            return False
        existing = history[0]
        matches = (
            existing.get("skill") == entry["skill"]
            and existing.get("version") == entry["version"]
            and existing.get("args") == entry["args"]
            and existing.get("input") == entry["input"]
        )
        if not matches:
            return False
        if completeness_probe is not None and not completeness_probe(out):
            print(
                f"Note: {out} matches the request but is an incomplete/unreadable "
                "store (likely a prior interrupted write); re-fetching.",
                file=sys.stderr,
            )
            return False
        return True

    upstream = upstream or []
    if len(history) != len(upstream) + 1:
        return False
    if history[:-1] != upstream:
        return False
    last = history[-1]
    return (
        last.get("skill") == entry["skill"]
        and last.get("version") == entry["version"]
        and last.get("args") == entry["args"]
        and _chained_input_match(last.get("input"), entry.get("input"), compare_hash=compare_hash)
        and last.get("reference_inputs") == entry.get("reference_inputs")
    )


def migrate_legacy_attrs(attrs: dict) -> dict:
    """Migrate ``rhiza_*`` attrs to their ``weather_skills_*`` names, in place.

    The legacy attr is always removed; its value lands on the new name only
    when the new name is not already set.
    """
    for old in LEGACY_ATTRS:
        if old in attrs:
            new = "weather_skills_" + old.removeprefix("rhiza_")
            attrs.setdefault(new, attrs.pop(old))
    return attrs


def stamp_zarr(ds, history: list, *, source: str | None = None) -> None:
    """Stamp a dataset for writing: history attr, legacy migration, encoding clear.

    Serializes ``history`` (the full chain, oldest first) onto
    ``weather_skills_history`` with sorted keys, sets ``weather_skills_source``
    when ``source`` is given (fetchers), migrates any legacy ``rhiza_*`` attrs,
    and clears every variable's ``encoding`` -- per-variable encoding is not
    part of the envelope contract, so re-writes must not carry the input's
    codecs. Skills that need controlled write encodings (time units/calendar,
    ``_FillValue``) set them after this call so the clear cannot drop them.
    """
    ds.attrs[HISTORY_ATTR] = json.dumps(history, sort_keys=True)
    if source is not None:
        ds.attrs[SOURCE_ATTR] = source
    migrate_legacy_attrs(ds.attrs)
    for v in ds.variables:
        ds[v].encoding = {}


def png_metadata(chains, software: str = DEFAULT_SOFTWARE) -> dict:
    """Build the ``savefig(metadata=...)`` dict for a plot skill's PNG output.

    ``chains`` is a list of ``(label, chain)`` pairs, one per input branch,
    where ``chain`` is that branch's full history (upstream + the plot entry).
    A single unlabeled input (``label`` None) uses the key
    ``weather_skills_history``; labeled inputs use suffixed keys
    (``weather_skills_history_<label>``, e.g. ``_a``/``_b`` or
    ``_forecast``/``_mclimate``). A ``Software`` key is always added.
    """
    metadata = {}
    for label, chain in chains:
        key = HISTORY_ATTR if label is None else f"{HISTORY_ATTR}_{label}"
        metadata[key] = json.dumps(chain, sort_keys=True)
    metadata["Software"] = software
    return metadata
