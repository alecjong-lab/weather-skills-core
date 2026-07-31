# CLI flag conventions

Skills declare their CLI through `@weather_skill` and stacked
`@weather_skill.argument(...)` decorators. A flag that means the same thing on
different skills must have the same name.

`--input` / `--output` come from `inputs=` / `outputs=`; everything else is
`@weather_skill.argument(...)`. The conformance linter checks naming
(WSK101 / WSK201 / WSK202).

## Canonical names

### Inputs and outputs

| Concept | Flag | Notes |
| --- | --- | --- |
| Inputs | `--input` / `-i`, repeated | Exactly `len(inputs)` paths, in order. A single `…+` entry means ≥1 paths (variadic). Slot grammar: list=OR, tuple=AND (see STANDARD_DATASET.md). |
| Outputs | `--output` / `-o`, repeated | Exactly `len(outputs)` paths, in order. Passed to the skill as `output` in `**kwargs`. |

### Region

| Concept | Flag | Notes |
| --- | --- | --- |
| Bounding box | `--bbox` | `N/W/S/E` decimal degrees. Declare `@weather_skill.argument("--bbox", ...)`. Auto help + `parse_bbox`. |
| Country code | positional `<CODE>` | `resolve-region` skill (ISO 3166-1 alpha-3). |
| Boundary GeoJSON | `--geojson` / `--mask-geojson` | Skill-specific. |

### Time

| Concept | Flag | Notes |
| --- | --- | --- |
| Date range | `--start-time` / `--end-time` | Absolute `YYYY-MM-DD`; both ends inclusive; when both set, `start_time <= end_time`. |
| Single date | `--date` | Absolute `YYYY-MM-DD`. |

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
