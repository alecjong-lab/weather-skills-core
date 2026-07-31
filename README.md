# weather-skills-core

Core library for weather skills. The `@weather_skill` decorator owns CLI
construction, input opening, standard-dataset validation, absolute-date parsing,
provenance stamping (`weather_skills_history`), and output writing.

```python
from weather_skills_core import weather_skill

@weather_skill(
    name="my-fancy-skill",
    version="0.1.0",
    inputs=["any"],
    outputs=["any"],
)
@weather_skill.argument("--bbox")
@weather_skill.argument("--start-time", required=True)
@weather_skill.argument("--end-time", required=True)
@weather_skill.argument("--corr-coefficient", type=int)
def my_fancy_skill(ds, bbox, start_time, end_time, corr_coefficient, **kwargs):
    ...
    return result_ds  # or a Path to an already-written file
```

The skill receives opened inputs (or `Path` for `unstructured`, or a `list` for
`type+` variadic inputs) plus resolved kwargs, and **must** accept `**kwargs`.
Return an xarray Dataset (decorator stamps provenance and writes Zarr) or a Path.

Extra CLI flags are stacked as `@weather_skill.argument(...)`, with the same
signature as `argparse.ArgumentParser.add_argument`.

## Inputs and outputs

Zarr slots are declared by **dimensions** or by a **type** that expands to dims.

### Dimensions

| Dimension | Detected on disk as |
| --- | --- |
| `space` | Lat/lon pair (CF / name heuristics) |
| `time` | Time-like dim (CF / `time`) |
| `init_time` | `init_time`, or scalar `time` + step |
| `prediction_timedelta` | `step`, `prediction_timedelta`, or `lead_time` |
| `member` | `number`, `member`, or `realization` |
| `day_of_year` (`doy`) | Day-of-year dim |
| `point_id` | `station_id` / `point_id` with lat/lon on that dim |
| `x`, `y` | Projected axes (declared explicitly) |

### Types

| Type | Implied dimensions | Aliases |
| --- | --- | --- |
| `observations` | `space`, `time` | `analysis`, `retrieval`, `field`, `data` |
| `forecast` | `space`, `init_time`, `prediction_timedelta` | — |
| `ensemble_forecast` | `space`, `init_time`, `prediction_timedelta`, `member` | — |
| `station` | `point_id`, `time` | — |

Also: `any` (no dim constraints), `unstructured` (opaque `Path`),
`visualization` (output-only PNG/JPEG/HTML).

`inputs=` / `outputs=` are lists of slots (one per `--input` / `--output`).
Within a slot: string = type or dim; tuple = AND; list = OR; `…+` = variadic.

Full details:
[`skills/weather-skill-authoring/references/STANDARD_DATASET.md`](skills/weather-skill-authoring/references/STANDARD_DATASET.md).

## Automatic argument processing

Declare flags with `@weather_skill.argument(...)`. For four **canonical dests**,
the decorator also applies help text and post-parse conversion:

| Dest | Typical flags | What the decorator does |
| --- | --- | --- |
| `bbox` | `--bbox` | Appends N/W/S/E help; parses to `(north, west, south, east)` floats. Rewrites `--bbox -10/...` so negative latitudes parse. |
| `date` | `--date` | Appends absolute-date help; parses `YYYY-MM-DD` → `datetime.date`. |
| `start_time` | `--start-time` | Same date parsing (range start, inclusive). |
| `end_time` | `--end-time` | Same date parsing (range end, inclusive). |

If both `start_time` and `end_time` are set, the decorator also requires
`start_time <= end_time`. It does **not** XOR `--date` with the range flags —
skills that need that check do it in-body.

`--input` / `--output` come only from `inputs=` / `outputs=` (do not redeclare
them in `@weather_skill.argument`). When outputs are declared, the skill also
receives `output` in `**kwargs` (a `Path`, or a list of paths for multi-output).

## Install

```
uv add "weather-skills-core @ git+https://github.com/rhiza-research/weather-skills-core"
```

## Development

```
uv sync
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run pre-commit run --all-files
```
