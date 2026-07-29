"""The ``@weather_skill`` decorator: declarative CLI + linear run loop.

Declaration: inputs/outputs types, dates, region, variable, extra_args.
Run loop (in the wrapper): parse → open inputs → call skill → stamp/write.
Skills return an xarray Dataset (decorator writes Zarr) or a Path (already
written; decorator stamps provenance).
"""

import argparse
import functools
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import xarray as xr

from weather_skills_core import dates as _dates
from weather_skills_core import envelope as _envelope
from weather_skills_core import provenance as _provenance
from weather_skills_core.errors import SkillError, UsageError

_DATE_HELP = "Absolute date YYYY-MM-DD."
_BBOX_REQUIRED_HELP = (
    "N/W/S/E decimal degrees (use the resolve-region skill to get a country's bbox)"
)
_BBOX_OPTIONAL_HELP = "Spatial subset N/W/S/E decimal degrees. Omit for the full grid."

_VARIABLE_MODES = (
    "single_optional",
    "single_required",
    "multiple_optional",
    "multiple_required",
)
_IO_KINDS = (
    _envelope.DATA,
    _envelope.FORECAST,
    _envelope.STATION,
    _envelope.UNSTRUCTURED,
    _envelope.VISUALIZATION,
)


@dataclass(frozen=True)
class StandardParameter:
    name: str
    dest: str
    flags: tuple
    kind: str  # "io" | "toggle"
    accepts_help: bool = False


def rewrite_bbox_argv(argv):
    """Rewrite ``--bbox VAL`` to ``--bbox=VAL`` so negative north latitudes parse."""
    out = []
    i = 0
    while i < len(argv):
        if argv[i] == "--bbox" and i + 1 < len(argv):
            out.append(f"--bbox={argv[i + 1]}")
            i += 2
            continue
        out.append(argv[i])
        i += 1
    return out


def standard_parameters():
    """Standard CLI parameters the decorator may own (for lint shadow checks)."""
    return (
        StandardParameter("inputs", "input", ("--input", "-i"), "io"),
        StandardParameter("outputs", "output", ("--output", "-o"), "io"),
        StandardParameter("dates", "start_time", ("--start",), "toggle", True),
        StandardParameter("dates", "end_time", ("--end",), "toggle", True),
        StandardParameter("dates", "date", ("--date",), "toggle", True),
        StandardParameter("region", "bbox", ("--bbox",), "toggle", True),
        StandardParameter("variable", "variable", ("--variable", "-v"), "toggle", True),
    )


