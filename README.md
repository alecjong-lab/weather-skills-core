# weather-skills-core

Core library for weather skills. The `@weather_skill` decorator owns CLI
construction, input opening, envelope validation, absolute-date parsing,
provenance stamping (`weather_skills_history`), and output writing.

```python
from weather_skills_core import weather_skill

@weather_skill(
    "my-fancy-skill",
    "0.1.0",
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
