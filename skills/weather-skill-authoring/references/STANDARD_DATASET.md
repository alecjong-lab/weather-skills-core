# WeatherSkills standard dataset

Skills read and write [CF-compliant](https://cfconventions.org/) Zarr stores.
We standardize dimensions so weather data from different sources can move
through the same pipeline: a skill declares what it needs, and the decorator
checks that the Zarr has those dimensions.

Use the dimension names below, or a **type** — an alias for a fixed set of
required dimensions.

## Dimensions

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

## Types

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

## Declaring I/O on a skill

Declare Zarr inputs with `type=Dataset(...)` on `@weather_skill.argument`.
Declare the write path with `type=Path` on `-o/--output`.

| `Dataset(...)` form | Meaning | Example |
| --- | --- | --- |
| String type/dim | That type or dim | `Dataset("spatial")` |
| Comma string | All required (AND) | `Dataset("lat, lon")` |
| Tuple | All required (AND) | `Dataset(("lat", "lon", "member"))` |
| List | Any one alternative (OR) | `Dataset(["forecast", "ensemble_forecast"])` |

```python
from weather_skills_core import Dataset, weather_skill

@weather_skill(name="clip-region", version=_SKILL_VERSION)
@weather_skill.argument("-i", "--input", type=Dataset("spatial"), required=True, dest="ds")
def clip_region(ds, output, **kwargs):
    return ds
```

Multi-input: `nargs=2` / `nargs="+"` on one Dataset arg, or separate Dataset flags.
The decorator owns `-o/--output`; return count must match the number of paths.

## Provenance attrs

| Attr | Who sets it | Meaning |
| --- | --- | --- |
| `weather_skills_source` | fetchers (optional) | Where the data came from, e.g. `chirps` |
| `weather_skills_history` | every writing skill | JSON list of steps that produced the file |

Each history entry has `skill`, `version`, `args`, and `input` (with basename and
content `hash`). Plots store the same JSON in file metadata. When that chain is
intact (non-empty and schema-valid), PNG/JPEG figure outputs also get a circular
old-school `weather-skills provenance verified` rubber stamp (bottom-right);
HTML figures get metadata only.

## Units

Give data variables a udunits-parseable `units` attr when they are a **known
standard kind** (skills treat those explicitly). Other variables may include
units optionally. Fetch writes accumulated variables as **rates** (canonical
precip `mm day-1`). Period amounts (`mm`) come from `convert-to-totals` after
aggregation stamps `aggregation_period`; that conversion refuses inputs that
are already amounts.

| Kind | Standard units |
| --- | --- |
| temp | `degree_Celsius` |
| precip | `mm day-1` |
| precip amount | `mm` (via totals utilities) |

Fetch stamps `data_interval` (uniform native spacing, e.g. `1 day`) or CF
`{dim}_bounds` (irregular start/end cells) — not both. Aggregation stamps CF
`cell_methods` (e.g. `time: mean (interval: 1 day)`), `aggregation_period`
(e.g. `7 day`), and `aggregation_coverage` (0–1 per interval). Totals use
`cell_methods` with `sum`. Convert-to-totals refuses overlapping intervals —
`select` a non-overlapping subset first.

Full details: [`UNITS.md`](UNITS.md).

## Writing Zarr

- CF-compliant, `consolidated=True`
- Missing values are NaN
- Clear `.encoding` before `to_zarr`
