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
| `space` | Regular grid (`lat` + `lon`) |
| `time` | Valid time |
| `init_time` | Forecast initialization time |
| `prediction_timedelta` | Forecast lead time |
| `member` | Ensemble member |
| `day_of_year` | Day of year |
| `point_id` | Station or point id |
| `x`, `y` | Coordinates for irregular gridded data (e.g. projected meshes) |

## Types

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

## Declaring I/O on a skill

```python
@weather_skill(
    name="clip-region",
    version=_SKILL_VERSION,
    inputs=["space"],          # needs a horizontal grid
    outputs=["space"],
)
```

- One entry per `--input` / `--output`
- Use a type or a dimension name
- Tuple = all required: `("space", "time")`
- List = any one is fine: `["forecast", "ensemble_forecast"]`
- Trailing `+` = one or more paths: `"any+"`

## Provenance attrs

| Attr | Who sets it | Meaning |
| --- | --- | --- |
| `weather_skills_source` | fetchers (optional) | Where the data came from, e.g. `chirps` |
| `weather_skills_history` | every writing skill | JSON list of steps that produced the file |

Each history entry has `skill`, `version`, `args`, and `input` (with basename and
content `hash`). Plots store the same JSON in file metadata.

## Units

Give data variables a udunits-parseable `units` attr. Optional helpers can
normalize common display units (`weather-skills-core[units]`):

| Kind | Standard units |
| --- | --- |
| temperature | `degree_Celsius` |
| precip rate | `mm day-1` |
| precip amount | `mm` |

## Writing Zarr

- CF-compliant, `consolidated=True`
- Missing values are NaN
- Clear `.encoding` before `to_zarr`
