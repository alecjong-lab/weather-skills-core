# weather-skills-core

Weather skills turn scientific weather-data pipelines into fixed, reviewable
tools. You write the work in ordinary Python — fetch a forecast, clip a region,
convert units, make a plot — and `@weather_skill` makes that function callable
from the CLI or an agent, with provenance on every output. The skill is the
code you checked in; nothing is generated at runtime.

```bash
# Fetch an ensemble forecast over Kenya
uv run ecmwf-fetch.py --bbox 5/34/-5/42 --date 2026-01-15 -o forecast.zarr

# Clip precip observations and compare against the forecast
uv run clip-region.py -i imerg.zarr -o kenya.zarr --bbox 5/34/-5/42
uv run plot-compare.py -i kenya.zarr -i forecast.zarr -o compare.png
```

## Writing a skill

Stack `@weather_skill.argument(...)` decorators to declare the CLI; they take
the same kwargs as `argparse.add_argument`. The decorator parses argv, opens
Zarr inputs, calls your function, and writes or stamps outputs.

```python
from weather_skills_core import Dataset, weather_skill

@weather_skill(name="clip-region", version="0.1.0")
@weather_skill.argument("-i", "--input", type=Dataset("spatial"), required=True, dest="ds")
@weather_skill.argument("--bbox", required=True)
def clip_region(ds, output, bbox, **kwargs):
    north, west, south, east = bbox  # already (N, W, S, E) floats
    clipped = ds.sel(
        lat=slice(south, north),
        lon=slice(west, east),
    )
    return clipped  # decorator stamps provenance and writes -o/--output
```

`ds` is the opened Zarr, `bbox` is already parsed, and `output` is injected
(do not declare `-o` yourself). The body is ordinary xarray: subset the grid
and return the result.

### Function parameters

The decorator always calls your function as `fn(**params)` — keyword arguments
only. Each CLI flag becomes one of those names:

| Where it comes from | Parameter name |
| --- | --- |
| `--start-time` | `start_time` (hyphens become underscores) |
| `--input` with `dest="ds"` | `ds` (without dest it would be `input`) |
| `-o/--output` | `output` — injected; do not declare this flag yourself |

The skill function must accept `**kwargs`. The decorator may pass keys you
did not list as named parameters, and without `**kwargs` the skill refuses to
load. Bind the values you use as named parameters and let `**kwargs` absorb
the rest.

### Zarr inputs

`type=Dataset(...)` means the CLI takes a path string, and your function
receives an opened `xarray.Dataset` that already passed the dimension check
and has pint units attached. Use it for weather-skills Zarr stores.

Opaque files (GeoJSON, `.eml`, a PNG you read) use `type=Path`. Those stay
paths; the decorator does not open them.

### Outputs

The decorator owns `-o/--output` (repeatable, required). Do not add that
argument yourself. `output` is a `Path` when the user passed one path, or a
`list[Path]` when they repeated `-o`.

What you return decides how that path is filled. The number of returned
artifacts must match the number of `--output` paths.

| Return | What the decorator does |
| --- | --- |
| `xr.Dataset` | stamp provenance and `to_zarr` to `--output` |
| `Path` | stamp that file (it must be the `--output` path; typical for a PNG you already wrote) |
| a sequence | one write per `--output` |
| `None` | skip write — the skill already wrote the file |

For skills that only print to stdout and write no file, pass `output=False`:
`@weather_skill(..., output=False)`.

The next example adds a custom flag alongside the standard ones:

```python
@weather_skill(name="my-fancy-skill", version="0.1.0")
@weather_skill.argument("-i", "--input", type=Dataset("any"), required=True, dest="ds")
@weather_skill.argument("--bbox")
@weather_skill.argument("--start-time", required=True)
@weather_skill.argument("--end-time", required=True)
@weather_skill.argument("--corr-coefficient", type=int)
def my_fancy_skill(ds, output, bbox, start_time, end_time, corr_coefficient, **kwargs):
    ...
    return result_ds
```

`--corr-coefficient` arrives as `corr_coefficient`, the same way `--bbox`
arrives as `bbox`. Standard flags get extra parsing (see below); custom flags
use ordinary argparse `type=` / `action=`.

## Why dimensions are standardized

Weather data comes from many sources with different shapes and names. Skills
share one contract so a forecast fetch, an observation clip, and a model
comparison can plug together without custom glue.

