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

For accumulated variables like precip, skills work in **rates** by default;
period **totals** are available when you want them via the conversion helpers
(`convert-to-totals` / `rate_to_total`) rather than carrying amounts through
rate-oriented skills.

## What the decorator does

1. After opening a Zarr, **`quantify_dataset`** attaches pint units from each
   data variable’s `units` attr (when present).
2. **Known standard kinds** with ``units_required`` in ``STANDARD`` (today:
   temp, precip) must have parseable units so skills can treat them
   explicitly. Other variables may include units optionally.
3. By default, skills expect rates: opening a precip **total** (amount units,
   or `cell_methods` with `sum`) raises unless the skill opts in with
   `allow_precip_totals=True` (e.g. plotters / `deaccumulate`).
4. Before writing Zarr, **`dequantify_dataset`** strips pint so stored attrs
   stay plain unit strings. The write path also normalizes GRIB unit strings,
   stamps precip-amount CF names when units are amounts, casts `step` to
   `timedelta64[ns]`, and fills data-var attrs stripped by the skill from the
   first input (same variable names). Value conversion (`to_standard_units`)
   stays skill-owned.

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

Temporal aggregation stamps two complementary data-variable attrs:

- `aggregation_period` — **how much time each value represents** (the resampling
  step), as a pint duration string: `1 day`, `7 day`, `1 dekad`, `1 month`, ….
  Parse it with `parse_aggregation_period`.
- CF `cell_methods` — **how values were combined** over that window
  (e.g. `time: mean (interval: 1 day)`, `time: sum`).

The two are orthogonal: `cell_methods` names the operation, `aggregation_period`
names the window length. A daily-mean variable carries both `time: mean` and
`aggregation_period = "1 day"`.

CLI period labels map to the `aggregation_period` value (`daily` → `1 day`,
`weekly` → `7 day`, `dekadal` → `1 dekad`, `monthly` → `1 month`).

**`convert-to-totals`** multiplies rates by `aggregation_period` → amounts
(`mm`). When the time/step axis has ≥ 2 points, it requires sample spacing ≥
`aggregation_period` (so overlapping rolling windows are not silently turned
into period totals). A singleton axis (one aggregated bin) skips that check.
If the series is still native resolution (e.g. half-hourly IMERG vs a 21-day
period), run `aggregate-temporal --period '21 day'` first. Use that when you
want amounts for plotting or reporting; keep rates in the
middle of the pipeline.

## Helpers skills use

| Function | Role |
| --- | --- |
| `units_equal` | Spelling-independent equality (`mm/day` ≈ `mm day-1`) |
| `convert_dataarray` / `convert_values` | Explicit unit ↔ unit |
| `to_standard_units` | Temp / precip → standard display units |
| `rate_to_total` | Rate × period → amount |
| `parse_aggregation_period` | Parse an `aggregation_period` string |

## Author checklist

- Known standard kinds (temp, precip, …) need a udunits-parseable `units`
  attr; other variables may include units optionally.
- Keep accumulated variables as rates (`mm day-1`) through most skills; convert
  to totals when you need amounts.
- After `aggregate-temporal`, expect `aggregation_period` + `cell_methods`.
- For full dim/type contract, see
  [`STANDARD_DATASET.md`](STANDARD_DATASET.md).
