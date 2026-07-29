# CLI flag conventions

Skills declare their CLI through `@weather_skill`. A flag that means the same
thing on different skills must have the same name.

Standard flags come from declaration toggles; everything else is `extra_args`.
The conformance linter checks naming (WSK101 / WSK201 / WSK202).

## Canonical names

### Inputs and outputs

| Concept | Flag | Notes |
| --- | --- | --- |
| Inputs | `--input` / `-i`, repeated | Exactly `len(inputs)` paths, in order. A single `type+` entry means ≥1 paths (variadic). |
| Outputs | `--output` / `-o`, repeated | Exactly `len(outputs)` paths, in order. Passed as `output` (`Path` or `list[Path]`) when the skill accepts it. |

### Region

| Concept | Flag | Notes |
| --- | --- | --- |
| Bounding box | `--bbox` | `N/W/S/E` decimal degrees. From `region="required"` / `"optional"`. |
| Country code | positional `<CODE>` | `resolve-region` skill (ISO 3166-1 alpha-3). |
| Boundary GeoJSON | `--geojson` / `--mask-geojson` | Skill-specific extras. |

### Time

| Concept | Flag | Notes |
| --- | --- | --- |
| Date range | `--start` / `--end` | Absolute `YYYY-MM-DD`; both ends inclusive; `start <= end`. |
| Single date | `--date` | Absolute `YYYY-MM-DD`. |
| Either | `--date` XOR `--start`/`--end` | From `dates="either"`. |

Relative / rolling dates (`now`, `latest`, offsets) are resolved by the caller
before invoking weather skills.

### Variable

| Concept | Flag | Notes |
| --- | --- | --- |
| Variable | `--variable` / `-v` | From `variable=` mode (`single_*` or `multiple_*` with append). |

### Common extras

| Concept | Flag | Notes |
| --- | --- | --- |
| Calendar | `--calendar` | CF calendar name. |
| Calendar align | `--align-on` | `date` \| `year`. |
| Workers | `--workers` | Parallelism (skill-specific extra). |
| Title | `--title` | Plot title (skill-specific extra). |
