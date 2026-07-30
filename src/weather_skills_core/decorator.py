"""The ``@weather_skill`` decorator: declarative CLI + linear run loop.

Declaration: dim-ontology inputs/outputs on ``@weather_skill``, plus stacked
``@weather_skill.argument(...)`` decorators (argparse ``add_argument`` kwargs).
Run loop (in the wrapper): parse → open inputs → call skill → stamp/write.
Skills return an xarray Dataset (decorator writes Zarr) or a Path (already
written; decorator stamps provenance).
"""

import argparse
import functools
import inspect
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
_BBOX_HELP = "N/W/S/E decimal degrees (use the resolve-region skill to get a country's bbox)."
_START_TIME_HELP = "Range start, inclusive. Absolute YYYY-MM-DD."
_END_TIME_HELP = "Range end, inclusive. Absolute YYYY-MM-DD."

# dest -> canonical help suffix/body appended (or used alone) for specials
_CANONICAL_HELP = {
    "bbox": _BBOX_HELP,
    "date": _DATE_HELP,
    "start_time": _START_TIME_HELP,
    "end_time": _END_TIME_HELP,
}
_CANONICAL_DESTS = frozenset(_CANONICAL_HELP)

# Accumulated by ``@weather_skill.argument`` (bottom-up); weather_skill reads it.
_ARGS_ATTR = "__weather_skill_arguments__"


@dataclass(frozen=True)
class StandardParameter:
    name: str
    dest: str
    flags: tuple
    kind: str  # "io" | "canonical"
    accepts_help: bool = False


class Argument:
    """One CLI argument; mirrors ``argparse.add_argument(*option_strings, **kwargs)``.

    Prefer declaring via ``@weather_skill.argument(...)`` rather than constructing
    this directly.
    """

    def __init__(self, *option_strings, **kwargs):
        if not option_strings:
            raise ValueError("Argument requires at least one option string or positional name")
        self.option_strings = option_strings
        self.kwargs = dict(kwargs)

    @property
    def dest(self) -> str:
        if "dest" in self.kwargs:
            return self.kwargs["dest"]
        # argparse default: first long option, else first option, dashes→underscores
        flag = next(
            (f for f in self.option_strings if f.startswith("--")),
            self.option_strings[0],
        )
        return flag.lstrip("-").replace("-", "_")


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
        StandardParameter("start_time", "start_time", ("--start-time",), "canonical", True),
        StandardParameter("end_time", "end_time", ("--end-time",), "canonical", True),
        StandardParameter("date", "date", ("--date",), "canonical", True),
        StandardParameter("bbox", "bbox", ("--bbox",), "canonical", True),
        StandardParameter("variable", "variable", ("--variable", "-v"), "canonical", True),
    )


def _slot_help_label(slot: _envelope.SlotSpec) -> str:
    if slot.kind == "unstructured":
        return "unstructured"
    if slot.kind == "visualization":
        return "visualization"
    if slot.alternatives is None:
        return "any"
    if len(slot.alternatives) == 1:
        dims = sorted(slot.alternatives[0])
        return "+".join(dims) if dims else "any"
    parts = []
    for alt in slot.alternatives:
        parts.append("{" + ",".join(sorted(alt)) + "}")
    return " OR ".join(parts)


def _accepts_var_keyword(fn) -> bool:
    return any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in inspect.signature(fn).parameters.values()
    )


def _add_help(kwargs: dict, canonical: str) -> dict:
    """Return kwargs with canonical help set or appended."""
    out = dict(kwargs)
    existing = out.get("help")
    if existing:
        text = str(existing).rstrip()
        if canonical not in text:
            out["help"] = f"{text} {canonical}"
    else:
        out["help"] = canonical
    return out


def _argument(*option_strings, **kwargs):
    """Stackable decorator mirroring ``argparse.add_argument(*option_strings, **kwargs)``.

    Apply under ``@weather_skill(...)``. Decorators run bottom-up; each call
    inserts at the front of the accumulated list so source order (top to bottom)
    is preserved.
    """
    arg = Argument(*option_strings, **kwargs)

    def decorate(fn):
        existing = list(getattr(fn, _ARGS_ATTR, []))
        existing.insert(0, arg)
        setattr(fn, _ARGS_ATTR, existing)
        return fn

    return decorate


