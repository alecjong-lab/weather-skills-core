# CLI flag conventions

Skills declare their CLI through `@weather_skill` and stacked
`@weather_skill.argument(...)` decorators. A flag that means the same thing on
different skills must have the same name.

Zarr inputs use `type=Dataset(...)`; write targets use `type=Path` on
`-o/--output`. Path I/O flag names are free-form. Canonical specials (`--bbox`,
dates, `--variable`, …) keep fixed spellings — the conformance linter checks
those (WSK101 / WSK201 / WSK202).

## Standard names

Shared flag names so skills behave the same way. Declare them with
`@weather_skill.argument(...)` and the decorator parses them for you (see the
core README). Types and dimensions live in STANDARD_DATASET.md.

### Inputs and outputs

| Concept | Flag | Notes |
| --- | --- | --- |
| Zarr input | often `--input` / `-i` | `type=Dataset(...)`. Arrives as `ds`. Free-form names allowed. Use `nargs`/`append` for multi-input. |
| Output path | `--output` / `-o`, repeated | Owned by the decorator (`output=True` default). Count must match returned artifacts. |

### Region

Named places are not a decorator flag. The resolve-region skill turns an ISO3
code, a Natural Earth multi-country region (`East Africa`), or a hierarchical
admin key into a printed `N/W/S/E` bbox (and optional GeoJSON). Queries that
are not an ISO3 / NE-region / `country-admin…` key fall through to OSM
Nominatim (`limit=1`) for landmarks. Consumer skills take `--bbox` (and
skill-specific `--geojson` / `--mask-geojson`).

| Concept | Flag | Notes |
| --- | --- | --- |
| Bounding box | `--bbox` | CLI is `N/W/S/E` decimal degrees. Skill receives `(N, W, S, E)` floats — do not re-parse. |
| Boundary GeoJSON | `--geojson` / `--mask-geojson` | Skill-specific. |

### Time

| Concept | Flag | Notes |
| --- | --- | --- |
| Date range | `--start-time` / `--end-time` | CLI is absolute `YYYY-MM-DD` (inclusive). Skill receives `datetime.date`; when both set, `start_time <= end_time`. Do not re-parse. |
| Single date | `--date` | CLI is absolute `YYYY-MM-DD`. Skill receives `datetime.date`. |

Relative / rolling dates (`now`, `latest`, offsets, "the last two weeks") are
not decorator flags. The resolve-time skill turns a query token into printed
`--start-time`/`--end-time` or `--date` from the current UTC date (or
`--as-of`). It is calendar math only — it does not probe a product or clip
to what is on disk. Mutual exclusion of `--date` vs the range flags is
skill-owned when needed.

Latest-available dates are not in YAML. Every fetcher implements
`--probe-latest` (see `weather_skills_core.probe.PROBE_LATEST_KWARGS`).
Stdout is one line `YYYY-MM-DD` or `none` (no realtime cap, e.g. CMIP6). Do
not GET full fields — HEAD, directory listing, time coordinate, or a tiny
catalog query. Optional IDENT selects a product (`--probe-latest final`,
`--probe-latest noaa-gfs-forecast`). Agents call the fetcher directly; to
end a rolling window on that day, pass it as resolve-time `--as-of`.

```yaml
metadata:
  catalog-group: fetchers
  variables:                 # exact --variable / -v names, most-used first
    - precip
```

`variables` is a non-empty list of exact `--variable` / `-v` tokens for that
fetcher, **most-used first**. Names are catalog-specific
(`precipitation_surface` on dynamical, `total_precipitation` on ARCO, `tp`
on ECMWF S2S). Open catalogs (ARCO, CMIP6, dynamical) and closed ones with
many fields (ECMWF S2S) list the usual first choices, not every field the
source can serve.

### Variable

| Concept | Flag | Notes |
| --- | --- | --- |
| Variable | `--variable` / `-v` | Declare with `@weather_skill.argument("--variable", "-v", ...)` (`action="append"` when multi). Fetcher `-v` names live on SKILL.md `metadata.variables`. |

### Common extras

| Concept | Flag | Notes |
| --- | --- | --- |
| Calendar | `--calendar` | CF calendar name. |
| Calendar align | `--align-on` | `date` \| `year`. |
| Workers | `--workers` | Parallelism (skill-specific). |
| Title | `--title` | Plot title (skill-specific). |
| Latest available | `--probe-latest` | Fetchers only. Print `YYYY-MM-DD` or `none` on stdout and exit. Optional ident. |
