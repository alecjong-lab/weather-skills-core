# Standard dataset

A weather-skills Zarr is a [CF-compliant](https://cfconventions.org/)
store with a small, shared dimension vocabulary. Skill A can feed skill
B because both agree what `forecast` and `lat` mean.

Declare what an input must have with `type=Dataset(...)`. The decorator
opens the path, checks those dimensions, attaches pint units, and
injects the opened `xarray.Dataset` as `ds`.

Units policy: [UNITS.md](UNITS.md). Flag names: [CONVENTIONS.md](CONVENTIONS.md).

## Dims are the vocabulary, types are nicknames

A **dimension** is one axis (`lat`, `time`, `member`). A **type** is a
named bundle of dimensions. These two declarations are the same check:

```python
Dataset("forecast")
Dataset("lat, lon, init_time, prediction_timedelta")
```

Prefer the type when the cube is one of the usual shapes. Spell dims
when you need a custom AND (`lat` + `member` but no time).

## Dimensions

| Name | Meaning |
| --- | --- |
| `lat` | Latitude (regular grid) |
| `lon` | Longitude (regular grid) |
| `time` | Valid time |
| `init_time` | Forecast initialization time |
| `prediction_timedelta` | Forecast lead time |
| `member` | Ensemble member |
| `vertical` | Vertical level (pressure, height, …) |
| `day_of_year` | Day of year |
| `point_id` | Station or point id |
| `x`, `y` | Irregular / projected grid axes |

Declare the **ontology** name. Incoming files often use a CF or GRIB
synonym; those still count (see [Names on disk](#names-on-disk)).

## Types

| Type (and aliases) | Required dimensions |
| --- | --- |
| `spatial`, `space` | `lat` + `lon` |
| `observations`, `obs`, `analysis`, `retrieval`, `field`, `data` | `lat` + `lon` + `time` |
| `forecast` | `lat` + `lon` + `init_time` + `prediction_timedelta` |
| `vertical_forecast` | forecast + `vertical` |
| `ensemble_forecast` | forecast + `member` |
| `point_obs`, `station` | `point_id` + `time` |
| `any` | any Zarr (skip the dim check) |

`any` still opens a Zarr. Opaque files and figures use `pathlib.Path`,
not `Dataset`.

## Names on disk

| Ontology | Also accepted |
| --- | --- |
| `lat` | `latitude` |
| `lon` | `longitude` |
| `prediction_timedelta` | `step`, `lead_time` |
| `member` | `number`, `realization` |
| `vertical` | `level`, `pressure`, `height`, `altitude`, `lev`, `isobaricInhPa` |
| `point_id` | `station_id` |
| `day_of_year` | `doy` |

Declare the ontology name, not every synonym. A file whose dim is
called `step` still satisfies `Dataset("forecast")`.

## Declaring `Dataset(...)`

| Form | Meaning | Example |
| --- | --- | --- |
| String type or dim | That type, or that one dim | `Dataset("spatial")`, `Dataset("time")` |
| Comma string | All of these (AND) | `Dataset("lat, lon")` |
| Tuple | All of these (AND) | `Dataset(("lat", "lon", "member"))` |
| List | Any one of these (OR) | `Dataset(["forecast", "ensemble_forecast"])` |
| `"any"` | Skip the dim check | `Dataset("any")` |

```python
from weather_skills_core import Dataset, weather_skill


@weather_skill(name="clip-region", version=_SKILL_VERSION)
@weather_skill.argument("-i", "--input", type=Dataset("spatial"), required=True)
def clip_region(ds, output, **kwargs):
    return ds
```

Several Zarrs: `action="append"` and repeat `-i` once per store.
`--input` still arrives as `ds`, now a list. Separate Dataset flags when
the roles differ (`--forecast` vs `--obs`).

The decorator owns `-o/--output`. There is no output dim check — the
returned cube is whatever the skill produced. Return count must match
the number of `--output` paths.

## Provenance

The decorator records provenance for you. The only thing a skill sets is
`weather_skills_source`, and only fetchers set it:

```python
ds.attrs["weather_skills_source"] = "chirps"  # or "dynamical:<catalog-id>"
```

Plots turn this token into the short product name, so pick one that
title-cases cleanly.

Everything else is in `weather_skills_history`, a JSON list of entries.
Each entry is one skill run:

| Key | Meaning |
| --- | --- |
| `skill`, `version` | Which skill ran |
| `args` | Its CLI arguments, minus input and output paths |
| `input` | The Zarr(s) it read: `{basename, hash}`, a list of those, or `null` for a fetcher |
| `commit`, `repo`, `dirty` | Git identity of the skill code, when known |

Chaining works like this:

- **One input:** the new history is the input's history plus one entry,
  oldest first. A fetch → clip → aggregate pipeline is a flat list of
  three entries.
- **Several inputs** (e.g. `difference A B`): the history is a single
  entry, and each item in its `input` list carries that parent's whole
  history under `history`. Read it as a tree, not a list.

Figures (PNG, JPEG, HTML) store the same JSON in file metadata. A PNG
or JPEG with a valid history also gets a small star stamp in the corner.

## Writing Zarr

- CF-compliant, `consolidated=True`
- Missing values are NaN
- Clear `.encoding` before `to_zarr`

The decorator stamps history and writes when you return an
`xr.Dataset`. Fetchers should also stamp native cell geometry
(`data_interval` or CF `{dim}_bounds`) — see [UNITS.md](UNITS.md).
