# WeatherSkills standard dataset

Zarr inputs and outputs are declared by **required dimensions** (or canonical
shorthands that expand to dims). Within one `inputs=` / `outputs=` slot:

| Form | Meaning | Example |
| --- | --- | --- |
| string (canonical) | Expand shorthand to required dims | `"forecast"` |
| string (dimension) | Require that one dim | `"time"` |
| string `"any"` | No dim requirements | `"any"` |
| string with `+` | Variadic (≥1 paths), same requirements each | `"any+"`, `"time+"` |
| **tuple** | **AND** — every entry required | `("space", "time")` |
| **list** | **OR** — any alternative matches | `["forecast", "ensemble_forecast"]` |

Outer list = multiple slots (one per `--input` / `--output`).

## Dimension vocabulary

| Name | On-disk detection |
| --- | --- |
| `space` | Lat/lon pair via CF / name heuristics |
| `time` | Time-like dim (CF / `time`) |
| `init_time` | `init_time` dim/coord, or scalar `time` + step (forecast init) |
| `prediction_timedelta` | Lead/step dim (`step`, `prediction_timedelta`, `lead_time`) |
| `member` | Ensemble member (`number`, `member`, `realization`) |
| `day_of_year` / `doy` | Day-of-year dim |
| `point_id` | Station/point dim (`station_id` / `point_id`) with lat/lon on that dim |
| `x` / `y` | Projected axis dims (when declared explicitly) |

## Canonical shorthands

Equivalence families share the same dim requirements; prefer the **primary**
name in skill declarations.

| Family (primary first) | Expands to (AND) |
| --- | --- |
| **`observations`**, `analysis`, `retrieval`, `field` | `space` + `time` |
| `forecast` | `space` + `init_time` + `prediction_timedelta` |
| `ensemble_forecast` | forecast dims + `member` |
| `station` | `point_id` + `time` |

Legacy `"data"` still expands like `observations` but prefer `observations`.

Other I/O kinds (not dim-validated):

- `unstructured` — opaque file path
- `visualization` — PNG / JPEG / HTML; provenance stamped into file metadata

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
