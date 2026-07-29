# weather-skills-core

Core library for weather skills. The `@weather_skill` decorator owns CLI
construction, input opening, envelope validation, absolute-date parsing,
provenance stamping (`weather_skills_history`), and output writing.

```python
@weather_skill(
    "my-fancy-skill",
    "0.1.0",
    inputs=["forecast", "station"],
    outputs=["forecast"],
    dates="range",
    region="optional",
    variable="single_required",
    extra_args=[
        (("--corr-coefficient",), {"type": int}),
        (("--interpolation-factor",), {"type": int, "choices": [0, 1]}),
    ],
)
def my_fancy_skill(
    forecast_ds, station_ds, start_time, end_time, bbox, variable,
    corr_coefficient, interpolation_factor,
):
    ...
    return result_ds  # or a Path to an already-written file
```

The skill receives opened inputs (or `Path` for `unstructured`) plus resolved
kwargs, and returns an xarray Dataset (decorator stamps provenance and writes
Zarr) or a Path (decorator stamps provenance on the existing file).

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
