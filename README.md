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

Declare what a skill needs with fixed dimension names, or a short type name.

### Dimensions

| Name | Meaning |
| --- | --- |
| `space` | Horizontal grid (`lat` + `lon`) |
| `time` | Valid time |
| `init_time` | Forecast initialization time |
| `prediction_timedelta` | Forecast lead time |
| `member` | Ensemble member |
| `day_of_year` | Day of year |
| `point_id` | Station or point id |
| `x`, `y` | Projected coordinates |

### Types

| Type (primary first) | Required dimensions |
| --- | --- |
| `observations`, `obs`, `analysis`, `retrieval`, `field`, `data` | `space` + `time` |
| `forecast` | `space` + `init_time` + `prediction_timedelta` |
| `ensemble_forecast` | forecast dims + `member` |
| `point_obs`, `station` | `point_id` + `time` |

Also: `any` (any Zarr), `unstructured` (file path), `visualization` (PNG / JPEG / HTML).

One `inputs=` / `outputs=` entry per CLI path. Tuple = all required; list = any
one; trailing `+` = one or more paths.

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
