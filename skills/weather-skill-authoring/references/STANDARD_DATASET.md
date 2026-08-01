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
| `figure` | PNG / JPEG / HTML (output only) |
| `unstructured` | opaque file path |

## Declaring I/O on a skill

Each entry in `inputs=` / `outputs=` is one `--input` / `--output` path. What
you put *in* an entry is a dimension, a type, or a combination:

| Entry form | Meaning | Example entry |
| --- | --- | --- |
| String | That dim or type | `"spatial"` |
| Tuple | All required (AND) | `("lat", "lon", "member")` |
| List | Any one alternative (OR) | `["forecast", "ensemble_forecast"]` |
| Trailing `+` on a string | One or more matching paths | `"any+"` (sole `inputs=` entry) |

```python
@weather_skill(
    name="clip-region",
    version=_SKILL_VERSION,
    inputs=["spatial"],          # one path; needs lat + lon
    outputs=["spatial"],
)
```

Two paths: `inputs=["spatial", "spatial"]`. One path that may be forecast or
ensemble: `inputs=[["forecast", "ensemble_forecast"]]`.

## Provenance attrs

| Attr | Who sets it | Meaning |
| --- | --- | --- |
| `weather_skills_source` | fetchers (optional) | Where the data came from, e.g. `chirps` |
| `weather_skills_history` | every writing skill | JSON list of steps that produced the file |

Each history entry has `skill`, `version`, `args`, and `input` (with basename and
content `hash`). Plots store the same JSON in file metadata.

## Units

Give data variables a udunits-parseable `units` attr. The pipeline is **rates-first**
for precip (canonical `mm day-1`). Period amounts (`mm`) come from the
`convert-to-totals` skill after aggregation stamps `aggregation_period`.

| Kind | Standard units |
| --- | --- |
| temp | `degree_Celsius` |
| precip rate | `mm day-1` |
| precip amount (totals only) | `mm` |

Aggregation stamps CF `cell_methods` (e.g. `time: mean (interval: 1 day)`) and
`aggregation_period` (e.g. `7 day`). Totals use `cell_methods` with `sum`.

## Writing Zarr

- CF-compliant, `consolidated=True`
- Missing values are NaN
- Clear `.encoding` before `to_zarr`
