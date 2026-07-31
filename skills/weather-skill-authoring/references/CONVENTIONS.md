# CLI flag conventions

Skills declare their CLI through `@weather_skill` and stacked
`@weather_skill.argument(...)` decorators. A flag that means the same thing on
different skills must have the same name.

`--input` / `--output` come from `inputs=` / `outputs=`; everything else is
`@weather_skill.argument(...)`. The conformance linter checks naming
(WSK101 / WSK201 / WSK202).

## Standard names

Shared flag names so skills behave the same way. Declare them with
`@weather_skill.argument(...)` and the decorator parses them for you (see the
core README). Types and dimensions live in STANDARD_DATASET.md (`figure` for
plots, `point_obs` for station/point time series).

### Inputs and outputs

| Concept | Flag | Notes |
| --- | --- | --- |
| Inputs | `--input` / `-i`, repeated | Exactly `len(inputs)` paths, in order. A single `…+` entry means ≥1 paths (variadic). |
| Outputs | `--output` / `-o`, repeated | Exactly `len(outputs)` paths, in order. Passed to the skill as `output` in `**kwargs`. |

### Region

| Concept | Flag | Notes |
| --- | --- | --- |
| Bounding box | `--bbox` | CLI is `N/W/S/E` decimal degrees. Skill receives `(N, W, S, E)` floats — do not re-parse. |
| Country / region | `--region` | CLI string (e.g. `Kenya`). Skill gets kwargs `region` as a GeoDataFrame; `bbox` is also filled. Requires `weather-skills-core[geo]`. Do not pass with `--bbox`. |
| Boundary GeoJSON | `--geojson` / `--mask-geojson` | Skill-specific. |

### Time

| Concept | Flag | Notes |
| --- | --- | --- |
| Date range | `--start-time` / `--end-time` | CLI is absolute `YYYY-MM-DD` (inclusive). Skill receives `datetime.date`; when both set, `start_time <= end_time`. Do not re-parse. |
| Single date | `--date` | CLI is absolute `YYYY-MM-DD`. Skill receives `datetime.date`. |

Relative / rolling dates (`now`, `latest`, offsets) are resolved by the caller
before invoking weather skills. Mutual exclusion of `--date` vs the range flags
is skill-owned when needed.

### Variable

| Concept | Flag | Notes |
| --- | --- | --- |
| Variable | `--variable` / `-v` | Declare with `@weather_skill.argument("--variable", "-v", ...)` (`action="append"` when multi). |

### Common extras

| Concept | Flag | Notes |
| --- | --- | --- |
| Calendar | `--calendar` | CF calendar name. |
| Calendar align | `--align-on` | `date` \| `year`. |
| Workers | `--workers` | Parallelism (skill-specific). |
| Title | `--title` | Plot title (skill-specific). |