def weather_skill(
    name,
    version,
    *,
    inputs=None,
    outputs=None,
    dates=None,
    region=None,
    variable=None,
    extra_args=None,
):
    """Declare a weather skill's CLI and wrap its domain function.

    ``inputs`` / ``outputs``: lists of type strings (``data``, ``forecast``,
    ``station``, ``unstructured``; ``visualization`` is output-only).
    ``dates``: ``"single"`` or ``"range"``. ``region``: ``"required"`` or
    ``"optional"``. ``variable``: one of the ``*_optional`` / ``*_required``
    modes. ``extra_args``: list of ``(option_strings, kwargs)`` for
    ``parser.add_argument``.
    """
    inputs = list(inputs or [])
    outputs = list(outputs or [])
    extra_args = list(extra_args or [])

    for t in inputs:
        if t not in _IO_KINDS or t == _envelope.VISUALIZATION:
            raise ValueError(
                f"invalid input type {t!r}; expected one of "
                f"{[k for k in _IO_KINDS if k != _envelope.VISUALIZATION]}"
            )
    for t in outputs:
        if t not in _IO_KINDS:
            raise ValueError(f"invalid output type {t!r}; expected one of {list(_IO_KINDS)}")
    if dates not in (None, "single", "range"):
        raise ValueError(f"dates must be None, 'single', or 'range'; got {dates!r}")
    if region not in (None, "required", "optional"):
        raise ValueError(f"region must be None, 'required', or 'optional'; got {region!r}")
    if variable is not None and variable not in _VARIABLE_MODES:
        raise ValueError(f"variable must be one of {_VARIABLE_MODES}; got {variable!r}")

    def decorator(fn):
        parser = argparse.ArgumentParser(
            description=fn.__doc__,
            epilog=f"skill version: {version}",
        )
        if inputs:
            n = len(inputs)
            parser.add_argument(
                "--input",
                "-i",
                dest="input",
                action="append",
                required=True,
                metavar="PATH",
                help=f"Input path (repeat exactly {n} time{'s' if n != 1 else ''}).",
            )
        if outputs:
            n = len(outputs)
            parser.add_argument(
                "--output",
                "-o",
                dest="output",
                action="append",
                required=True,
                metavar="PATH",
                help=f"Output path (repeat exactly {n} time{'s' if n != 1 else ''}).",
            )
        if dates == "range":
            parser.add_argument(
                "--start", required=True, help="Range start, inclusive. " + _DATE_HELP
            )
            parser.add_argument("--end", required=True, help="Range end, inclusive. " + _DATE_HELP)
        elif dates == "single":
            parser.add_argument("--date", required=True, help=_DATE_HELP)
        if region is not None:
            kwargs = {
                "help": _BBOX_REQUIRED_HELP if region == "required" else _BBOX_OPTIONAL_HELP,
            }
            if region == "required":
                kwargs["required"] = True
            parser.add_argument("--bbox", **kwargs)
        if variable is not None:
            multiple = variable.startswith("multiple_")
            required = variable.endswith("_required")
            kwargs = {"dest": "variable", "help": "Variable name."}
            if multiple:
                kwargs["action"] = "append"
            if required:
                kwargs["required"] = True
            parser.add_argument("--variable", "-v", **kwargs)
        for option_strings, kwargs in extra_args:
            parser.add_argument(*option_strings, **kwargs)

        @functools.wraps(fn)
        def wrapper(argv=None):
            args_list = sys.argv[1:] if argv is None else list(argv)
            if region is not None:
                args_list = rewrite_bbox_argv(args_list)
            try:
                args = parser.parse_args(args_list)

                # --- arity checks ---
                input_paths = [Path(p) for p in (getattr(args, "input", None) or [])]
                output_paths = [Path(p) for p in (getattr(args, "output", None) or [])]
                if inputs and len(input_paths) != len(inputs):
                    raise UsageError(
                        f"expected {len(inputs)} --input path(s), got {len(input_paths)}"
                    )
                if outputs and len(output_paths) != len(outputs):
                    raise UsageError(
                        f"expected {len(outputs)} --output path(s), got {len(output_paths)}"
                    )
                for p in input_paths:
                    if not p.exists():
                        raise UsageError(f"input not found: {p}")

                # --- resolve standard kwargs ---
                params = {}
                if region is not None:
                    params["bbox"] = (
                        _envelope.parse_bbox(args.bbox) if args.bbox is not None else None
                    )
                if dates == "range":
                    params["start_time"], params["end_time"] = _dates.parse_range(
                        args.start, args.end
                    )
                elif dates == "single":
                    params["date"] = _dates.parse_date(args.date)
                if variable is not None:
                    params["variable"] = args.variable
                for option_strings, kwargs in extra_args:
                    dest = kwargs.get("dest")
                    if dest is None:
                        # argparse default: first long option, else first option, dashes→underscores
                        flag = next(
                            (f for f in option_strings if f.startswith("--")), option_strings[0]
                        )
                        dest = flag.lstrip("-").replace("-", "_")
                    params[dest] = getattr(args, dest)

                # --- open inputs ---
                opened = []
                upstream = []
                for path, kind in zip(input_paths, inputs, strict=True):
                    if kind == _envelope.UNSTRUCTURED:
                        opened.append(path)
                        upstream.append([])
                    else:
                        ds = xr.open_zarr(path, consolidated=True)
                        _envelope.validate_input(ds, kind, str(path))
                        opened.append(ds)
                        upstream.append(_provenance.load_history(path))

                # --- call skill ---
                result = fn(*opened, **params)
                if not outputs:
                    return result

                results = result if isinstance(result, (list, tuple)) else [result]
                if len(results) != len(outputs):
                    raise SkillError(
                        f"skill returned {len(results)} value(s), expected {len(outputs)}"
                    )

                # --- provenance args (no paths) ---
                entry_args = {k: v for k, v in vars(args).items() if k not in ("input", "output")}
                if dates == "range":
                    entry_args["start"] = params["start_time"].isoformat()
                    entry_args["end"] = params["end_time"].isoformat()
                elif dates == "single":
                    entry_args["date"] = params["date"].isoformat()
                entry_args = json.loads(json.dumps(entry_args, default=str))

                if not input_paths:
                    input_field = None
                    base_history = []
                elif len(input_paths) == 1:
                    if inputs[0] == _envelope.UNSTRUCTURED:
                        input_field = {
                            "basename": input_paths[0].name,
                            "hash": _provenance.hash_file(input_paths[0]),
                        }
                    else:
                        input_field = _provenance.input_ref(input_paths[0])
                    base_history = upstream[0]
                else:
                    input_field = []
                    for path, kind, hist in zip(input_paths, inputs, upstream, strict=True):
                        if kind == _envelope.UNSTRUCTURED:
                            input_field.append(
                                {
                                    "basename": path.name,
                                    "hash": _provenance.hash_file(path),
                                    "history": hist,
                                }
                            )
                        else:
                            input_field.append(
                                {
                                    "basename": path.name,
                                    "hash": _provenance.hash_zarr(path),
                                    "history": hist,
                                }
                            )
                    base_history = upstream[0]

                entry = _provenance.build_entry(name, version, entry_args, input_field)
                history = base_history + [entry]

                # --- stamp / write each output ---
                for value, out_path, out_type in zip(results, output_paths, outputs, strict=True):
                    if out_type == _envelope.VISUALIZATION:
                        if not isinstance(value, (str, Path)):
                            raise SkillError(
                                "visualization outputs must return a Path to the written file"
                            )
                        written = Path(value)
                        if written.resolve() != out_path.resolve():
                            raise SkillError(
                                f"visualization path {written} does not match --output {out_path}"
                            )
                        _provenance.stamp_visualization(written, history)
                        print(f"Wrote: {out_path}", file=sys.stderr)
                        continue

                    if isinstance(value, (str, Path)):
                        written = Path(value)
                        if written.resolve() != out_path.resolve():
                            raise SkillError(
                                f"returned path {written} does not match --output {out_path}"
                            )
                        _provenance.restamp_zarr(written, history)
                        print(f"Wrote: {out_path}", file=sys.stderr)
                        continue

                    # Dataset → stamp then write
                    if opened and hasattr(opened[0], "attrs"):
                        value.attrs = {**opened[0].attrs, **value.attrs}
                    _provenance.stamp_zarr(value, history)
                    if out_path.exists():
                        shutil.rmtree(out_path)
                    try:
                        value.to_zarr(out_path, mode="w", consolidated=True)
                    except Exception:
                        if out_path.exists():
                            shutil.rmtree(out_path)
                        raise
                    print(f"Wrote: {out_path}", file=sys.stderr)

                return result
            except SkillError as exc:
                msg = str(exc)
                print(msg if not exc.prefix else f"Error: {msg}", file=sys.stderr)
                sys.exit(exc.exit_code)

        wrapper.parser = parser
        return wrapper

    return decorator
