"""Shared CLI flags (bbox, dates, …) and spatial helpers skills use with them."""

from __future__ import annotations

from dataclasses import dataclass

from weather_skills_core import dates as dates_mod
from weather_skills_core.errors import DataError, UsageError
from weather_skills_core.standard_dataset import detect_spatial_dims

DATE_HELP = "Absolute date YYYY-MM-DD."
BBOX_HELP = "N/W/S/E decimal degrees (use the resolve-region skill to get a country's bbox)."
START_TIME_HELP = "Range start, inclusive. Absolute YYYY-MM-DD."
END_TIME_HELP = "Range end, inclusive. Absolute YYYY-MM-DD."

STANDARD_HELP = {
    "bbox": BBOX_HELP,
    "date": DATE_HELP,
    "start_time": START_TIME_HELP,
    "end_time": END_TIME_HELP,
}
STANDARD_DESTS = frozenset(STANDARD_HELP)


@dataclass(frozen=True)
class StandardParameter:
    name: str
    dest: str
    flags: tuple
    kind: str  # "io" | "canonical"
    accepts_help: bool = False


def standard_parameters():
    """Shared flags (``--input``, ``--bbox``, …) used by the decorator (and the linting package)."""
    return (
        StandardParameter("inputs", "input", ("--input", "-i"), "io"),
        StandardParameter("outputs", "output", ("--output", "-o"), "io"),
        StandardParameter("start_time", "start_time", ("--start-time",), "canonical", True),
        StandardParameter("end_time", "end_time", ("--end-time",), "canonical", True),
        StandardParameter("date", "date", ("--date",), "canonical", True),
        StandardParameter("bbox", "bbox", ("--bbox",), "canonical", True),
        StandardParameter("variable", "variable", ("--variable", "-v"), "canonical", True),
    )


def rewrite_bbox_argv(argv):
    """Turn ``--bbox N/W/S/E`` into ``--bbox=…`` so a negative north is not a flag."""
    out = []
    i = 0
    while i < len(argv):
        if argv[i] == "--bbox" and i + 1 < len(argv):
            out.append(f"--bbox={argv[i + 1]}")
            i += 2
            continue
        out.append(argv[i])
        i += 1
    return out


def add_standard_help(kwargs: dict, canonical: str) -> dict:
    """Attach standard help text to an ``add_argument`` kwargs dict."""
    out = dict(kwargs)
    existing = out.get("help")
    if existing:
        text = str(existing).rstrip()
        if canonical not in text:
            out["help"] = f"{text} {canonical}"
    else:
        out["help"] = canonical
    return out


def convert_standard_args(args, arguments) -> dict:
    """Build skill kwargs from parsed CLI: bbox → tuple, dates → date.

    Raises UsageError if ``start_time`` is after ``end_time``.
    """
    params = {}
    for arg in arguments:
        dest = arg.dest
        raw = getattr(args, dest)
        if dest == "bbox":
            params[dest] = parse_bbox(raw) if raw is not None else None
        elif dest in ("date", "start_time", "end_time"):
            params[dest] = dates_mod.parse_date(raw) if raw is not None else None
        else:
            params[dest] = raw

    start = params.get("start_time")
    end = params.get("end_time")
    if start is not None and end is not None and start > end:
        raise UsageError(
            f"--start-time {start.isoformat()} is after --end-time {end.isoformat()}."
        )
    return params


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
        lat_slice = None
    else:
        diffs = np.diff(lat_vals)
        if (diffs > 0).all():
            lat_slice = slice(south, north)
        elif (diffs < 0).all():
            lat_slice = slice(north, south)
        else:
            raise UsageError(
                "lat axis is non-monotonic; cannot infer slice orientation. "
                "Re-sort the input or pre-process before subsetting."
            )
    if lat_slice is not None:
        ds = ds.sel({lat_dim: lat_slice})

    if west <= east:
        # Contiguous longitude span. Slice in the axis's own monotonic order.
        lon_slice = slice(west, east) if lon_ascending else slice(east, west)
        ds = ds.sel({lon_dim: lon_slice})
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
