"""Resolve named regions (CLI string → bbox + GeoDataFrame)."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files

import geopandas as gpd

from weather_skills_core.errors import DataError, UsageError


@lru_cache(maxsize=1)
def _countries() -> dict:
    path = files("weather_skills_core.data").joinpath("countries.geojson")
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_region(query: str):
    """Resolve a country name or ISO3 code to ``((N, W, S, E), GeoDataFrame)``."""
    text = query.strip()
    if not text:
        raise UsageError("--region must be a non-empty country name or ISO3 code.")

    features = _countries()["features"]
    upper = text.upper()
    match = None
    if len(upper) == 3 and upper.isalpha():
        match = next((f for f in features if f["properties"]["iso3"] == upper), None)
    if match is None:
        needle = text.casefold()
        named = [f for f in features if str(f["properties"].get("name", "")).casefold() == needle]
        if len(named) > 1:
            codes = ", ".join(sorted(f["properties"]["iso3"] for f in named))
            raise DataError(
                f"--region {query!r} matches multiple countries ({codes}); use an ISO3 code."
            )
        match = named[0] if named else None
    if match is None:
        raise DataError(
            f"--region {query!r} is not a known ISO3 code or country name in the bundled "
            "Natural Earth 1:110m admin-0 dataset (177 countries)."
        )

    gdf = gpd.GeoDataFrame.from_features(
        {"type": "FeatureCollection", "features": [match]},
        crs="EPSG:4326",
    )
    minx, miny, maxx, maxy = gdf.total_bounds
    return (float(maxy), float(minx), float(miny), float(maxx)), gdf
