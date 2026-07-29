# Weather-Skills Envelope

The common Zarr-based container that skills consume and produce.

## Shape

A Zarr store containing one or more data variables. Consumers also read Zarr v2
(xarray detects the format on open).

### Data envelope
- Spatial dims: `latitude`, `longitude` (aliases `lat`/`lon`, `y`/`x` accepted on input).
- Temporal: a `time` dim.

### Forecast envelope
- `step` dim (lead time, `timedelta64`) plus a scalar `time` coord for the init date.
- Optional `number` — ensemble member index.

### Station envelope
- Spatial dim `station_id` (string).
- 1-D coords `latitude(station_id)` and `longitude(station_id)`.
- `time` dim.

Other I/O kinds (not Zarr envelope shapes):

- `unstructured` — opaque file path; no shape validation.
- `visualization` — PNG / JPEG / HTML written by the skill; provenance stamped into file metadata.

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

## Conventions

- CF-compliant Zarr; use cf-xarray for coord identification.
- Write with `consolidated=True`.
- Missing data is NaN.
- Per-variable `encoding` is not part of the contract; clear `.encoding` before `to_zarr`.
