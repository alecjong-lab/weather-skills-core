"""Helpers skills call: dates, bbox, lon wrap, GeoJSON, env, transient errors."""

from __future__ import annotations

import os
import re
from datetime import date

from weather_skills_core.errors import DataError, UsageError
from weather_skills_core.standard_dataset import detect_spatial_dims

_ABS_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Retryable HTTP status codes as whole tokens (avoid matching "14290", "50000").
_STATUS_RE = re.compile(r"\b(?:429|500|502|503|504)\b")
_TIMEOUT_MARKERS = ("timed out", "timeout")
# Specific phrases only — bare "connection" appears on permanent urllib3 errors too.
_CONNECTION_MARKERS = (
    "connection error",
    "connection reset",
    "connection refused",
    "connection aborted",
)


def is_transient(exc: Exception) -> bool:
    """Heuristic: does this error look like a retryable transient/rate-limit?

    Matches error text for HTTP 429/5xx status tokens, timeout markers, or
    specific connection-failure phrases. Case-insensitive. Retry policy stays
    with the caller.
    """
    text = str(exc).lower()
    if _STATUS_RE.search(text):
        return True
    if any(marker in text for marker in _TIMEOUT_MARKERS):
        return True
    return any(marker in text for marker in _CONNECTION_MARKERS)


def require_env(*names: str, message: str | None = None) -> tuple:
    """Return the values of the named environment variables, in order.

    Unset or empty vars are missing; raises ``UsageError`` with ``message`` or a
    default listing the missing names. Never print or log the values.
    """
    values = [os.environ.get(name) for name in names]
    missing = [name for name, value in zip(names, values, strict=True) if not value]
    if missing:
        raise UsageError(message or f"missing required env var(s): {', '.join(missing)}")
    return tuple(values)


def np_to_date(value) -> date:
    """Convert a numpy datetime64 to a calendar date (truncating time-of-day)."""
    import numpy as np

    if np.isnat(value):
        raise DataError(
            "time coordinate value is NaT (not-a-time); the dataset has a missing or "
            "unfilled time entry where a valid date is required."
        )
    return date.fromisoformat(np.datetime_as_string(value, unit="D"))


def parse_date(value: str) -> date:
    """Parse an absolute ``YYYY-MM-DD`` date string."""
    if not _ABS_DATE_RE.match(value):
        raise UsageError(f"invalid date value {value!r}: expected an absolute date YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise UsageError(
            f"invalid date value {value!r}: expected an absolute date YYYY-MM-DD"
        ) from None


def parse_range(start_value: str, end_value: str) -> tuple[date, date]:
    """Parse ``--start``/``--end`` as absolute dates and require ``start <= end``."""
    start = parse_date(start_value)
    end = parse_date(end_value)
    if start > end:
        raise UsageError(
            f"resolved --start {start.isoformat()} is after resolved "
            f"--end {end.isoformat()}; the range is reversed."
        )
    return start, end


def parse_bbox(bbox: str) -> tuple:
    """Parse ``N/W/S/E`` decimal degrees into ``(N, W, S, E)`` floats."""
    try:
        north, west, south, east = (float(x) for x in bbox.split("/"))
    except ValueError:
        raise UsageError("--bbox must be four decimal degrees N/W/S/E.") from None
    return north, west, south, east


def lat_slice(lat_vals, north, south) -> slice:
    """Build a latitude ``sel`` slice for ascending or descending axes."""
    if lat_vals.size and lat_vals[0] > lat_vals[-1]:
        return slice(north, south)
    return slice(south, north)


