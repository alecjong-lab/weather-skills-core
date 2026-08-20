# Units

Weather-skills track physical units on every data variable so skills can
combine, convert, and compare datasets safely instead of guessing from a
variable's name. Units live in the CF `units` attr and are carried through the
pipeline as real quantities, not bare numbers. Implementation:
[`weather_skills_core.units`](../../../src/weather_skills_core/units.py).

Built on **pint** / **pint-xarray** with CF/UDUNITS strings (via
`cf_xarray.units`). Custom registry extras: `pentad` (5 day) and `dekad`
(10 day); week and month come from pint itself.

## Standard kinds and units

A few known variable kinds have a standard unit skills expect:

| Kind | Standard units | Notes |
| --- | --- | --- |
| temp | `degree_Celsius` | Existing CF `standard_name` kept |
| precip | `mm day-1` | `lwe_precipitation_rate` (rate / flux) |
| precip amount | `mm` | From rate × period via totals utilities |

Mass precip flux (`kg m-2 s-1`) converts to depth rate using liquid-water
density (1000 kg m⁻³).

For accumulated variables like precip, fetch writes **rates**; period
**totals** come from `convert-to-totals` / `rate_to_total` (rate × period).
Skills otherwise open rates and amounts alike. `rate_to_total` is the
operation that refuses precip amounts — multiplying an amount by a period
would double-count.

## What the decorator does

1. After opening a Zarr, **`quantify_dataset`** attaches pint units from each
   data variable’s `units` attr (when present).
2. **Known standard kinds** with ``units_required`` in ``STANDARD`` (today:
   temp, precip) must have parseable units so skills can treat them
   explicitly. Other variables may include units optionally.
3. Skills open precip rates and amounts alike. ``rate_to_total`` (used by
   `convert-to-totals`) refuses precip **totals** (amount units, or
   `cell_methods` with `sum`) because multiplying an amount by a period would
   double-count.
4. Before writing Zarr, **`dequantify_dataset`** strips pint so stored attrs
   stay plain unit strings. The write path also normalizes GRIB unit strings,
   stamps precip-amount CF names when units are amounts (including overwriting
   rate-like `long_name` / `GRIB_name`), casts `step` to `timedelta64[ns]`, and
   fills data-var attrs stripped by the skill from the first input (same
   variable names). Value conversion (`to_standard_units`) stays skill-owned.

## Classification and `--to-standard`

`classify_variable` picks a kind in this order:

1. CF `standard_name`
2. Named variable hints (`t2m`, `tp`, `precip`, …); for precip hints, amount
   vs rate is taken from `units` when that fingerprint is clear
   (`kg m-2` / `mm` → amount, `kg m-2 s-1` / `mm day-1` → rate). If units are
   present but not convertible to a precip rate or amount, the name hint is
   ignored (so `precipitation_quality_index_surface` with `units="1"` is not
   treated as precip).

Units alone do **not** classify a variable (a bare `kg m-2 s-1` field is not
treated as precip). `to_standard_units` converts recognized temp / precip vars
to the table above (rates → `mm day-1`, amounts → `mm`), stamps the kind's CF
`standard_name` when set, and leaves the variable **name** unchanged.

## Aggregation and totals

Fetch writes native-resolution rates and stamps **native cell geometry**: a
scalar **`data_interval`** when spacing is uniform (`30 minute`, `1 day`), or
CF **`{dim}_bounds`** (start and end per sample) when it is not. Those two
are XOR — never both. Aggregation then adds `aggregation_period` and
`aggregation_coverage`; convert-to-totals reads those and does not guess.

| Name | On | When | Meaning |
| --- | --- | --- | --- |
| `data_interval` | data variable (pint string) | fetch; kept through aggregate | Uniform native sample spacing |
| `{dim}_bounds` | coordinate `(N, 2)` start/end | fetch / deaccumulate; irregular only | CF cell bounds; native geometry when spacing is not one width |
| `aggregation_period` | data variable (pint string) | **aggregate-temporal only** | Length of each aggregated interval (`7 day`, `21 day`) |
| `aggregation_coverage` | time/step coordinate, 0–1 | **aggregate-temporal only** | Completeness of that interval vs native cells |

`data_interval` is not the aggregation window. Convert-to-totals multiplies
rates by `aggregation_period`, not by `data_interval` or bound widths. CF
`bounds` on the axis are the irregular form of native cell geometry; they
are not a per-sample `aggregation_period`.

CF `cell_methods` is orthogonal: it names the operation (`time: mean`,
`time: sum`). A weekly-mean rate carries `time: mean` and `aggregation_period =
"7 day"`, plus the original `data_interval` when the native axis was uniform.

CLI period labels map to the `aggregation_period` value (`daily` → `1 day`,
`weekly` → `7 day`, `dekadal` → `1 dekad`, `monthly` → `1 month`). Custom pint
durations (`21 day`) are also valid `--period` values.

**`convert-to-totals`** multiplies rates by `aggregation_period` → amounts
(`mm`). It also rewrites leftover rate display names (`long_name` /
`GRIB_name` containing `rate` or `flux`) to `Total precipitation`. It requires:

- a stamped `aggregation_period` (run `aggregate-temporal` first)
- coverage at or above `--min-coverage` (default 1.0; incomplete bins fail
  unless you pass a lower threshold)
- non-overlapping intervals (sample spacing ≥ `aggregation_period`)
- rate inputs — precip totals (amount units or `cell_methods` with `sum`)
  are refused, because multiplying an amount by the period would double-count

Overlapping series (rolling `--window`, or 21-day bins whose labels are 10
days apart) are refused — run **`select`** on `time` or `step` to keep a
non-overlapping subset, then convert-to-totals. A singleton axis (one
aggregated bin) skips the overlap check. Native-only cubes have
`data_interval` and no `aggregation_period`; convert-to-totals will not
invent a period from the native spacing.

## Helpers skills use

| Function | Role |
| --- | --- |
| `units_equal` | Spelling-independent equality (`mm/day` ≈ `mm day-1`) |
| `convert_dataarray` / `convert_values` | Explicit unit ↔ unit |
| `to_standard_units` | Temp / precip → standard display units |
| `stamp_data_interval` | Stamp uniform `data_interval` or CF `{dim}_bounds` on fetch / deaccumulate |
| `precip_amounts_to_rates` | Amount → `mm day-1` (deaccumulate amount vars on `step`, else ÷ interval) |
| `stamp_precip_amounts` | Amount units → amount CF `standard_name`; rewrite rate `long_name` / `GRIB_name` |
| `rate_to_total` | Rate × period → amount (refuses precip totals) |
| `parse_aggregation_period` | Parse an `aggregation_period` / duration string |
| `filter_min_coverage` | Drop aggregated intervals below a coverage threshold |

## Author checklist

- Known standard kinds (temp, precip, …) need a udunits-parseable `units`
  attr; other variables may include units optionally.
- Fetch writes accumulated variables as rates (`mm day-1`). Convert to totals
  with `convert-to-totals` when you need amounts; that step (and
  `rate_to_total`) refuse inputs that are already amounts.
- After fetch, expect `data_interval` (uniform) or CF `{dim}_bounds`
  (irregular), and no `aggregation_period`.
- After `aggregate-temporal`, expect `aggregation_period` + `aggregation_coverage` + `cell_methods`.
- For full dim/type contract, see
  [`STANDARD_DATASET.md`](STANDARD_DATASET.md).
