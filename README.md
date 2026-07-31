# weather-skills-core

Weather skills turn scientific weather-data pipelines into fixed, reviewable
tools. You write the work in ordinary Python — fetch a forecast, pull station
observations, compare models, convert units, clip to a region, make a plot —
and `@weather_skill` makes that function callable from the CLI or an agent,
with auditable provenance on every output. No dynamic code generation: the
skill is the code you checked in.

```bash
# Fetch an ensemble forecast over Kenya
uv run ecmwf-fetch.py --bbox 5/34/-5/42 --date 2026-01-15 -o forecast.zarr

# Clip precip observations and compare against the forecast
uv run clip-region.py -i imerg.zarr -o kenya.zarr --bbox 5/34/-5/42
uv run plot-compare.py -i kenya.zarr -i forecast.zarr -o compare.png
```

A minimal skill:

```python
from weather_skills_core import weather_skill

@weather_skill(
    name="clip-region",
    version="0.1.0",
    inputs=["space"],
    outputs=["space"],
)
@weather_skill.argument("--bbox", required=True)
def clip_region(ds, bbox, **kwargs):
    return ds  # or a Path you already wrote
```

A skill with standard args plus a custom flag:

```python
@weather_skill(
    name="my-fancy-skill",
    version="0.1.0",
    inputs=["any"],
    outputs=["any"],
)
@weather_skill.argument("--bbox")
@weather_skill.argument("--start-time", required=True)
@weather_skill.argument("--end-time", required=True)
@weather_skill.argument("--corr-coefficient", type=int)
def my_fancy_skill(ds, bbox, start_time, end_time, corr_coefficient, **kwargs):
    ...
    return result_ds  # or a Path to an already-written file
```

Always take `**kwargs`. The decorator does not only call your function — it
fills kwargs with the parsed values for every declared argument (and with
runtime extras like `output`). You receive ready-to-use Python objects, not
raw CLI strings. For example, declare `--bbox` and pass Kenya's box on the
command line (often from `resolve-region`); your skill gets
`bbox=(north, west, south, east)` floats for Kenya, not `"5/34/-5/42"` to
split yourself. The same idea applies to dates (`datetime.date`) and other
standard args.

Stack more flags with `@weather_skill.argument(...)` (same signature as
`argparse.add_argument`). The decorator opens inputs, runs your function, and
writes Zarr outputs with provenance.

## Why dimensions are standardized

Weather data comes from many sources with different shapes and names. Skills
share one contract so a forecast fetch, an observation clip, and a model
comparison can plug together without custom glue.

We follow [CF conventions](https://cfconventions.org/) for coordinates and
metadata, and expose a small set of **standard dimensions**. Skills declare
what they need either as those dimension names, or as a **type** — a short
alias for a fixed set of required dimensions (for example `forecast` means
`space` + `init_time` + `prediction_timedelta`). That is how the decorator
checks inputs and outputs before and after your function runs.

## Inputs and outputs

### Dimensions

| Name | Meaning |
| --- | --- |
| `space` | Regular grid (`lat` + `lon`) |
| `time` | Valid time |
| `init_time` | Forecast initialization time |
| `prediction_timedelta` | Forecast lead time |
| `member` | Ensemble member |
| `day_of_year` | Day of year |
| `point_id` | Station or point id |
| `x`, `y` | Coordinates for irregular gridded data (e.g. projected meshes) |

### Types

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

One `inputs=` / `outputs=` entry per CLI path. Tuple = all required; list = any
one; trailing `+` = one or more paths.

Full details:
[`skills/weather-skill-authoring/references/STANDARD_DATASET.md`](skills/weather-skill-authoring/references/STANDARD_DATASET.md).

## Standard arguments

Many weather skills need the same inputs: a region, a date or date range, a
variable name. Declare those with `@weather_skill.argument(...)` using the
shared names below, and the decorator handles them for you — help text, parsing,
and basic checks — so every skill behaves the same way.

For example, add `--bbox` and the decorator puts a parsed
`(north, west, south, east)` bounding box in your function kwargs (and accepts
negative latitudes). Add `--start-time` / `--end-time` and you get
`datetime.date` values, with a check that the start is not after the end. For a
named country or region, run the `resolve-region` skill first to get a standard
bbox (and optional boundary polygon), then pass that into skills that take
`--bbox`.

| Argument | Flag | What you get |
| --- | --- | --- |
| `bbox` | `--bbox` | Bounding box `(N, W, S, E)` floats |
| `date` | `--date` | Single `datetime.date` (`YYYY-MM-DD`) |
| `start_time` | `--start-time` | Range start as `datetime.date` |
| `end_time` | `--end-time` | Range end as `datetime.date` |
| `variable` | `--variable` / `-v` | Variable name(s); use `action="append"` for several |

`--input` / `--output` come only from `inputs=` / `outputs=` — do not redeclare
them. When outputs are declared, your function also receives `output` in
`**kwargs` (a `Path`, or a list for multi-output).

## Install

```
uv add "weather-skills-core @ git+https://github.com/rhiza-research/weather-skills-core"
```

## Development

We are building toward an **open registry of shared weather skills** that anyone
can use and contribute to — fetchers, transforms, and figures that plug into the
same standard dataset and provenance model. That work is in progress. Today this
repo is the core library and authoring tools; companion skill collections (such
as forecasting-skills) already ship real skills. Next we want clearer
contribution paths, stronger skill linting and CI checks, and a public place to
discover and publish skills.

Local development:

```
uv sync
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run pre-commit run --all-files
```

To author a skill, see
[`skills/weather-skill-authoring/SKILL.md`](skills/weather-skill-authoring/SKILL.md)
and the linting CLI (`weather-skills-core lint`; implementation lives under
`weather_skills_core.linting`). Contributions that improve the decorator, the
standard dataset contract, or contributor docs are welcome — open a PR against
this repository.
