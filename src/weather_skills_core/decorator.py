"""The ``@weather_skill`` decorator: a declarative CLI for weather skills.

A skill declares its surface (input/output envelope types, standard parameter
toggles, extra arguments) and keeps only its domain logic; the decorator owns
argparse construction, input reading, envelope validation, date resolution,
provenance, the cache-hit short-circuit, and output writing.

The wrapped function receives the input dataset(s) positionally followed by
the resolved parameters as keyword arguments, and returns its output:

- a Dataset for a zarr-writing skill (the decorator stamps provenance and
  writes it);
- a generator of per-period Datasets in streaming mode (the decorator writes
  the first with ``mode="w"`` and appends the rest, re-stamping provenance on
  every append and rolling back a partial store on failure);
- a Figure-like object (anything with ``savefig``) for a PNG-writing skill
  (the decorator saves it with provenance embedded in the PNG metadata);
- anything (ignored) for a no-artifact skill.

Calling the decorated function runs the CLI on ``sys.argv``; pass ``argv`` to
run it on an explicit argument list. Usage/validation failures exit 2 and
occur before any network work; data-availability and hard failures exit 1
(see :mod:`weather_skills_core.errors`).
"""

import argparse
import functools
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from weather_skills_core import dates as _dates
from weather_skills_core import envelope as _envelope
from weather_skills_core import provenance as _provenance
from weather_skills_core.errors import DataError, SkillError, UsageError

_START_HELP = (
    "Range start, inclusive. Either YYYY-MM-DD, 'now'/'today', 'latest', "
    "or an offset 'now-<int>{d|w}' / 'latest-<int>{d|w}' (w = 7 days)."
)
_END_HELP = "Range end, inclusive. Same date grammar as --start."
_DATE_HELP = (
    "Date. Either YYYY-MM-DD, 'now'/'today', 'latest', "
    "or an offset 'now-<int>{d|w}' / 'latest-<int>{d|w}' (w = 7 days)."
)
_BBOX_REQUIRED_HELP = (
    "N/W/S/E decimal degrees (use the resolve-region skill to get a country's bbox)"
)
_BBOX_OPTIONAL_HELP = "Spatial subset N/W/S/E decimal degrees. Omit for the full grid."

_ZARR_OUTPUT_TYPES = (_envelope.GRIDDED, _envelope.FORECAST, _envelope.STATION)
PNG = "png"


@dataclass
class EntryOverride:
    """Post-run provenance-entry rewrite.

    ``args`` is merged over the entry's recorded args. A standard-mode skill
    returns ``(dataset, EntryOverride(...))`` instead of a bare dataset; a
    streaming skill yields it from the generator (before or between datasets)
    and every subsequent stamp uses the rewritten entry -- the last stamp is
    the one that persists on the store. This supports effective-end rewrites:
    a fetcher that discovers mid-run that trailing days are unavailable
    records the effective window rather than the requested one.
    """

    args: dict


def rewrite_bbox_argv(argv):
    """Rewrite ``--bbox VAL`` to ``--bbox=VAL`` in an argv list.

    argparse rejects a space-separated ``--bbox`` value that starts with ``-``
    (a bbox whose North latitude is negative); the equals form parses either
    way.
    """
    out, i = [], 0
    while i < len(argv):
        if argv[i] == "--bbox" and i + 1 < len(argv):
            out.append(f"--bbox={argv[i + 1]}")
            i += 2
        else:
            out.append(argv[i])
            i += 1
    return out


def _add_extra_argument(parser, dest, spec):
    """Add one ``extra_args`` entry to the parser.

    ``spec`` is a bare type (``int``; ``bool`` becomes a store-true flag), a
    constraint set combining a type with a value domain (``{int, range(0, 2)}``
    derives ``choices``; a tuple lists choices literally), or a dict of
    argparse keywords for full control with the extra keys ``positional``,
    ``flag``, ``aliases``, and ``repeat``.
    """
    flag_name = "--" + dest.replace("_", "-")
    if isinstance(spec, dict):
        spec = dict(spec)
        positional = spec.pop("positional", False)
        flag = spec.pop("flag", flag_name)
        aliases = list(spec.pop("aliases", ()))
        if spec.pop("repeat", False):
            spec["action"] = "append"
        if positional:
            parser.add_argument(dest, **spec)
        else:
            parser.add_argument(flag, *aliases, dest=dest, **spec)
        return
    kwargs = {}
    if spec is bool:
        kwargs["action"] = "store_true"
    elif isinstance(spec, type):
        kwargs["type"] = spec
    elif isinstance(spec, set | frozenset | tuple | list):
        for element in spec:
            if element is bool:
                kwargs["action"] = "store_true"
            elif isinstance(element, type):
                kwargs["type"] = element
            elif isinstance(element, range):
                kwargs["choices"] = list(element)
            elif isinstance(element, tuple | list):
                kwargs["choices"] = list(element)
            else:
                raise ValueError(f"unsupported constraint {element!r} for extra arg {dest!r}")
    else:
        raise ValueError(f"unsupported extra_args spec {spec!r} for {dest!r}")
    parser.add_argument(flag_name, dest=dest, **kwargs)


