"""``@weather_skill`` decorator: CLI, open inputs, run skill, stamp/write outputs."""

import argparse
import functools
import inspect
import json
import shutil
import sys
from pathlib import Path

import xarray as xr

from weather_skills_core import provenance as provenance_mod
from weather_skills_core import standard_args
from weather_skills_core import standard_dataset as std
from weather_skills_core.errors import SkillError, UsageError
from weather_skills_core.units import dequantify_dataset, quantify_dataset

# Accumulated by ``@weather_skill.argument`` (bottom-up); weather_skill reads it.
ARGS_ATTR = "__weather_skill_arguments__"


class Argument:
    """One stacked CLI flag (same kwargs as ``argparse.add_argument``)."""

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


def io_spec_help_label(spec: std.IoSpec) -> str:
    if spec.kind == "unstructured":
        return "unstructured"
    if spec.kind == "figure":
        return "figure"
    if spec.alternatives is None:
        return "any"
    if len(spec.alternatives) == 1:
        dims = sorted(spec.alternatives[0])
        return "+".join(dims) if dims else "any"
    parts = []
    for alt in spec.alternatives:
        parts.append("{" + ",".join(sorted(alt)) + "}")
    return " OR ".join(parts)


def accepts_var_keyword(fn) -> bool:
    return any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in inspect.signature(fn).parameters.values()
    )


def serialize_args(entry_args: dict) -> dict:
    """Copy args into JSON-safe values for a provenance history entry."""
    return json.loads(json.dumps(entry_args, default=str))


def build_history(name, version, args, params, input_paths, path_specs, upstream):
    """Append this skill's provenance entry to the first input's history."""
    entry_args = {k: v for k, v in vars(args).items() if k not in ("input", "output")}
    for dest in ("date", "start_time", "end_time"):
        if dest in params and params[dest] is not None:
            entry_args[dest] = params[dest].isoformat()
    entry_args = serialize_args(entry_args)

    if not input_paths:
        input_field = None
        base_history = []
    elif len(input_paths) == 1:
        if path_specs[0].kind == std.UNSTRUCTURED:
            input_field = {
                "basename": input_paths[0].name,
                "hash": provenance_mod.hash_file(input_paths[0]),
            }
        else:
            input_field = provenance_mod.input_ref(input_paths[0])
        base_history = upstream[0]
    else:
        input_field = []
        for path, spec, hist in zip(input_paths, path_specs, upstream, strict=True):
            if spec.kind == std.UNSTRUCTURED:
                input_field.append(
                    {
                        "basename": path.name,
                        "hash": provenance_mod.hash_file(path),
                        "history": hist,
                    }
                )
            else:
                input_field.append(
                    {
                        "basename": path.name,
                        "hash": provenance_mod.hash_zarr(path),
                        "history": hist,
                    }
                )
        base_history = upstream[0]

    entry = provenance_mod.build_entry(name, version, entry_args, input_field)
    return base_history + [entry]


def write_output(value, out_path, out_spec, history, first_ds):
    """Write one skill result: stamp a figure/Path, or ``to_zarr`` a Dataset."""
    if out_spec.kind == std.FIGURE:
        if not isinstance(value, (str, Path)):
            raise SkillError("figure outputs must return a Path to the written file")
        written = Path(value)
        if written.resolve() != out_path.resolve():
            raise SkillError(f"figure path {written} does not match --output {out_path}")
        provenance_mod.stamp_figure(written, history)
        print(f"Wrote: {out_path}", file=sys.stderr)
        return

    if isinstance(value, (str, Path)):
        written = Path(value)
        if written.resolve() != out_path.resolve():
            raise SkillError(f"returned path {written} does not match --output {out_path}")
        provenance_mod.restamp_zarr(written, history)
        print(f"Wrote: {out_path}", file=sys.stderr)
        return

    if out_spec.kind == "zarr" and hasattr(value, "dims"):
        std.validate_input(value, out_spec, f"output {out_path}")

    if hasattr(value, "pint"):
        value = dequantify_dataset(value)

    if first_ds is not None:
        value.attrs = {**first_ds.attrs, **value.attrs}
    provenance_mod.stamp_zarr(value, history)
    if out_path.exists():
        shutil.rmtree(out_path)
    try:
        value.to_zarr(out_path, mode="w", consolidated=True)
    except Exception:
        if out_path.exists():
            shutil.rmtree(out_path)
        raise
    print(f"Wrote: {out_path}", file=sys.stderr)


def argument(*option_strings, **kwargs):
    """Declare an extra CLI flag under ``@weather_skill`` (argparse-style)."""
    arg = Argument(*option_strings, **kwargs)

    def decorate(fn):
        existing = list(getattr(fn, ARGS_ATTR, []))
        existing.insert(0, arg)
        setattr(fn, ARGS_ATTR, existing)
        return fn

    return decorate


