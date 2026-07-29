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
    inputs=["data"],                 # list; empty = fetcher
    outputs=["data"],                # data|forecast|station|unstructured|visualization
    dates="range",                   # None | "single" | "range"
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
| Visualization | `outputs=["visualization"]` | Path (skill writes PNG/JPEG/HTML first) |
| Unstructured input | `inputs=["unstructured"]` | skill receives a `Path` |
| No-artifact | omit / empty `outputs` | anything (ignored) |

## Types

- `data`, `forecast`, `station` — Zarr envelopes (validated on open)
- `unstructured` — opaque file; passed as `Path`
- `visualization` — output-only; decorator stamps provenance into file metadata

## Dates

`--start`/`--end` or `--date` take **absolute `YYYY-MM-DD` only**. Relative
dates belong in a separate resolve-dates skill.

## Provenance

The decorator appends a `weather_skills_history` entry:

- Dataset return → stamp attrs, then write Zarr to `--output`
- Path return (Zarr) → reopen and restamp in place
- Path return (`visualization`) → embed JSON history in PNG/JPEG/HTML metadata

Do not clear or rewrite history yourself; set `weather_skills_source` on fetcher
Datasets before return if needed.

## Errors

Raise `UsageError` (exit 2, pre-network) or `DataError` (exit 1) from
`weather_skills_core.errors`.
