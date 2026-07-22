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
"""

import hashlib
import json
import sys
from pathlib import Path

HISTORY_ATTR = "weather_skills_history"
SOURCE_ATTR = "weather_skills_source"

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


def parse_chain(raw: str) -> list:
    """Strictly parse a raw ``weather_skills_history`` value into a chain list.

    Raises :class:`ValueError` with the message ``"value is not valid JSON"``
    when the value does not decode, or ``"value is not a JSON array"`` when it
    decodes to anything but an array. Schema checkers record the raised
    message as a violation; lenient render paths use :func:`coerce_chain`.
    """
    try:
        chain = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        raise ValueError("value is not valid JSON") from None
    if not isinstance(chain, list):
        raise ValueError("value is not a JSON array")
    return chain


def coerce_chain(raw: str, label: str) -> list | None:
    """Leniently parse a raw ``weather_skills_history`` value for render paths.

    A value that is present but not a JSON array (non-JSON, or a JSON
    object/scalar) is malformed under the ``weather_skills_history`` array
    contract; return ``None`` after a one-line stderr warning naming
    ``label`` (the artifact basename or key being read) and pointing at
    ``provenance --check``, so the caller omits the branch. A valid array
    (including an empty one) passes through unchanged, even when its entries
    are imperfect.
    """
    try:
        chain = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        chain = None
    if not isinstance(chain, list):
        print(
            f"ignoring malformed weather_skills_history on {label}; "
            "run `provenance --check` for details",
            file=sys.stderr,
        )
        return None
    return chain


_ENTRY_KNOWN_KEYS = {"skill", "version", "args", "input"}
_INPUT_ITEM_KNOWN_KEYS = {"basename", "hash", "history"}


def _validate_input(value, loc: str, violations: list, notes: list) -> None:
    """Validate an entry's ``input`` field against the array contract.

    ``input`` is one of: ``null``; a ``{basename, hash}`` dict; or an array of
    ``{basename, hash}`` dicts, each of which may also carry a nested
    ``history`` chain (recursively validated). Appends violations and notes in
    place.
    """
    if value is None:
        return

    def _check_item(item, item_loc: str) -> None:
        if not isinstance(item, dict):
            violations.append(f"{item_loc}: input entry is not an object")
            return
        if "basename" not in item:
            violations.append(f"{item_loc}: missing required key 'basename'")
        elif not isinstance(item["basename"], str):
            violations.append(f"{item_loc}.basename: must be a string")
        if "hash" not in item:
            violations.append(f"{item_loc}: missing required key 'hash'")
        elif not isinstance(item["hash"], str):
            violations.append(f"{item_loc}.hash: must be a string")
        if "history" in item:
            _validate_chain(item["history"], f"{item_loc}.history", violations, notes)
        for key in item:
            if key not in _INPUT_ITEM_KNOWN_KEYS:
                notes.append(f"{item_loc}: unknown key {key!r}")

    if isinstance(value, list):
        for j, item in enumerate(value):
            _check_item(item, f"{loc}[{j}]")
        return
    if isinstance(value, dict):
        _check_item(value, loc)
        return
    violations.append(f"{loc}: must be null, an object, or an array of objects")


def _validate_chain(chain, loc: str, violations: list, notes: list) -> None:
    """Validate one chain (an array of entries) against the schema, in place.

    Records every violation with its location into ``violations``; records
    unknown/extra keys (which do not fail validation) into ``notes``. Recurses
    into a multi-input entry's ``input[*].history``.
    """
    if not isinstance(chain, list):
        violations.append(f"{loc}: value is not a JSON array")
        return
    for i, entry in enumerate(chain):
        eloc = f"{loc}[{i}]"
        if not isinstance(entry, dict):
            violations.append(f"{eloc}: entry is not an object")
            continue
        if "skill" not in entry:
            violations.append(f"{eloc}: missing required key 'skill'")
        elif not isinstance(entry["skill"], str):
            violations.append(f"{eloc}.skill: must be a string")
        elif not entry["skill"]:
            violations.append(f"{eloc}.skill: must be a non-empty string")
        if "version" not in entry:
            violations.append(f"{eloc}: missing required key 'version'")
        elif not isinstance(entry["version"], str):
            violations.append(f"{eloc}.version: must be a string")
        if "args" not in entry:
            violations.append(f"{eloc}: missing required key 'args'")
        elif not isinstance(entry["args"], dict):
            violations.append(f"{eloc}.args: must be an object")
        if "input" not in entry:
            violations.append(f"{eloc}: missing required key 'input'")
        else:
            _validate_input(entry["input"], f"{eloc}.input", violations, notes)
        for key in entry:
            if key not in _ENTRY_KNOWN_KEYS:
                notes.append(f"{eloc}: unknown key {key!r}")


def validate_chain(chain, loc: str) -> tuple[list, list]:
    """Validate a parsed ``weather_skills_history`` chain against the entry schema.

    Returns ``(violations, notes)``, both lists of location-prefixed strings.
    Violations cover a non-array chain, non-object entries, missing or
    mistyped required entry keys (``skill``/``version``/``args``/``input``),
    and a malformed ``input`` value; a multi-input entry's nested per-branch
    ``history`` is validated recursively, its findings located under
    ``<loc>[i].input[j].history``. Unknown/extra keys land in ``notes`` and do
    not fail validation. ``loc`` prefixes every location (typically the attr
    or tEXt key name the chain was read from).
    """
    violations: list = []
    notes: list = []
    _validate_chain(chain, loc, violations, notes)
    return violations, notes


def load_history(zarr_path: Path) -> list:
    """Read an artifact's provenance chain, tolerating absence and malformation.

    Only the ``weather_skills_history`` attr is read; a store carrying no such
    attr has no history. A not-yet-existing or unreadable store is a silent
    miss (empty chain). A present-but-non-array value is malformed under the
    ``weather_skills_history`` contract; it is treated as no history with a
    one-line stderr warning pointing at ``provenance --check``. The coercion
    is array-level only: an array whose individual entries are imperfect is
    passed through unchanged.
    """
    zarr_path = Path(zarr_path)
    try:
        import xarray as xr

        with xr.open_zarr(zarr_path, consolidated=False) as ds:
            raw = ds.attrs.get(HISTORY_ATTR)
    except (FileNotFoundError, KeyError, ValueError):
        # A not-yet-existing or unreadable output during a cache check is a miss.
        return []
    if not raw:
        return []
    parsed = coerce_chain(raw, str(zarr_path))
    return [] if parsed is None else parsed


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


def stamp_zarr(ds, history: list, *, source: str | None = None) -> None:
    """Stamp a dataset for writing: history attr and encoding clear.

    Serializes ``history`` (the full chain, oldest first) onto
    ``weather_skills_history`` with sorted keys, sets ``weather_skills_source``
    when ``source`` is given (fetchers), and clears every variable's
    ``encoding`` -- per-variable encoding is not part of the envelope
    contract, so re-writes must not carry the input's codecs. Skills that
    need controlled write encodings (time units/calendar, ``_FillValue``) set
    them after this call so the clear cannot drop them. Other pre-existing
    attrs are left untouched.
    """
    ds.attrs[HISTORY_ATTR] = json.dumps(history, sort_keys=True)
    if source is not None:
        ds.attrs[SOURCE_ATTR] = source
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
