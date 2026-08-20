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
from weather_skills_core import Dataset, weather_skill

@weather_skill(name="clip-region", version="0.1.0")
@weather_skill.argument("-i", "--input", type=Dataset("spatial"), required=True, dest="ds")
@weather_skill.argument("--bbox", required=True)
def clip_region(ds, output, bbox, **kwargs):
    return ds  # or a Path you already wrote
```

A skill with standard args plus a custom flag:

```python
@weather_skill(name="my-fancy-skill", version="0.1.0")
@weather_skill.argument("-i", "--input", type=Dataset("any"), required=True, dest="ds")
@weather_skill.argument("--bbox")
@weather_skill.argument("--start-time", required=True)
@weather_skill.argument("--end-time", required=True)
@weather_skill.argument("--corr-coefficient", type=int)
def my_fancy_skill(ds, output, bbox, start_time, end_time, corr_coefficient, **kwargs):
    ...
    return result_ds  # or a Path to an already-written file
```

Always take `**kwargs`. Every flag you declare with
`@weather_skill.argument(...)` is passed into your function that way (match the
dest as a named parameter, or read it from kwargs). Use `type=Dataset(...)` for
Zarr inputs (opened and dim-checked). The decorator owns `-o/--output` and
injects `output`; returning an `xr.Dataset` writes Zarr, returning a `Path`
stamps that file, and the number of returned values must match the number of
`--output` paths.

Stack more flags with `@weather_skill.argument(...)` (same signature as
`argparse.add_argument`). The decorator opens Dataset inputs, runs your function,
and writes/stamps outputs with provenance.

## Why dimensions are standardized

Weather data comes from many sources with different shapes and names. Skills
share one contract so a forecast fetch, an observation clip, and a model
comparison can plug together without custom glue.

We follow [CF conventions](https://cfconventions.org/) for coordinates and
metadata, and expose a small set of **standard dimensions**. Skills declare
what they need on `Dataset(...)` either as those dimension names, or as a
**type** — a short alias for a fixed set of required dimensions (for example
`forecast` means `lat` + `lon` + `init_time` + `prediction_timedelta`). That is
how the decorator checks inputs before your function runs.

## Inputs and outputs

`type=Dataset(...)` on an argument marks a Zarr input. Forms:

| Form | Meaning | Example |
| --- | --- | --- |
| String type/dim | That type or dim | `Dataset("spatial")` |
| Comma string | All of these (AND) | `Dataset("lat, lon")` |
| Tuple | All of these (AND) | `Dataset(("lat", "lon", "member"))` |
| List | Any one of these (OR) | `Dataset(["forecast", "ensemble_forecast"])` |

`-o/--output` is owned by the decorator (repeatable). Multi-input uses `nargs` /
`append` or separate Dataset flags. Opaque files use `Path`, not `Dataset`.
The number of returned artifacts must match the number of `--output` paths.

### Dimensions

| Name | Meaning |
| --- | --- |
| `lat` | Latitude (regular grid) |
| `lon` | Longitude (regular grid) |
| `time` | Valid time |
| `init_time` | Forecast initialization time |
| `prediction_timedelta` | Forecast lead time |
| `member` | Ensemble member |
| `vertical` | Vertical level (pressure, height, …) |
| `day_of_year` | Day of year |
| `point_id` | Station or point id |
| `x`, `y` | Coordinates for irregular gridded data (e.g. projected meshes) |

### Types

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

Opaque files and figures use `pathlib.Path`, not `Dataset`.

Full details:
[`skills/weather-skill-authoring/references/STANDARD_DATASET.md`](skills/weather-skill-authoring/references/STANDARD_DATASET.md).

## Units

Most skills accept precip **rates** and **amounts**. Fetch writes accumulated
variables as rates: temp → `degree_Celsius`, precip → `mm day-1`. Known
standard kinds must carry `units` for explicit skill treatment; other
variables may include units optionally. When you need period amounts (`mm`),
convert with the totals utilities after aggregation stamps
`aggregation_period`. `convert-to-totals` / `rate_to_total` refuse inputs that
are already amounts (multiplying again would double-count). The decorator
quantifies on input and dequantifies before writing Zarr. Fetch stamps
`data_interval` (uniform) or CF `{dim}_bounds` (irregular); aggregation adds
`aggregation_period` and `aggregation_coverage`. Convert-to-totals requires
those stamps, 100% coverage by default, and non-overlapping intervals.

Full details:
[`skills/weather-skill-authoring/references/UNITS.md`](skills/weather-skill-authoring/references/UNITS.md).

## Arguments

Declare CLI flags with stacked `@weather_skill.argument(...)` decorators (same
kwargs as `argparse.add_argument`). **Every** declared flag is passed into your
skill as a keyword argument — custom ones included. In the fancy-skill example
above, `corr_coefficient` arrives the same way `bbox` and `start_time` do;
only the conversion rules differ.

### Standard arguments

A few shared names get extra help text, parsing, and checks so every skill that
uses them behaves the same way. Declare them with the canonical flags below;
do **not** re-parse in the skill body (no `bbox.split("/")`, no
`date.fromisoformat` on these).

For example, add `--bbox` and you receive a parsed
`(north, west, south, east)` float tuple. Named places are not a decorator
flag — compose with the resolve-region skill and pass the printed bbox here.
Add `--start-time` / `--end-time` and you get `datetime.date` values, with a
check that the start is not after the end. Relative dates (`latest`, `now-3d`)
are not parsed here — compose with the resolve-time skill and pass the printed
flags.

| Argument | Flag | What you get |
| --- | --- | --- |
| `bbox` | `--bbox` | Bounding box `(N, W, S, E)` floats |
| `date` | `--date` | Single `datetime.date` (`YYYY-MM-DD`) |
| `start_time` | `--start-time` | Range start as `datetime.date` |
| `end_time` | `--end-time` | Range end as `datetime.date` |
| `variable` | `--variable` / `-v` | Variable name(s); use `action="append"` for several |

Path I/O: Dataset-typed args for Zarr inputs (any flag names); the decorator
owns `-o/--output`.

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
