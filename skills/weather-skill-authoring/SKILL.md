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
- `references/UNITS.md` — rate defaults for accumulated vars, quantify/dequantify, totals utilities
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
    inputs=["spatial"],
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
function **must** accept `**kwargs`. **Every** declared flag is injected as a
keyword argument (named parameter or via kwargs) — custom flags the same as
standard ones.

## Standard arguments

Shared by many weather skills. Declare them with the canonical flags below;
the decorator adds help, parses CLI strings, and injects kwargs. The skill body
must **not** re-parse those values (no `bbox.split("/")`, no
`date.fromisoformat` on `bbox` / `date` / `start_time` / `end_time`). Format for
APIs with `.isoformat()` if needed, or apply a spatial subset in the skill
itself. Other `@weather_skill.argument` flags still arrive as kwargs; they just
use ordinary argparse typing (`type=`, `action=`, …) without this extra
conversion.

| Argument | Flag | What you get |
| --- | --- | --- |
| `bbox` | `--bbox` | Bounding box `(N, W, S, E)` floats |
| `region` | `--region` | GeoDataFrame in kwargs as `region` (CLI string e.g. `Kenya`; also fills `bbox`) |
| `date` | `--date` | `datetime.date` |
| `start_time` | `--start-time` | Range start as `datetime.date` |
| `end_time` | `--end-time` | Range end as `datetime.date` |
| `variable` | `--variable` / `-v` | Variable name(s) |

When both `start_time` and `end_time` are set, start must be ≤ end. Pass
`--region` or `--bbox`, not both. `--region` needs `weather-skills-core[geo]`;
the skill body receives a GeoDataFrame, not the CLI string.

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

CF-based standard dimensions: `lat`, `lon`, `time`, `init_time`,
`prediction_timedelta`, `member`, `vertical`, `day_of_year`, `point_id`, `x`, `y`.

Types are aliases for specific required dimensions:

| Type aliases | Required dimensions |
| --- | --- |
| `spatial`, `space` | `lat` + `lon` |
| `observations`, `obs`, `analysis`, `retrieval`, `field`, `data` | `lat` + `lon` + `time` |
| `forecast` | `lat` + `lon` + `init_time` + `prediction_timedelta` |
| `vertical_forecast` | forecast dims + `vertical` |
| `ensemble_forecast` | forecast dims + `member` |
| `point_obs`, `station` | `point_id` + `time` |
| `any` | any Zarr (no dimension check) |
| `figure` | PNG / JPEG / HTML (output only) |
| `unstructured` | opaque file path |

Example: `inputs=["spatial"]` for clip; `inputs=["forecast"]` for a forecast-only
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
and precip **rates** → `mm day-1`. Most skills expect rates for accumulated
variables; use `convert-to-totals` / `rate_to_total` when you need amounts.
`@weather_skill` quantifies inputs via `pint-xarray`. Known standard kinds
with `units_required` in `STANDARD` require `units` for explicit skill treatment;
other variables may omit them. Skills that intentionally accept totals set
`allow_precip_totals=True` (plotters / `deaccumulate`).

## Errors

Raise `UsageError` (exit 2, pre-network) or `DataError` (exit 1) from
`weather_skills_core.errors`.