def weather_skill(
    *,
    name,
    version,
    inputs=None,
    outputs=None,
):
    """Turn a function into a weather skill CLI with validated I/O and provenance.

    ``inputs``/``outputs`` are IO spec lists (str; list=OR; tuple=AND; trailing ``+``
    = variadic). Stack ``@weather_skill.argument`` for extra flags. The skill
    must accept ``**kwargs`` (decorator passes ``output`` there).
    """
    raw_inputs = list(inputs or [])
    raw_outputs = list(outputs or [])
    try:
        input_specs, variadic_input = std.normalize_io_specs(
            raw_inputs, allow_variadic=True, for_input=True
        )
        output_specs, _ = std.normalize_io_specs(
            raw_outputs, allow_variadic=False, for_input=False
        )
    except ValueError as exc:
        raise ValueError(f"skill {name!r}: {exc}") from exc

    def decorator(fn):
        arguments = list(getattr(fn, ARGS_ATTR, []))
        for arg in arguments:
            if not isinstance(arg, Argument):
                raise ValueError(
                    f"skill {name!r}: stacked arguments must be Argument instances; "
                    f"got {type(arg).__name__}"
                )

        declared_dests = {arg.dest for arg in arguments}
        has_bbox = "bbox" in declared_dests

        if not accepts_var_keyword(fn):
            raise TypeError(
                f"skill {name!r} must accept **kwargs so the decorator can pass "
                "extra runtime information"
            )

        parser = argparse.ArgumentParser(
            description=fn.__doc__,
            epilog=f"skill version: {version}",
        )
        if input_specs:
            if variadic_input:
                label = io_spec_help_label(input_specs[0])
                help_text = f"Input path (repeat once per input; each must be {label})."
            else:
                n = len(input_specs)
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
        if output_specs:
            n = len(output_specs)
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
            if arg.dest in standard_args.STANDARD_HELP:
                kwargs = standard_args.add_standard_help(
                    kwargs, standard_args.STANDARD_HELP[arg.dest]
                )
            parser.add_argument(*arg.option_strings, **kwargs)

        @functools.wraps(fn)
        def wrapper(argv=None):
            argv = sys.argv[1:] if argv is None else list(argv)
            if has_bbox:
                argv = standard_args.rewrite_bbox_argv(argv)

            try:
                args = parser.parse_args(argv)

                # 1. Paths
                input_paths = [Path(p) for p in (getattr(args, "input", None) or [])]
                output_paths = [Path(p) for p in (getattr(args, "output", None) or [])]
                if input_specs:
                    if variadic_input:
                        if len(input_paths) < 1:
                            raise UsageError("expected at least one --input path")
                    elif len(input_paths) != len(input_specs):
                        raise UsageError(
                            f"expected {len(input_specs)} --input path(s), "
                            f"got {len(input_paths)}"
                        )
                if output_specs and len(output_paths) != len(output_specs):
                    raise UsageError(
                        f"expected {len(output_specs)} --output path(s), "
                        f"got {len(output_paths)}"
                    )
                for path in input_paths:
                    if not path.exists():
                        raise UsageError(f"input not found: {path}")

                if variadic_input:
                    path_specs = [input_specs[0]] * len(input_paths)
                else:
                    path_specs = list(input_specs)

                # 2. Standard kwargs (bbox / region / dates)
                params = standard_args.convert_standard_args(args, arguments)

                # 3. Open inputs
                opened = []
                upstream = []
                for path, spec in zip(input_paths, path_specs, strict=True):
                    if spec.kind == std.UNSTRUCTURED:
                        opened.append(path)
                        upstream.append([])
                    else:
                        ds = xr.open_zarr(path, consolidated=True)
                        std.validate_input(ds, spec, str(path))
                        opened.append(quantify_dataset(ds))
                        upstream.append(provenance_mod.load_history(path))

                # 4. Call skill
                extra = {}
                if output_specs:
                    extra["output"] = (
                        output_paths[0] if len(output_paths) == 1 else output_paths
                    )
                if variadic_input:
                    result = fn(opened, **params, **extra)
                else:
                    result = fn(*opened, **params, **extra)
                if not output_specs:
                    return result

                # 5. Stamp / write each output
                results = result if isinstance(result, (list, tuple)) else [result]
                if len(results) != len(output_specs):
                    raise SkillError(
                        f"skill returned {len(results)} value(s), "
                        f"expected {len(output_specs)}"
                    )

                history = build_history(
                    name, version, args, params, input_paths, path_specs, upstream
                )
                first_ds = next(
                    (item for item in opened if hasattr(item, "attrs")),
                    None,
                )
                for value, out_path, out_spec in zip(
                    results, output_paths, output_specs, strict=True
                ):
                    write_output(value, out_path, out_spec, history, first_ds)

                return result
            except SkillError as exc:
                msg = str(exc)
                print(msg if not exc.prefix else f"Error: {msg}", file=sys.stderr)
                sys.exit(exc.exit_code)

        wrapper.parser = parser
        return wrapper

    return decorator


weather_skill.argument = argument  # type: ignore[attr-defined]