def weather_skill(
    *,
    name,
    version,
    inputs=None,
    outputs=None,
):
    """Declare a weather skill's CLI and wrap its domain function.

    ``name`` / ``version`` are required keyword arguments. ``inputs`` /
    ``outputs``: lists of IO slots. Within a slot, a **list** is OR, a
    **tuple** is AND, and a **string** is a canonical shorthand
    (``observations``, ``forecast``, …), a dimension name, ``any``,
    ``unstructured``, or (outputs only) ``visualization``. A single ``…+``
    string in ``inputs`` is variadic.

    Extra CLI flags: stack ``@weather_skill.argument(...)`` under this decorator
    (same signature as ``argparse.add_argument``). Canonical dests ``bbox``,
    ``date``, ``start_time``, ``end_time`` get auto help and post-parse
    conversion. The skill function must accept ``**kwargs``.
    """
    raw_inputs = list(inputs or [])
    raw_outputs = list(outputs or [])
    try:
        input_slots, variadic_input = _envelope.normalize_io_list(
            raw_inputs, allow_variadic=True, for_input=True
        )
        output_slots, _ = _envelope.normalize_io_list(
            raw_outputs, allow_variadic=False, for_input=False
        )
    except ValueError as exc:
        raise ValueError(f"skill {name!r}: {exc}") from exc

    def decorator(fn):
        arguments = list(getattr(fn, _ARGS_ATTR, []))
        for arg in arguments:
            if not isinstance(arg, Argument):
                raise ValueError(
                    f"skill {name!r}: stacked arguments must be Argument instances; "
                    f"got {type(arg).__name__}"
                )

        declared_dests = {arg.dest for arg in arguments}
        has_bbox = "bbox" in declared_dests

        if not _accepts_var_keyword(fn):
            raise TypeError(
                f"skill {name!r} must accept **kwargs so the decorator can pass "
                "extra runtime information"
            )

        parser = argparse.ArgumentParser(
            description=fn.__doc__,
            epilog=f"skill version: {version}",
        )
        if input_slots:
            if variadic_input:
                label = _slot_help_label(input_slots[0])
                help_text = f"Input path (repeat once per input; each must be {label})."
            else:
                n = len(input_slots)
                help_text = f"Input path (repeat exactly {n} time{'s' if n != 1 else ''})."
            parser.add_argument(
                "--input",
                "-i",
                dest="input",
                action="append",
                required=True,
                metavar="PATH",
                help=help_text,
            )
        if output_slots:
            n = len(output_slots)
            parser.add_argument(
                "--output",
                "-o",
                dest="output",
                action="append",
                required=True,
                metavar="PATH",
                help=f"Output path (repeat exactly {n} time{'s' if n != 1 else ''}).",
            )
        for arg in arguments:
            kwargs = dict(arg.kwargs)
            if arg.dest in _CANONICAL_HELP:
                kwargs = _add_help(kwargs, _CANONICAL_HELP[arg.dest])
            parser.add_argument(*arg.option_strings, **kwargs)

        @functools.wraps(fn)
        def wrapper(argv=None):
            args_list = sys.argv[1:] if argv is None else list(argv)
            if has_bbox:
                args_list = rewrite_bbox_argv(args_list)
            try:
                args = parser.parse_args(args_list)

                # --- arity checks ---
                input_paths = [Path(p) for p in (getattr(args, "input", None) or [])]
                output_paths = [Path(p) for p in (getattr(args, "output", None) or [])]
                if input_slots:
                    if variadic_input:
                        if len(input_paths) < 1:
                            raise UsageError("expected at least one --input path")
                    elif len(input_paths) != len(input_slots):
                        raise UsageError(
                            f"expected {len(input_slots)} --input path(s), got {len(input_paths)}"
                        )
                if output_slots and len(output_paths) != len(output_slots):
                    raise UsageError(
                        f"expected {len(output_slots)} --output path(s), "
                        f"got {len(output_paths)}"
                    )
                for p in input_paths:
                    if not p.exists():
                        raise UsageError(f"input not found: {p}")

                if variadic_input:
                    path_slots = [input_slots[0]] * len(input_paths)
                else:
                    path_slots = list(input_slots)

                # --- resolve argument kwargs (with canonical conversions) ---
                params = {}
                for arg in arguments:
                    dest = arg.dest
                    raw = getattr(args, dest)
                    if dest == "bbox":
                        params[dest] = _envelope.parse_bbox(raw) if raw is not None else None
                    elif dest == "date":
                        params[dest] = _dates.parse_date(raw) if raw is not None else None
                    elif dest in ("start_time", "end_time"):
                        params[dest] = _dates.parse_date(raw) if raw is not None else None
                    else:
                        params[dest] = raw

                start = params.get("start_time")
                end = params.get("end_time")
                if start is not None and end is not None and start > end:
                    raise UsageError(
                        f"--start-time {start.isoformat()} is after --end-time {end.isoformat()}."
                    )

                # --- open inputs ---
                opened = []
                upstream = []
                for path, slot in zip(input_paths, path_slots, strict=True):
                    if slot.kind == _envelope.UNSTRUCTURED:
                        opened.append(path)
                        upstream.append([])
                    else:
                        ds = xr.open_zarr(path, consolidated=True)
                        _envelope.validate_input(ds, slot, str(path))
                        opened.append(ds)
                        upstream.append(_provenance.load_history(path))

                # Decorator-owned extras (skills must accept **kwargs).
                extra = {}
                if output_slots:
                    if len(output_paths) == 1:
                        extra["output"] = output_paths[0]
                    else:
                        extra["output"] = output_paths

                if variadic_input:
                    result = fn(opened, **params, **extra)
                else:
                    result = fn(*opened, **params, **extra)
                if not output_slots:
                    return result

                results = result if isinstance(result, (list, tuple)) else [result]
                if len(results) != len(output_slots):
                    raise SkillError(
                        f"skill returned {len(results)} value(s), expected {len(output_slots)}"
                    )

                # --- provenance args (no paths) ---
                entry_args = {k: v for k, v in vars(args).items() if k not in ("input", "output")}
                for dest in ("date", "start_time", "end_time"):
                    if dest in params and params[dest] is not None:
                        entry_args[dest] = params[dest].isoformat()
                    elif dest in entry_args and entry_args[dest] is None:
                        pass  # keep None or leave; json will serialize
                entry_args = json.loads(json.dumps(entry_args, default=str))

                if not input_paths:
                    input_field = None
                    base_history = []
                elif len(input_paths) == 1:
                    if path_slots[0].kind == _envelope.UNSTRUCTURED:
                        input_field = {
                            "basename": input_paths[0].name,
                            "hash": _provenance.hash_file(input_paths[0]),
                        }
                    else:
                        input_field = _provenance.input_ref(input_paths[0])
                    base_history = upstream[0]
                else:
                    input_field = []
                    for path, slot, hist in zip(input_paths, path_slots, upstream, strict=True):
                        if slot.kind == _envelope.UNSTRUCTURED:
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

                first_ds = None
                if opened:
                    candidate = opened[0]
                    if hasattr(candidate, "attrs"):
                        first_ds = candidate

                for value, out_path, out_slot in zip(
                    results, output_paths, output_slots, strict=True
                ):
                    if out_slot.kind == _envelope.VISUALIZATION:
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

                    # Optional output-shape check for Dataset returns
                    if out_slot.kind == "zarr" and hasattr(value, "dims"):
                        _envelope.validate_input(value, out_slot, f"output {out_path}")

                    if first_ds is not None:
                        value.attrs = {**first_ds.attrs, **value.attrs}
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


weather_skill.argument = _argument  # type: ignore[attr-defined]
