---
name: weather-skill-authoring
description: Playbook for writing a weather skill on the @weather_skill decorator. Covers the declaration surface, envelope types, absolute dates, provenance, and script layout.
---

# weather-skill-authoring

A skill is `skills/<name>/` with a **SKILL.md** and `scripts/<name>.py`. The
`@weather_skill` decorator owns the CLI, input opening, envelope validation,
provenance, and output writing. The script body is domain logic only.

## References

- `references/ENVELOPE.md` — Zarr shapes and `weather_skills_history`
- `references/CONVENTIONS.md` — canonical CLI flag names

## Declaration

```python
# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core @ git+https://github.com/rhiza-research/weather-skills-core",
# ]
# ///
from weather_skills_core import weather_skill

_SKILL_VERSION = "0.1.0"


@weather_skill(
    "my-skill",
    _SKILL_VERSION,
    inputs=["data"],                 # list; empty = fetcher; "any" / "any+" allowed
    outputs=["data"],                # data|forecast|station|any|unstructured|visualization
    dates="range",                   # None | "single" | "range" | "either"
    region="optional",               # None | "required" | "optional" → --bbox
    variable="single_required",      # None | single_*/multiple_*
    extra_args=[
        (("--smoothing",), {"type": int, "help": "Window width."}),
    ],
)
def my_skill(ds, bbox, start_time, end_time, variable, smoothing):
    """Shown as the CLI description."""
    return ds  # or Path(...)


if __name__ == "__main__":
    my_skill()
```

`extra_args` entries are `(option_strings, kwargs)` passed to
`parser.add_argument(*option_strings, **kwargs)`.

## Skill shapes

| Kind | Declaration | Return |
| --- | --- | --- |
| Transform | `inputs=[...]`, `outputs=[zarr type]` | Dataset or Path |
| Fetcher | `inputs=[]` (or omit), zarr `outputs` | Dataset or Path |
| Visualization | `outputs=["visualization"]` | Path (skill writes PNG/JPEG/HTML to `output` kwarg) |
| Unstructured input | `inputs=["unstructured"]` | skill receives a `Path` |
| Variadic inputs | `inputs=["any+"]` (or `data+`, …) | skill receives a `list` of opened inputs |
| No-artifact | omit / empty `outputs` | anything (ignored) |

Variadic form is a **single** `type+` entry (≥1 `--input`). Do not mix fixed and
variadic slots. Skills that need ≥2 inputs (e.g. concat) enforce that in-body.

## Types

- `data`, `forecast`, `station` — Zarr envelopes (validated on open)
- `any` — any Zarr envelope (`data|forecast|station`); valid on inputs and outputs
  (output `any` means passthrough / shape-preserving write)
- `unstructured` — opaque file; passed as `Path`
- `visualization` — output-only; decorator stamps provenance into file metadata

## Dates

`--start`/`--end` or `--date` take **absolute `YYYY-MM-DD` only**. Modes:

- `"range"` — required `--start` and `--end`
- `"single"` — required `--date`
- `"either"` — `--date` XOR (`--start` + `--end`); skill receives all three kwargs
  with the unused side `None`

Relative / rolling dates are resolved by the caller before invoking the skill.

## Provenance

The decorator appends a `weather_skills_history` entry:

- Dataset return → stamp attrs, then write Zarr to `--output`
- Path return (Zarr) → reopen and restamp in place
- Path return (`visualization`) → embed JSON history in PNG/JPEG/HTML metadata

When `outputs` is declared, the decorator passes `--output` path(s) as an
`output` keyword when the skill function accepts it: a single `Path` when
there is one output, or a `list[Path]` when there are several. Visualization
skills must declare `output`, write the file to that path, and return the
same `Path`. Skills that return a Dataset and let the decorator write Zarr
may omit `output` from their signature.

Do not clear or rewrite history yourself; set `weather_skills_source` on fetcher
Datasets before return if needed.

## Errors

Raise `UsageError` (exit 2, pre-network) or `DataError` (exit 1) from
`weather_skills_core.errors`.
