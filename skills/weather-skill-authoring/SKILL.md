---
name: weather-skill-authoring
description: Playbook for writing a weather skill on the @weather_skill decorator. Covers the declaration surface, dim-ontology IO, stacked argument decorators, provenance, and script layout.
---

# weather-skill-authoring

A skill is `skills/<name>/` with a **SKILL.md** and `scripts/<name>.py`. The
`@weather_skill` decorator owns the CLI, input opening, standard-dataset validation,
provenance, and output writing. The script body is domain logic only.

## References

- `references/STANDARD_DATASET.md` — dim ontology, Zarr shapes, and `weather_skills_history`
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
    name="my-skill",
    version=_SKILL_VERSION,
    inputs=["space"],                # dim / canonical / any; list=OR, tuple=AND
    outputs=["observations"],
)
@weather_skill.argument("--bbox", required=True)
@weather_skill.argument("--start-time", required=True)
@weather_skill.argument("--end-time", required=True)
@weather_skill.argument("--variable", "-v", action="append")
@weather_skill.argument("--smoothing", type=int, help="Window width.")
def my_skill(ds, bbox, start_time, end_time, variable, smoothing, **kwargs):
    """Shown as the CLI description."""
    return ds  # or Path(...)


if __name__ == "__main__":
    my_skill()
```

`@weather_skill.argument(...)` mirrors
`argparse.ArgumentParser.add_argument(*option_strings, **kwargs)`. Stack one
decorator per flag under `@weather_skill(...)`. The skill function **must**
accept `**kwargs`.

Canonical dests get automatic help and post-parse conversion:

| Dest | Flags | Conversion |
| --- | --- | --- |
| `bbox` | `--bbox` | `parse_bbox` → `(N,W,S,E)` |
| `date` | `--date` | `parse_date` → `datetime.date` |
| `start_time` | `--start-time` | `parse_date` → `datetime.date` |
| `end_time` | `--end-time` | `parse_date` → `datetime.date` |

If both `start_time` and `end_time` are set, the decorator also requires
`start_time <= end_time`. It does **not** XOR `--date` with the range flags;
skills that need that check do it in-body.

## Skill shapes

| Kind | Declaration | Return |
| --- | --- | --- |
| Transform | `inputs=[...]`, `outputs=[…]` | Dataset or Path |
| Fetcher | `inputs=[]` (or omit), zarr `outputs` | Dataset or Path |
| Visualization | `outputs=["visualization"]` | Path (write to `kwargs["output"]`) |
| Unstructured input | `inputs=["unstructured"]` | skill receives a `Path` |
| Variadic inputs | `inputs=["any+"]` (or `time+`, …) | skill receives a `list` of opened inputs |
| No-artifact | omit / empty `outputs` | anything (ignored) |

## Inputs / outputs ontology

Within one slot: **list = OR**, **tuple = AND**, **string = atom** (canonical,
dimension, `any`, `unstructured`, or output-only `visualization`).

Canonicals (prefer primary): `observations` (+ aliases analysis/retrieval/field),
`forecast`, `ensemble_forecast`, `station`. Dims: `space`, `time`, `init_time`,
`prediction_timedelta`, `member`, `day_of_year`/`doy`, `point_id`, `x`, `y`.

Be specific when the skill's contract is dimensional (`inputs=["space"]` for
clip); use `any` / `any+` when shape-agnostic. See `references/STANDARD_DATASET.md`.

## Provenance

The decorator appends a `weather_skills_history` entry. When `outputs` is
declared, `--output` path(s) are passed as `output` in `**kwargs`. Returned
Datasets are checked against the output slot dims before write (Path returns
skip that check).

Do not clear or rewrite history yourself; set `weather_skills_source` on fetcher
Datasets before return if needed.

## Units helpers

`to_standard_units(ds)` / `units_equal(a, b)` — normalize temp → `degree_Celsius`
and precip → `mm day-1` / `mm` (requires `weather-skills-core[units]` / `cf-units`).

## Errors

Raise `UsageError` (exit 2, pre-network) or `DataError` (exit 1) from
`weather_skills_core.errors`.