def _normalize_mutex_groups(mutex_groups, extra_args):
    """Validate a ``mutex_groups`` declaration against ``extra_args``.

    Returns ``(group_required, dest_to_group)``: the per-group ``required``
    flag and the dest-to-group-name membership map. Raises :class:`ValueError`
    for a group naming an undeclared dest, a dest in two groups, a group with
    fewer than two members, a positional member, or a member carrying its own
    ``required`` (requiredness belongs to the group).
    """
    group_required = {}
    dest_to_group = {}
    for group_name, group_spec in (mutex_groups or {}).items():
        if isinstance(group_spec, dict):
            unknown = set(group_spec) - {"args", "required"}
            if unknown:
                raise ValueError(f"mutex group {group_name!r} has unknown keys {sorted(unknown)}")
            if "args" not in group_spec:
                raise ValueError(f"mutex group {group_name!r} must list member dests under 'args'")
            dests = list(group_spec["args"])
            required = bool(group_spec.get("required", False))
        else:
            dests = list(group_spec)
            required = False
        if len(dests) < 2:
            raise ValueError(f"mutex group {group_name!r} needs at least two member dests")
        for dest in dests:
            if dest not in (extra_args or {}):
                raise ValueError(
                    f"mutex group {group_name!r} names {dest!r}, which is not an extra_args dest"
                )
            if dest in dest_to_group:
                raise ValueError(
                    f"extra arg {dest!r} is in both mutex groups "
                    f"{dest_to_group[dest]!r} and {group_name!r}"
                )
            spec = extra_args[dest]
            if isinstance(spec, dict):
                if spec.get("positional"):
                    raise ValueError(
                        f"mutex group {group_name!r} member {dest!r} is positional; "
                        "mutually exclusive arguments must be flags"
                    )
                if spec.get("required"):
                    raise ValueError(
                        f"mutex group {group_name!r} member {dest!r} sets required=True; "
                        "declare requiredness on the group, not the member"
                    )
            dest_to_group[dest] = group_name
        group_required[group_name] = required
    return group_required, dest_to_group


