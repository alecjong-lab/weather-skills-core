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
`argparse.ArgumentParser.add_argument`. Stack one decorator per flag. The skill
function **must** accept `**kwargs`.

## Standard arguments

Shared by many weather skills. Declare them with the names below and the
decorator handles help, parsing, and checks for you.

| Argument | Flag | What you get |
| --- | --- | --- |
| `bbox` | `--bbox` | Bounding box `(N, W, S, E)` floats |
| `date` | `--date` | `datetime.date` |
| `start_time` | `--start-time` | Range start as `datetime.date` |
| `end_time` | `--end-time` | Range end as `datetime.date` |
| `variable` | `--variable` / `-v` | Variable name(s) |

When both `start_time` and `end_time` are set, start must be ≤ end. For named
regions, use `resolve-region` to get a bbox (and optional polygon), then pass
`--bbox` into skills.

## Skill shapes

| Kind | Declaration | Return |
| --- | --- | --- |
| Transform | `inputs=[...]`, `outputs=[…]` | Dataset or Path |
| Fetcher | `inputs=[]` (or omit), zarr `outputs` | Dataset or Path |
| Figure | `outputs=["figure"]` | Path (write to `kwargs["output"]`) |
| Unstructured input | `inputs=["unstructured"]` | skill receives a `Path` |
| Variadic inputs | `inputs=["any+"]` (or `time+`, …) | skill receives a `list` of opened inputs |
| No-artifact | omit / empty `outputs` | anything (ignored) |

## Inputs / outputs

CF-based standard dimensions: `space`, `time`, `init_time`,
`prediction_timedelta`, `member`, `day_of_year`, `point_id`, `x`, `y`.

Types are aliases for specific required dimensions:

| Type aliases | Required dimensions |
| --- | --- |
| `observations`, `obs`, `analysis`, `retrieval`, `field`, `data` | `space` + `time` |
| `forecast` | `space` + `init_time` + `prediction_timedelta` |
| `ensemble_forecast` | forecast dims + `member` |
| `point_obs`, `station` | `point_id` + `time` |
| `any` | any Zarr (no dimension check) |
| `figure` | PNG / JPEG / HTML (output only) |
| `unstructured` | opaque file path |

Example: `inputs=["space"]` for clip; `inputs=["forecast"]` for a forecast-only
skill. See `references/STANDARD_DATASET.md`.

## Provenance

The decorator appends a `weather_skills_history` entry. When `outputs` is
declared, `--output` path(s) are passed as `output` in `**kwargs`. Returned
Datasets are checked against the declared output dims before write (Path returns
skip that check).

Do not clear or rewrite history yourself; set `weather_skills_source` on fetcher
Datasets before return if needed.

## Units helpers

`to_standard_units(ds)` / `units_equal(a, b)` — normalize temp → `degree_Celsius`
and precip → `mm day-1` / `mm` (requires `weather-skills-core[units]` / `cf-units`).

## Errors

Raise `UsageError` (exit 2, pre-network) or `DataError` (exit 1) from
`weather_skills_core.errors`.
