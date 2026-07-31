# WeatherSkills standard dataset

Skills read and write CF-compliant Zarr stores. Name your dimensions with the
list below. Skills declare what they need either by those names, or by a short
**type** that stands for a fixed set of them.

## Dimensions

Use these names:

| Name | Meaning |
| --- | --- |
| `space` | Horizontal grid (`latitude` + `longitude`) |
| `time` | Valid time |
| `init_time` | Forecast initialization time |
| `prediction_timedelta` | Forecast lead time |
| `member` | Ensemble member |
| `day_of_year` | Day of year |
| `point_id` | Station or point id |
| `x` | Projected X |
| `y` | Projected Y |

## Types

A type is a shortcut for a set of dimensions:

| Type | Requires |
| --- | --- |
| `observations` | `space`, `time` |
| `forecast` | `space`, `init_time`, `prediction_timedelta` |
| `ensemble_forecast` | `space`, `init_time`, `prediction_timedelta`, `member` |
| `station` | `point_id`, `time` |
| `any` | any Zarr (no dimension check) |
| `unstructured` | a file path (not Zarr) |
| `visualization` | a plot file (PNG / JPEG / HTML) |

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
