"""Envelope shape vocabulary, CF dim detection, and bbox subsetting.

The weather-skills envelope is a CF-compliant Zarr store in one of three
shapes:

- ``gridded`` -- spatial dims ``latitude``/``longitude`` (aliases accepted on
  input) with a ``time`` dim;
- ``forecast`` -- a ``step`` (lead time) dim plus a scalar ``time`` coord for
  the forecast init date;
- ``station`` -- a single spatial dim ``station_id`` with 1-D
  ``latitude(station_id)`` / ``longitude(station_id)`` coords and a ``time``
  dim.

``any`` opts a declared input out of shape validation.

Coordinate identification goes through cf-xarray's CF-attr resolution first,
falling back to name heuristics, with explicit ``--dims``/``--time-dim``
overrides winning over both.
"""

from weather_skills_core.errors import DataError, UsageError

GRIDDED = "gridded"
FORECAST = "forecast"
STATION = "station"
ANY = "any"

TYPES = (GRIDDED, FORECAST, STATION, ANY)

_LAT_NAMES = ("latitude", "lat", "y")
_LON_NAMES = ("longitude", "lon", "x")


def parse_bbox(bbox: str) -> tuple:
    """Parse an ``N/W/S/E`` bbox string into four floats.

    Raises :class:`UsageError` when the value is not four slash-separated
    decimal degrees.
    """
    try:
        north, west, south, east = (float(x) for x in bbox.split("/"))
    except ValueError:
        raise UsageError("--bbox must be four decimal degrees N/W/S/E.") from None
    return north, west, south, east


def detect_spatial_dims(ds, override: str | None = None) -> tuple:
    """Identify the latitude and longitude dim names of a dataset.

    ``override`` is a ``LAT,LON`` string (the ``--dims`` flag); when given, the
    named dims must exist. Otherwise cf-xarray CF-attr detection is tried
    first, then name heuristics. Raises :class:`UsageError` when nothing
    resolves, naming the coords searched.
    """
    if override:
        parts = [s.strip() for s in override.split(",")]
        if len(parts) != 2:
            raise UsageError("--dims must be two comma-separated names: LAT,LON.")
        lat_dim, lon_dim = parts
        if lat_dim not in ds.dims or lon_dim not in ds.dims:
            raise UsageError(f"--dims names not in dataset dims {list(ds.dims)}")
        return lat_dim, lon_dim
    try:
        import cf_xarray  # noqa: F401 -- registers the .cf accessor

        return ds.cf["latitude"].name, ds.cf["longitude"].name
    except KeyError:
        pass
    lat_dim = next((n for n in _LAT_NAMES if n in ds.dims), None)
    lon_dim = next((n for n in _LON_NAMES if n in ds.dims), None)
    if lat_dim is None or lon_dim is None:
        raise UsageError(
            f"could not identify lat/lon coords via CF metadata or name "
            f"heuristics in {list(ds.coords)}. Pass --dims to override."
        )
    return lat_dim, lon_dim


def detect_time_dim(ds, override: str | None = None) -> str:
    """Identify the time-like dim name of a dataset.

    ``override`` is the ``--time-dim`` flag value; when given, the named dim
    must exist. Otherwise cf-xarray CF-attr detection is tried first, then the
    literal name ``time``. Raises :class:`UsageError` when nothing resolves.
    """
    if override:
        if override not in ds.dims:
            raise UsageError(f"--time-dim {override!r} not in dataset dims {list(ds.dims)}")
        return override
    try:
        import cf_xarray  # noqa: F401 -- registers the .cf accessor

        name = ds.cf["time"].name
        if name in ds.dims:
            return name
    except KeyError:
        pass
    if "time" in ds.dims:
        return "time"
    raise UsageError(
        f"could not identify a time dim via CF metadata or name heuristics in "
        f"{list(ds.dims)}. Pass --time-dim to override."
    )


def detect_type(ds) -> str:
    """Classify a dataset as ``station``, ``forecast``, or ``gridded``.

    A ``station_id`` dim makes it a station envelope. A ``step`` dim with a
    scalar ``time`` coord makes it a forecast envelope. Everything else is
    gridded.
    """
    if "station_id" in ds.dims:
        return STATION
    if "step" in ds.dims and "time" in ds.coords and ds["time"].ndim == 0:
        return FORECAST
    return GRIDDED