def weather_skill(
    name,
    version,
    *,
    input_type=None,
    output_type=None,
    input_names=None,
    variadic_input=False,
    start_time=False,
    end_time=False,
    date=False,
    bbox=None,
    variable=None,
    workers=None,
    title=False,
    dims=False,
    time_dim=False,
    extra_args=None,
    mutex_groups=None,
    latest_resolver=None,
    source=None,
    streaming=False,
    cache=True,
    hash_input=True,
    completeness_probe=None,
    validate_args=None,
    normalize_args=None,
    exclude_args=(),
    reference_args=(),
    history_labels=None,
    write_encoding=None,
    append_dim="time",
    savefig_kwargs=None,
    software=_provenance.DEFAULT_SOFTWARE,
):
    """Declare a weather skill.

    Declaration surface:

    - ``name`` / ``version`` -- canonical skill name and its version (the
      script's ``_SKILL_VERSION``); the version appears in the argparse epilog
      and every provenance entry.
    - ``input_type`` -- envelope type(s) of the zarr input(s): ``None`` (no
      zarr inputs), one type string, or a comma string / list declaring one
      type per input (each from ``gridded``/``forecast``/``station``/``any``).
      Inputs arrive via ``--input``/``-i`` (repeated when there are several)
      unless ``input_names`` names a dedicated flag per input (e.g.
      ``["forecast", "mclimate"]``), or ``variadic_input=True`` accepts two or
      more ``--input`` repeats of a single declared type (the function then
      receives one list of datasets).
    - ``output_type`` -- ``None`` for a no-artifact skill (argparse + version
      epilog only: no provenance, no cache, no write), a zarr envelope type,
      or ``"png"`` for a Figure-writing skill.
    - standard parameter toggles: ``start_time``/``end_time``/``date`` (the
      relative-or-absolute date grammar; resolved dates are passed to the
      function and recorded in provenance), ``bbox`` (``"required"`` or
      ``"optional"``; parsed to an (N, W, S, E) tuple), ``variable``
      (``"single"`` or ``"repeat"``), ``workers`` (an int default; excluded
      from the cache key), ``title``, ``dims`` (LAT,LON override), and
      ``time_dim`` (pass a string to set a default).
    - ``extra_args`` -- mapping of dest name to a bare type, a constraint set
      (``{int, range(0, 2)}``), or an argparse-keyword dict.
    - ``mutex_groups`` -- mapping of group name to either a sequence of
      ``extra_args`` dests (an optional group) or a dict
      ``{"args": (dests...), "required": True}``. Each group becomes a real
      argparse mutually exclusive group: at most one member may be given, and
      ``required=True`` demands exactly one. Members must be non-positional
      ``extra_args`` entries that do not set their own ``required``; the group
      name labels the declaration only (argparse mutex groups are untitled).
    - ``latest_resolver`` -- ``callable(args) -> date`` resolving the
      ``latest`` token; invoked lazily, at most once per run.
    - ``source`` -- ``weather_skills_source`` value stamped on fetcher output.
    - ``streaming`` -- the function is a generator yielding per-period
      datasets, written as ``mode="w"`` then appends along ``append_dim``.
    - cache behavior: ``cache=False`` disables the cache check entirely -- the
      function runs and the output is rewritten on every invocation, with the
      provenance entry still built and stamped (for skills whose recompute is
      cheaper than a meaningful cache key); ``hash_input`` compares the
      input's content hash in the cache key (``False`` defers the expensive
      hash until after a cheap check); ``completeness_probe`` (``callable(Path) -> bool``) verifies a
      candidate fetcher hit actually reads back; ``reference_args`` names
      arg dests holding secondary reference-store paths, content-hashed into
      the entry's ``reference_inputs``.
    - hooks: ``validate_args(args)`` for pre-cache argument validation (raise
      ``UsageError``); ``normalize_args(dict) -> dict`` canonicalizes the
      recorded entry args (sort/dedupe) so flag order cannot cause spurious
      misses; ``exclude_args`` drops further dests from the entry args;
      ``write_encoding(ds)`` sets controlled write encodings after the
      encoding clear.
    - PNG: ``history_labels`` gives the per-input suffix for the embedded
      history keys (defaults to ``input_names``); ``savefig_kwargs`` extends
      the ``savefig`` call (default ``{"dpi": 150}``).
    """
    input_types = _normalize_input_types(input_type)
    if variadic_input and len(input_types) != 1:
        raise ValueError("variadic_input requires exactly one declared input type")
    if input_names is not None and len(input_names) != len(input_types):
        raise ValueError("input_names must declare one flag per declared input type")
    if output_type not in (None, PNG, *_ZARR_OUTPUT_TYPES):
        raise ValueError(f"unknown output_type {output_type!r}")
    if streaming and output_type not in _ZARR_OUTPUT_TYPES:
        raise ValueError("streaming requires a zarr output_type")
    if cache is False and output_type not in _ZARR_OUTPUT_TYPES:
        raise ValueError(
            "cache=False requires a zarr output_type; PNG and no-artifact skills have no cache"
        )
    if output_type is None and input_types:
        raise ValueError("no-artifact skills do not declare input_type")
    if bbox not in (None, "optional", "required"):
        raise ValueError(f"bbox must be None, 'optional', or 'required', not {bbox!r}")
    if variable not in (None, "single", "repeat"):
        raise ValueError(f"variable must be None, 'single', or 'repeat', not {variable!r}")
    if start_time != end_time:
        raise ValueError("start_time and end_time must be enabled together")
    png_labels = history_labels if history_labels is not None else input_names
    if output_type == PNG and len(input_types) > 1:
        if png_labels is None or len(png_labels) != len(input_types):
            raise ValueError("a multi-input PNG skill must declare one history label per input")

    input_dests = list(input_names) if input_names else (["input"] if input_types else [])
    input_dests = [d.replace("-", "_") for d in input_dests]

    group_required, dest_to_group = _normalize_mutex_groups(mutex_groups, extra_args)

    def decorate(fn):
        parser = _build_parser(fn)

        @functools.wraps(fn)
        def wrapper(argv=None):
            args_list = list(sys.argv[1:]) if argv is None else list(argv)
            if bbox is not None:
                args_list = rewrite_bbox_argv(args_list)
            args = parser.parse_args(args_list)
            try:
                _execute(fn, args)
            except SkillError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                sys.exit(exc.exit_code)

        wrapper.parser = parser
        return wrapper

    def _build_parser(fn):
        parser = argparse.ArgumentParser(
            description=fn.__doc__,
            epilog=f"skill version: {version}",
        )
        if input_names:
            for flag_name in input_names:
                parser.add_argument(f"--{flag_name}", required=True)
        elif variadic_input:
            parser.add_argument(
                "--input",
                "-i",
                action="append",
                required=True,
                help="Input Zarr (repeat the flag for each input; need at least 2)",
            )
        elif len(input_types) == 1:
            parser.add_argument("--input", "-i", required=True)
        elif len(input_types) > 1:
            parser.add_argument(
                "--input",
                "-i",
                action="append",
                required=True,
                help=f"Input Zarr; pass exactly {len(input_types)} times, in order",
            )
        if output_type is not None:
            parser.add_argument("--output", "-o", required=True)
        if start_time:
            parser.add_argument("--start", required=True, help=_START_HELP)
        if end_time:
            parser.add_argument("--end", required=True, help=_END_HELP)
        if date:
            parser.add_argument("--date", required=True, help=_DATE_HELP)
        if bbox == "required":
            parser.add_argument("--bbox", required=True, help=_BBOX_REQUIRED_HELP)
        elif bbox == "optional":
            parser.add_argument("--bbox", help=_BBOX_OPTIONAL_HELP)
        if variable == "single":
            parser.add_argument("--variable", "-v")
        elif variable == "repeat":
            parser.add_argument("--variable", "-v", action="append", default=None)
        if workers is not None:
            parser.add_argument(
                "--workers",
                type=int,
                default=workers,
                help=f"Max concurrent fetch threads (default {workers}).",
            )
        if title:
            parser.add_argument("--title", help="Optional figure title.")
        if dims:
            parser.add_argument("--dims", help="Override LAT,LON dim names")
        if time_dim:
            kwargs = {"help": "Name of the time-like dim when not auto-detectable."}
            if isinstance(time_dim, str):
                kwargs["default"] = time_dim
            parser.add_argument("--time-dim", **kwargs)
        groups = {
            group_name: parser.add_mutually_exclusive_group(required=required)
            for group_name, required in group_required.items()
        }
        for dest, spec in (extra_args or {}).items():
            target = groups.get(dest_to_group.get(dest), parser)
            _add_extra_argument(target, dest, spec)
        return parser

    def _execute(fn, args):
        if workers is not None and args.workers < 1:
            raise UsageError("--workers must be >= 1.")
        if validate_args is not None:
            validate_args(args)

        paths = _input_paths(args)
        for p in paths:
            if not p.exists():
                raise UsageError(f"{p} not found.")

        out = Path(args.output) if output_type is not None else None
        if output_type in _ZARR_OUTPUT_TYPES:
            _overlap_guard(paths, out, args)

        # Resolve dates and bbox before any provenance or network work: a
        # malformed value must exit 2 without side effects, and the recorded
        # args carry resolved absolute dates, never relative tokens.
        latest_fn = (lambda: latest_resolver(args)) if latest_resolver is not None else None
        params = {}
        resolved_dates = {}
        if start_time and end_time:
            start_d, end_d, log_line = _dates.resolve_window(args.start, args.end, latest_fn)
            if log_line is not None:
                print(log_line, file=sys.stderr)
            params["start_time"], params["end_time"] = start_d, end_d
            resolved_dates["start"] = start_d.isoformat()
            resolved_dates["end"] = end_d.isoformat()
        if date:
            date_d, log_line = _dates.resolve_date(args.date, latest_fn)
            if log_line is not None:
                print(log_line, file=sys.stderr)
            params["date"] = date_d
            resolved_dates["date"] = date_d.isoformat()
        if bbox is not None:
            params["bbox"] = _envelope.parse_bbox(args.bbox) if args.bbox else None
        if variable is not None:
            params["variable"] = args.variable
        if workers is not None:
            params["workers"] = args.workers
        if title:
            params["title"] = args.title
        if dims:
            params["dims"] = args.dims
        if time_dim:
            params["time_dim"] = args.time_dim
        for dest in extra_args or {}:
            params[dest] = getattr(args, dest)

        if output_type is None:
            fn(**params)
            return

        entry_args = _entry_args(args, resolved_dates)

        if output_type == PNG:
            _run_png(fn, args, paths, out, entry_args, params)
            return
        _run_zarr(fn, args, paths, out, entry_args, params)

    def _input_paths(args):
        if input_names:
            return [Path(getattr(args, d)) for d in input_dests]
        if not input_types:
            return []
        if variadic_input:
            values = args.input
            if len(values) < 2:
                raise UsageError("need at least 2 inputs.")
            return [Path(v) for v in values]
        if len(input_types) == 1:
            return [Path(args.input)]
        values = args.input
        if len(values) != len(input_types):
            raise UsageError(
                f"--input must be passed exactly {len(input_types)} times; got {len(values)}."
            )
        return [Path(v) for v in values]

    def _overlap_guard(paths, out, args):
        # rmtree of the output must never precede lazy reads of an input; the
        # same-store and nested-store cases would corrupt the input before its
        # lazily-backed values are read.
        out_r = out.resolve()
        for p in paths:
            p_r = p.resolve()
            if p_r == out_r or out_r.is_relative_to(p_r) or p_r.is_relative_to(out_r):
                raise UsageError(
                    f"--output ({args.output}) overlaps with input ({p}) as the same "
                    f"store or one nested inside the other; {name} writes to a "
                    "distinct output path."
                )

    def _entry_args(args, resolved_dates):
        path_dests = set(input_dests) | {"output"}
        raw = {k: v for k, v in vars(args).items() if k not in path_dests}
        raw.update(resolved_dates)
        raw.pop("workers", None)
        for dest in exclude_args:
            raw.pop(dest, None)
        if normalize_args is not None:
            raw = normalize_args(raw)
        return raw

    def _open_inputs(paths):
        import xarray as xr

        if variadic_input:
            declared_per_path = [input_types[0]] * len(paths)
        else:
            declared_per_path = input_types
        datasets = []
        for p, declared in zip(paths, declared_per_path, strict=True):
            try:
                ds = xr.open_zarr(p, consolidated=False)
            except Exception as exc:
                raise UsageError(
                    f"{p} is not a readable Zarr store ({type(exc).__name__}: {exc})."
                ) from None
            # An input may declare alternatives with "|" (e.g. "gridded|forecast").
            _envelope.validate_input(ds, [t.strip() for t in declared.split("|")], str(p))
            datasets.append(ds)
        return datasets

    def _call(fn, datasets, params):
        if variadic_input:
            return fn(datasets, **params)
        return fn(*datasets, **params)

    def _reference_inputs(args):
        refs = []
        for dest in reference_args:
            value = getattr(args, dest, None)
            if value:
                ref_p = Path(value)
                if not ref_p.exists():
                    raise UsageError(f"--{dest.replace('_', '-')} {ref_p} not found.")
                refs.append(ref_p)
        return _provenance.reference_ref(refs) if refs else None

    def _run_png(fn, args, paths, out, entry_args, params):
        # Plot skills carry no cache: they always render. Each input branch
        # gets its own entry (same args, that input's basename + hash) on top
        # of that input's chain.
        upstreams = [_provenance.load_history(p) for p in paths]
        for p, upstream in zip(paths, upstreams, strict=True):
            if not upstream:
                print(
                    f"Warning: no upstream weather_skills_history on {p.name}; "
                    f"embedding {name} step alone.",
                    file=sys.stderr,
                )
        chains = []
        labels = png_labels if len(paths) > 1 else [None]
        for label, p, upstream in zip(labels, paths, upstreams, strict=True):
            entry = _provenance.build_entry(
                name, version, entry_args, _provenance.input_ref(p, include_hash=True)
            )
            chains.append((label, upstream + [entry]))

        datasets = _open_inputs(paths)
        fig = _call(fn, datasets, params)

        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(
            out,
            metadata=_provenance.png_metadata(chains, software=software),
            **{"dpi": 150, **(savefig_kwargs or {})},
        )
        # matplotlib is deliberately not a dependency of this package; close
        # the figure only when it is importable (a real Figure was returned).
        try:
            import matplotlib.pyplot as plt
        except ImportError:
            pass
        else:
            plt.close(fig)
        print(f"Wrote: {args.output}", file=sys.stderr)

    def _run_zarr(fn, args, paths, out, entry_args, params):
        # The provenance entry is computed BEFORE the function runs: the entry
        # is the cache key, and a hit returns without calling the function or
        # touching the store.
        reference_inputs = _reference_inputs(args)
        if not paths:
            upstream = []
            entry = _provenance.build_entry(name, version, entry_args, None, reference_inputs)
            if cache and _provenance.cache_hit(
                out, entry, fetcher=True, completeness_probe=completeness_probe
            ):
                print(
                    f"Cache hit: {args.output} already matches requested params; skipping {name}.",
                    file=sys.stderr,
                )
                return
        elif len(paths) == 1:
            upstream = _provenance.load_history(paths[0])
            entry = _provenance.build_entry(
                name,
                version,
                entry_args,
                _provenance.input_ref(paths[0], include_hash=hash_input),
                reference_inputs,
            )
            if cache and _provenance.cache_hit(out, entry, upstream, compare_hash=hash_input):
                print(
                    f"Cache hit: {args.output} already matches requested params; skipping {name}.",
                    file=sys.stderr,
                )
                return
            if not hash_input:
                # Cache miss: now pay for the content hash so the stamped
                # entry is complete even though the check skipped it.
                entry["input"] = _provenance.input_ref(paths[0], include_hash=True)
            if not upstream:
                print(
                    "Warning: no upstream weather_skills_history on input; "
                    "treating input as opaque.",
                    file=sys.stderr,
                )
        else:
            histories = [_provenance.load_history(p) for p in paths]
            upstream = histories[0]
            entry = _provenance.build_entry(
                name,
                version,
                entry_args,
                _provenance.multi_input_ref(paths, histories),
                reference_inputs,
            )
            if cache and _provenance.cache_hit(out, entry, upstream):
                print(
                    f"Cache hit: {args.output} already matches requested params; skipping {name}.",
                    file=sys.stderr,
                )
                return
            for p, hist in zip(paths, histories, strict=True):
                if not hist:
                    print(
                        f"Warning: no upstream weather_skills_history on input "
                        f"{p.name}; treating input as opaque.",
                        file=sys.stderr,
                    )

        datasets = _open_inputs(paths)
        result = _call(fn, datasets, params)

        if streaming:
            _write_streaming(result, out, upstream, entry, args)
            return

        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], EntryOverride):
            result, override = result
            entry = {**entry, "args": {**entry["args"], **override.args}}
        # Carry the first input's attrs (source metadata, upstream history)
        # under the function's own attrs, then stamp the new chain over both.
        if datasets:
            result.attrs = {**datasets[0].attrs, **result.attrs}
        _provenance.stamp_zarr(result, upstream + [entry], source=source)
        if write_encoding is not None:
            write_encoding(result)
        if out.exists():
            shutil.rmtree(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        result.to_zarr(out, mode="w", consolidated=True)
        print(f"Wrote: {args.output} ({dict(result.sizes)})", file=sys.stderr)

    def _write_streaming(gen, out, upstream, entry, args):
        # First write is mode="w"; later periods append along append_dim.
        # Provenance is re-stamped on every append because a to_zarr append
        # rewrites the root group attrs from the appended dataset. The
        # store_created flag flips BEFORE the first write (after any
        # pre-existing store is removed): a failure during the first write may
        # already have created a partial directory that must be cleaned up,
        # while a complete store from a previous run is gone before the flag
        # flips and so can never be deleted by the rollback.
        store_created = False
        total = 0
        try:
            for item in gen:
                if isinstance(item, EntryOverride):
                    entry = {**entry, "args": {**entry["args"], **item.args}}
                    continue
                piece = item
                _provenance.stamp_zarr(piece, upstream + [entry], source=source)
                if write_encoding is not None:
                    write_encoding(piece)
                if not store_created:
                    if out.exists():
                        shutil.rmtree(out)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    store_created = True
                    piece.to_zarr(out, mode="w", consolidated=True)
                else:
                    piece.to_zarr(out, mode="a", append_dim=append_dim, consolidated=True)
                total += piece.sizes.get(append_dim, 0)
        except BaseException:
            if store_created and out.exists():
                shutil.rmtree(out)
                print(
                    f"Removed partial store {args.output} after a mid-stream failure "
                    "so it is not mistaken for a complete cache on a later run.",
                    file=sys.stderr,
                )
            raise
        if not store_created:
            raise DataError(f"{name} produced no data for the requested window; nothing written.")
        print(f"Wrote: {args.output} ({append_dim}={total})", file=sys.stderr)

    return decorate


def _normalize_input_types(input_type):
    """Normalize the ``input_type`` declaration to a list of per-input types."""
    if input_type is None:
        return []
    if isinstance(input_type, str):
        return [t.strip() for t in input_type.split(",")]
    return list(input_type)