def polygon_from_geojson(path, *, flag: str = "--mask-geojson"):
    """Load GeoJSON and return one shapely geometry (union of features).

    ``flag`` is only for error messages (which CLI flag supplied the path).
    """
    import json
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        raise UsageError(f"{flag} file not found: {path}")
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise UsageError(f"could not read {flag} {path}: {exc}") from None

    if not isinstance(data, dict):
        # Valid JSON, but a top-level array or scalar is no GeoJSON object.
        raise UsageError(f"{flag} {path} has no usable geometry.")
    if data.get("type") == "FeatureCollection":
        features = data.get("features", [])
        if not isinstance(features, list):
            raise UsageError(f"{flag} {path}: 'features' is not a list.")
        geoms = []
        for feature in features:
            if not isinstance(feature, dict):
                raise UsageError(f"{flag} {path}: a feature is not a JSON object.")
            if feature.get("geometry"):
                geoms.append(feature["geometry"])
    elif data.get("type") == "Feature":
        geoms = [data["geometry"]] if data.get("geometry") else []
    else:
        # A bare geometry object.
        geoms = [data]

    if not geoms:
        raise UsageError(f"{flag} {path} has no usable geometry.")

    from shapely.errors import GeometryTypeError
    from shapely.geometry import shape
    from shapely.ops import unary_union

    # shape()/unary_union() raise on a well-formed JSON value that is not a
    # valid geometry (an unknown type, missing/malformed coordinates, a
    # non-object entry); convert every such failure to a flag-named UsageError
    # so no JSON input produces a raw traceback.
    try:
        return unary_union([shape(g) for g in geoms])
    except (GeometryTypeError, KeyError, AttributeError, ValueError, TypeError) as exc:
        raise UsageError(f"{flag} {path} has no usable geometry ({exc}).") from None


def normalize_longitude(ds, lon_dim: str = "longitude"):
    """Map a 0..360 longitude axis to [-180, 180) and sort ascending.

    Keeps coord attrs; drops duplicate labels produced by wrapping 0 and 360.
    """
    import numpy as np

    attrs = dict(ds[lon_dim].attrs)
    lon = ((ds[lon_dim] + 180) % 360) - 180
    lon.attrs = attrs
    ds = ds.assign_coords({lon_dim: lon})
    # np.unique returns each unique value's first-occurrence index; keeping
    # those (in input order) drops any later duplicate the wrap produced.
    _, first = np.unique(ds[lon_dim].values, return_index=True)
    if len(first) < ds.sizes[lon_dim]:
        ds = ds.isel({lon_dim: np.sort(first)})
    return ds.sortby(lon_dim)


def bbox_subset(ds, bbox, *, lat_dim: str | None = None, lon_dim: str | None = None):
    """Subset a gridded dataset to an ``N/W/S/E`` bbox (string or tuple).

    Wraps 0..360 lon, supports antimeridian (west > east), auto-detects dims
    unless given. Empty selection raises DataError.
    """
    import numpy as np

    if isinstance(bbox, str):
        north, west, south, east = parse_bbox(bbox)
    else:
        north, west, south, east = bbox
    if lat_dim is None or lon_dim is None:
        lat_dim, lon_dim = detect_spatial_dims(ds)

    # Wrap lon to [-180, 180] before the slice so a 0..360 input grid still
    # intersects bboxes that use negative west/east values.
    lon_vals = np.asarray(ds[lon_dim].values)
    if lon_vals.size and float(np.nanmax(lon_vals)) > 180.0:
        ds = ds.assign_coords({lon_dim: ((ds[lon_dim] + 180) % 360 - 180)}).sortby(lon_dim)
        lon_vals = np.asarray(ds[lon_dim].values)
    if lon_vals.size == 0:
        raise UsageError("lon axis has length 0; cannot subset.")
    if lon_vals.size == 1:
        lon_ascending = True
    else:
        lon_diffs = np.diff(lon_vals)
        if (lon_diffs > 0).all():
            lon_ascending = True
        elif (lon_diffs < 0).all():
            lon_ascending = False
        else:
            raise UsageError(
                "lon axis is non-monotonic; cannot infer slice orientation. "
                "Re-sort the input or pre-process before subsetting."
            )

    lat_vals = np.asarray(ds[lat_dim].values)
    if lat_vals.size == 0:
        raise UsageError("lat axis has length 0; cannot subset.")
    if lat_vals.size == 1:
        lat_sel = None
    else:
        diffs = np.diff(lat_vals)
        if (diffs > 0).all():
            lat_sel = slice(south, north)
        elif (diffs < 0).all():
            lat_sel = slice(north, south)
        else:
            raise UsageError(
                "lat axis is non-monotonic; cannot infer slice orientation. "
                "Re-sort the input or pre-process before subsetting."
            )
    if lat_sel is not None:
        ds = ds.sel({lat_dim: lat_sel})

    if west <= east:
        # Contiguous longitude span. Slice in the axis's own monotonic order.
        lon_sel = slice(west, east) if lon_ascending else slice(east, west)
        ds = ds.sel({lon_dim: lon_sel})
    else:
        # Antimeridian crossing (west > east): the span runs west .. +180 and
        # -180 .. east. Select each wing with a label slice and concatenate
        # in the axis's native order; unlike a where(..., drop=True) mask
        # this never materializes a full-grid mask and keeps integer
        # variables integer (masking promotes them to float).
        import xarray as xr

        if lon_ascending:
            wings = [ds.sel({lon_dim: slice(None, east)}), ds.sel({lon_dim: slice(west, None)})]
        else:
            wings = [ds.sel({lon_dim: slice(None, west)}), ds.sel({lon_dim: slice(east, None)})]
        ds = xr.concat(
            wings,
            dim=lon_dim,
            data_vars="minimal",
            coords="minimal",
            compat="override",
            join="exact",
        )

    if ds.sizes.get(lat_dim, 0) == 0 or ds.sizes.get(lon_dim, 0) == 0:
        bbox_str = f"{north}/{west}/{south}/{east}"
        if west > east:
            raise DataError(
                f"--bbox {bbox_str} crosses the antimeridian (west {west} > east {east}) "
                "but selects no grid cells; check the N/S extent and that west/east "
                "bracket the intended dateline-crossing span."
            )
        raise DataError(
            f"--bbox {bbox_str} selects no grid cells; check the extent and N/W/S/E order."
        )
    return ds