def validate_input(
    ds, allowed, name: str, *, dims: str | None = None, time_dim: str | None = None
) -> str:
    """Validate a dataset against the declared input type(s).

    ``allowed`` is a type string or list of type strings from :data:`TYPES`;
    ``name`` labels the input in error messages (usually the path). Returns
    the detected type. Raises :class:`UsageError` naming the offending or
    missing dim/variable when the shape does not match.

    ``dims`` and ``time_dim`` are the user's override flag values (``--dims``
    as a ``LAT,LON`` string, ``--time-dim`` as a dim name). When ``dims`` is
    given, the gridded/forecast spatial-dims check validates that the named
    dims exist instead of running CF/heuristic detection, so an input whose
    spatial dims detection cannot find still validates under the overridden
    names (and fails naming them when they are absent). When ``time_dim`` is
    given, the named dim must exist on the dataset. Overrides participate
    only in typed validation: an input declared ``any`` skips every shape
    check, overrides included. Type classification (:func:`detect_type`) is
    override-independent; it keys on the fixed ``station_id``/``step``/
    ``time`` names.
    """
    if isinstance(allowed, str):
        allowed = [allowed]
    unknown = [t for t in allowed if t not in TYPES]
    if unknown:
        raise ValueError(f"unknown envelope type(s) {unknown}; valid types: {list(TYPES)}")
    actual = detect_type(ds)
    if ANY not in allowed and actual not in allowed:
        raise UsageError(
            f"input {name} is a {actual} envelope, but this skill expects "
            f"{' or '.join(allowed)}: {_shape_detail(ds, allowed)}"
        )
    # Structural checks for the matched shape.
    if actual == STATION and (ANY not in allowed or STATION in allowed):
        for coord in ("latitude", "longitude"):
            if coord not in ds.coords:
                raise UsageError(
                    f"input {name} is a station envelope but has no {coord!r} "
                    f"coordinate (coords: {list(ds.coords)})."
                )
            if tuple(ds[coord].dims) != ("station_id",):
                raise UsageError(
                    f"input {name} is a station envelope but its {coord!r} "
                    f"coordinate has dims {list(ds[coord].dims)}, expected "
                    "('station_id',)."
                )
    elif actual in (GRIDDED, FORECAST) and ANY not in allowed:
        # Gridded/forecast envelopes must expose identifiable spatial dims;
        # a --dims override replaces detection with an existence check.
        detect_spatial_dims(ds, dims)
    if time_dim and ANY not in allowed:
        # A --time-dim override must name an existing dim.
        detect_time_dim(ds, time_dim)
    return actual


def _shape_detail(ds, allowed) -> str:
    """Describe what the expected shape(s) require and what the dataset has."""
    details = []
    if FORECAST in allowed and "step" not in ds.dims:
        details.append("no 'step' dim")
    if FORECAST in allowed and "step" in ds.dims:
        if "time" not in ds.coords:
            details.append("no scalar 'time' coord")
        elif ds["time"].ndim != 0:
            details.append("'time' is a dim, not a scalar init coord")
    if STATION in allowed and "station_id" not in ds.dims:
        details.append("no 'station_id' dim")
    if GRIDDED in allowed and "station_id" in ds.dims:
        details.append("has a 'station_id' dim")
    if GRIDDED in allowed and "step" in ds.dims:
        details.append("has a 'step' dim")
    detail = "; ".join(details) if details else "shape does not match"
    return f"{detail} (dims: {list(ds.dims)})"


def bbox_subset(ds, bbox, *, lat_dim: str | None = None, lon_dim: str | None = None):
    """Subset a gridded dataset to an ``N/W/S/E`` bbox.

    Subsetting rules:

    - a longitude axis on 0..360 is wrapped onto [-180, 180] and sorted
      ascending before the selection, so negative west/east values select
      correctly;
    - the latitude slice follows the axis's own monotonic order (ascending or
      descending), so the same bbox works regardless of orientation; a
      single-row latitude axis is passed through unsliced; an empty or
      non-monotonic axis raises :class:`UsageError`;
    - the longitude axis is guarded the same way: empty or non-monotonic
      raises :class:`UsageError`;
    - a bbox with ``west <= east`` selects the contiguous span, sliced in the
      longitude axis's own order; ``west > east`` crosses the antimeridian and
      selects the union ``lon >= west OR lon <= east`` by concatenating the
      two wing slices in the axis's native order, dropping the interior band
      while preserving each variable's dtype;
    - an empty result is a data error (:class:`DataError`, exit 1).

    ``bbox`` is an ``N/W/S/E`` string or a ``(north, west, south, east)``
    tuple. Dim names are auto-detected unless given.
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
