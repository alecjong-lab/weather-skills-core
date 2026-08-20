"""``Dataset`` — argparse type marker for weather-skills Zarr inputs."""

from __future__ import annotations

from pathlib import Path

from weather_skills_core import standard_dataset as std


class Dataset:
    """Declare a Zarr input with required dims / types.

    Use as ``type=`` on ``@weather_skill.argument``::

        @weather_skill.argument("-i", "--input", type=Dataset("forecast"), required=True)
        @weather_skill.argument("-i", "--input", type=Dataset("lat, lon"), required=True)
        @weather_skill.argument("--ds", type=Dataset(["spatial", "point_obs"]), required=True)

    Grammar:
        ``Dataset("forecast")`` — named type (see ``TYPE_DIMS``)
        ``Dataset("lat, lon")`` — AND of ontology dims (comma-separated string)
        ``Dataset(("lat", "lon"))`` — same AND as a tuple
        ``Dataset(["forecast", "ensemble_forecast"])`` — OR of alternatives
        ``Dataset("any")`` — any Zarr; skip dimension checks

    Opaque files use ``pathlib.Path``, not ``Dataset``.
    """

    def __init__(self, spec: str | list | tuple):
        if isinstance(spec, str) and spec != std.ANY and "," in spec:
            parts = tuple(p.strip() for p in spec.split(",") if p.strip())
            if not parts:
                raise ValueError(f"empty Dataset dim list in {spec!r}")
            body: str | list | tuple = parts
        elif isinstance(spec, (str, list, tuple)):
            body = spec
        else:
            raise TypeError(f"Dataset() expects str, list, or tuple; got {type(spec).__name__}")
        try:
            self.io_spec = std.normalize_io_spec(body)
        except ValueError as exc:
            raise ValueError(f"invalid Dataset({spec!r}): {exc}") from exc
        self.raw = spec

    def __call__(self, value: str | Path) -> Path:
        """Argparse converter: CLI string → ``Path`` (opening happens in the decorator)."""
        return Path(value)

    def __repr__(self) -> str:
        return f"Dataset({self.raw!r})"

    def help_label(self) -> str:
        """Short dim/type label for CLI help."""
        spec = self.io_spec
        if spec.alternatives is None:
            return "any"
        if len(spec.alternatives) == 1:
            dims = sorted(spec.alternatives[0])
            return "+".join(dims) if dims else "any"
        parts = ["{" + ",".join(sorted(alt)) + "}" for alt in spec.alternatives]
        return " OR ".join(parts)