def grid_spacing(coord_vals) -> float:
    """Median absolute spacing of a 1-D coordinate (degrees or similar)."""
    import numpy as np

    coord = np.asarray(coord_vals)
    if coord.size < 2:
        raise ValueError(f"Cannot infer spacing for coord with size {coord.size}")
    return float(abs(np.median(np.diff(coord))))


def pick_time_dim(obj, override=None) -> str:
    """Resolve a time-like dim: override, then ``time``, ``step``, then CF time."""
    from weather_skills_core.cf import cf_dim
    from weather_skills_core.errors import UsageError

    dims = list(obj.dims)
    if override:
        if override not in obj.dims:
            raise UsageError(f"--time-dim {override!r} not in dims {dims}")
        return override
    if "time" in obj.dims:
        return "time"
    if "step" in obj.dims:
        return "step"
    cf = cf_dim(obj, "time")
    if cf and cf in obj.dims:
        return cf
    raise UsageError(f"no time-like dim in {dims}; pass --time-dim")


def dataset_label(ds, fallback) -> str:
    """Short label from ``weather_skills_source``, else ``fallback`` (str or callable)."""
    from pathlib import Path

    src = ds.attrs.get("weather_skills_source")
    if isinstance(src, str) and src.strip():
        return Path(src).stem
    return fallback() if callable(fallback) else str(fallback)


def apply_write_encoding(ds, *, time_units=None, time_calendar=None, fills=None):
    """Set time encoding and optional per-variable ``_FillValue`` encodings in place."""
    if time_units is not None and "time" in ds.coords:
        ds["time"].encoding["units"] = time_units
    if time_calendar is not None and "time" in ds.coords:
        ds["time"].encoding["calendar"] = time_calendar
    if fills:
        for var, fill in fills.items():
            if fill is not None and var in ds.variables:
                ds[var].encoding["_FillValue"] = fill
    return ds


def verify_cf_decode(ds, axes: tuple = ("X", "Y", "T")):
    """Raise DataError if cf-xarray cannot resolve the given axes."""
    from weather_skills_core.cf import cf_axes_missing

    missing = cf_axes_missing(ds, axes=axes)
    if missing:
        raise DataError(
            f"cf-xarray did not resolve axes {missing} "
            f"(expected {list(axes)}); the output is not CF-compliant."
        )


def latitude_weights(lats):
    """Cosine latitude weights normalized to mean 1."""
    import numpy as np
    import xarray as xr

    if not isinstance(lats, xr.DataArray):
        lats = xr.DataArray(lats)
    weights = np.cos(np.deg2rad(lats))
    return weights / weights.mean()
