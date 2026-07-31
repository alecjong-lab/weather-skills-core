# WeatherSkills standard dataset

Zarr inputs and outputs are declared by **required dimensions**, or by a
**type** name that expands to a fixed set of dimensions.

## Dimensions

| Dimension | Meaning | Detected on disk as |
| --- | --- | --- |
| `space` | Horizontal grid | Lat/lon pair (CF attrs or name heuristics) |
| `time` | Wall-clock / valid time | Time-like dim (CF / `time`) |
| `init_time` | Forecast initialization | `init_time`, or scalar `time` plus a step dim |
| `prediction_timedelta` | Forecast lead | `step`, `prediction_timedelta`, or `lead_time` |
| `member` | Ensemble member | `number`, `member`, or `realization` |
| `day_of_year` | Day of year (`doy` alias) | Day-of-year dim |
| `point_id` | Station / point | `station_id` or `point_id`, with lat/lon on that dim |
| `x` | Projected X | Declared explicitly (not inferred) |
| `y` | Projected Y | Declared explicitly (not inferred) |

A skill can require any of these directly, e.g. `inputs=["space"]` or
`inputs=[("space", "time")]`.

## Types

Each type is a named shorthand for a set of required dimensions. Prefer the
primary name in skill declarations; aliases are accepted and mean the same thing.

| Type | Implied dimensions | Aliases |
| --- | --- | --- |
| `observations` | `space`, `time` | `analysis`, `retrieval`, `field`, `data` |
| `forecast` | `space`, `init_time`, `prediction_timedelta` | — |
| `ensemble_forecast` | `space`, `init_time`, `prediction_timedelta`, `member` | — |
| `station` | `point_id`, `time` | — |

Special (not dimension-validated):

| Kind | Meaning |
| --- | --- |
| `any` | Zarr with no dim requirements |
| `unstructured` | Opaque file path (`Path`) |
| `visualization` | Output-only PNG / JPEG / HTML |

## Declaring slots

`inputs=` / `outputs=` are lists of **slots** (one per `--input` / `--output`).
Within one slot:

| Form | Meaning | Example |
| --- | --- | --- |
| type or dimension string | Require that type's dims, or that one dim | `"forecast"`, `"time"` |
| `"any"` | No dim requirements | `"any"` |
| string with `+` | Variadic (≥1 paths), same requirements each | `"any+"`, `"time+"` |
| **tuple** | **AND** — every entry required | `("space", "time")` |
| **list** | **OR** — any alternative matches | `["forecast", "ensemble_forecast"]` |

## Attrs

| Attr | Set by | Meaning |
|---|---|---|
| `weather_skills_source` | fetchers (optional) | e.g. `ecmwf-s2s`, `chirps` |
| `weather_skills_history` | every artifact-writing skill | JSON append-only provenance chain |

### `weather_skills_history` schema

JSON array, oldest first. Each entry:

- `skill` — canonical skill name
- `version` — `_SKILL_VERSION` at write time
- `args` — argparse namespace minus input/output paths (resolved absolute dates as ISO strings)
- `input` — `null` for fetchers; `{basename, hash}` for one input; list of `{basename, hash, history}` for multi-input

Visualization files embed the same JSON under the key `weather_skills_history`
(PNG `tEXt`, JPEG EXIF UserComment, HTML `<meta name="weather_skills_history">`).

## Data-variable units

Data variables should carry udunits-parseable `units`. Incoming CF stores are
accepted as-is. Optional helpers in `weather_skills_core.units` (extra
`weather-skills-core[units]`) normalize common display units:

| Kind | Standard units | Typical `standard_name` |
| --- | --- | --- |
| temperature | `degree_Celsius` | keep existing |
| precip rate / flux | `mm day-1` | `lwe_precipitation_rate` |
| precip amount / depth | `mm` | `lwe_thickness_of_precipitation_amount` |

Use `to_standard_units(ds)` / `units_equal(a, b)` from fetchers, plots, or
`unit-convert --to-standard`.

## Conventions

- CF-compliant Zarr; use cf-xarray for coord identification.
- Write with `consolidated=True`.
- Missing data is NaN.
- Per-variable `encoding` is not part of the contract; clear `.encoding` before `to_zarr`.