We follow [CF conventions](https://cfconventions.org/) for coordinates and
metadata, and expose a small set of **standard dimensions**. `Dataset(...)`
declares what an input must have: either those dimension names, or a **type**
— a short alias for a fixed set of required dimensions (`forecast` means
`lat` + `lon` + `init_time` + `prediction_timedelta`). The decorator checks
that before your function runs.

On disk, common aliases are accepted (`step` counts as `prediction_timedelta`,
`number` as `member`). Declare the ontology name or type rather than listing
every alias.

## Dataset inputs

| Form | Meaning | Example |
| --- | --- | --- |
| String type or dim | That type or dim | `Dataset("spatial")` |
| Comma string | All of these (AND) | `Dataset("lat, lon")` |
| Tuple | All of these (AND) | `Dataset(("lat", "lon", "member"))` |
| List | Any one of these (OR) | `Dataset(["forecast", "ensemble_forecast"])` |
| `"any"` | Any Zarr (no dim check) | `Dataset("any")` |

Pass several Zarrs with `nargs=2` or `nargs="+"` on one Dataset argument, or
with separate Dataset flags.

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

| Type aliases | Required dimensions |
| --- | --- |
| `spatial`, `space` | `lat` + `lon` |
| `observations`, `obs`, `analysis`, `retrieval`, `field`, `data` | `lat` + `lon` + `time` |
| `forecast` | `lat` + `lon` + `init_time` + `prediction_timedelta` |
| `vertical_forecast` | forecast dims + `vertical` |
| `ensemble_forecast` | forecast dims + `member` |
| `point_obs`, `station` | `point_id` + `time` |
| `any` | any Zarr (no dimension check) |

See
[`skills/weather-skill-authoring/references/STANDARD_DATASET.md`](skills/weather-skill-authoring/references/STANDARD_DATASET.md)
for the full contract.

## Standard flags

A few shared names get extra help text, parsing, and checks so every skill
that uses them behaves the same way. Declare them with the canonical flags
below; do **not** re-parse in the skill body (no `bbox.split("/")`, no
`date.fromisoformat` on these).

`--bbox` arrives as `(north, west, south, east)` floats. Named places are not
a decorator flag — compose with resolve-region and pass the printed bbox.
`--start-time` / `--end-time` arrive as `datetime.date`, with a check that
start is not after end. Relative dates (`latest`, `now-3d`) are not parsed
here — compose with resolve-time and pass the printed flags.

| Parameter | Flag | What you get |
| --- | --- | --- |
| `bbox` | `--bbox` | `(N, W, S, E)` floats |
| `date` | `--date` | `datetime.date` (`YYYY-MM-DD`) |
| `start_time` | `--start-time` | Range start as `datetime.date` |
| `end_time` | `--end-time` | Range end as `datetime.date` |
| `variable` | `--variable` / `-v` | Variable name(s); `action="append"` for several |

Canonical spellings (`--bbox`, dates, `--variable`) are linted; Dataset input
flag names are free-form (`-i/--input`, `--forecast`, …).

## Units

Fetch writes precipitation as a **rate** (`mm day-1`), temperature as
`degree_Celsius`. Period **amounts** (`mm`) come from multiplying a rate by a
stamped `aggregation_period` (`convert-to-totals` / `rate_to_total`). Most
skills accept rates and amounts alike; those totals helpers refuse amounts so
they cannot double-count.

The decorator quantifies units when it opens a Zarr and dequantifies before
it writes. Known kinds (temp, precip) must carry parseable `units`; other
variables may include units optionally.

See
[`skills/weather-skill-authoring/references/UNITS.md`](skills/weather-skill-authoring/references/UNITS.md)
for the full units contract.

## Install

```
uv add "weather-skills-core @ git+https://github.com/rhiza-research/weather-skills-core"
```

## Development

We are building toward an **open registry of shared weather skills** that
anyone can use and contribute to — fetchers, transforms, and figures on the
same standard dataset and provenance model. Today this repo is the core
library and authoring tools; companion collections (such as
[weather-skills](https://github.com/rhiza-research/weather-skills)) already
ship real skills. Next: clearer contribution paths, stronger linting and CI,
and a public place to discover and publish skills.

```
uv sync
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run pre-commit run --all-files
```

To author a skill, see
[`skills/weather-skill-authoring/SKILL.md`](skills/weather-skill-authoring/SKILL.md)
and the linting CLI (`weather-skills-core lint`). Contributions that improve
the decorator, the standard dataset contract, or contributor docs are welcome
— open a PR against this repository.
