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


def stamp_cf_attrs(ds):
    """Stamp CF ``standard_name``/``units``/``axis`` on spatial + time coords, non-destructively.

    The first latitude-named coord present (``latitude``/``lat``/``y``) gets
    ``standard_name="latitude"``, ``units="degrees_north"``, ``axis="Y"``; the
    first longitude-named coord (``longitude``/``lon``/``x``) gets the
    longitude equivalents; a ``time`` coord gets ``standard_name``/``axis``
    (its units/calendar belong in the write encoding). Every attr is applied
    with ``setdefault``, so source-provided values win. Returns ``ds``.
    """
    for name in _LAT_NAMES:
        if name in ds.coords:
            ds[name].attrs.setdefault("standard_name", "latitude")
            ds[name].attrs.setdefault("units", "degrees_north")
            ds[name].attrs.setdefault("axis", "Y")
            break
    for name in _LON_NAMES:
        if name in ds.coords:
            ds[name].attrs.setdefault("standard_name", "longitude")
            ds[name].attrs.setdefault("units", "degrees_east")
            ds[name].attrs.setdefault("axis", "X")
            break
    if "time" in ds.coords:
        ds["time"].attrs.setdefault("standard_name", "time")
        ds["time"].attrs.setdefault("axis", "T")
    return ds


def stamp_cf_coords(ds, *, long_names: dict | None = None):
    """Force CF ``standard_name``/``units``/``axis`` onto latitude/longitude/time coords.

    The overwriting counterpart of :func:`stamp_cf_attrs` for fetchers that
    assert the coordinate metadata rather than fill gaps: a ``latitude`` coord
    gets ``standard_name="latitude"``, ``units="degrees_north"``, ``axis="Y"``
    (``longitude`` the equivalents; ``time`` gets ``standard_name``/``axis``
    only -- its units/calendar belong in the write encoding), replacing any
    prior values. Only the canonical names are stamped; coords absent from the
    dataset are skipped. ``long_names`` optionally maps a coord name to a
    ``long_name`` applied with ``setdefault`` (a source-provided long_name
    wins). Returns ``ds``.
    """
    long_names = long_names or {}
    stamps = {
        "latitude": {"standard_name": "latitude", "units": "degrees_north", "axis": "Y"},
        "longitude": {"standard_name": "longitude", "units": "degrees_east", "axis": "X"},
        "time": {"standard_name": "time", "axis": "T"},
    }
    for name, attrs in stamps.items():
        if name in ds.coords:
            ds[name].attrs.update(attrs)
            if name in long_names:
                ds[name].attrs.setdefault("long_name", long_names[name])
    return ds


def udunits_error(units, *, catch: tuple = (ValueError,)):
    """Parse ``units`` with cf_units; return the parse failure, or None when it parses.

    ``catch`` is the exception tuple treated as a parse failure --
    ``cf_units.Unit`` raises :class:`ValueError` for an unparseable string;
    pass ``(Exception,)`` to convert any parse-time error. The caller owns
    message construction and raises its own typed error from the returned
    exception. Note that ``cf_units.Unit(None)`` and ``cf_units.Unit("")``
    return an "unknown" unit rather than raising, so a missing/blank-units
    guard also belongs to the caller.
    """
    import cf_units

    try:
        cf_units.Unit(units)
    except catch as exc:
        return exc
    return None


def cf_axes_missing(ds, axes: tuple = ("X", "Y", "T")) -> list:
    """Return the CF axis letters among ``axes`` that cf-xarray cannot resolve on ``ds``.

    Each axis is resolved independently off the dataset's CF attrs; a
    resolution failure counts that axis as missing rather than raising. An
    empty list means every requested axis resolved. Use it for write-side or
    post-write decode checks; the caller decides the failure message.
    """
    import cf_xarray  # noqa: F401 -- registers the .cf accessor

    missing = []
    for axis in axes:
        try:
            resolved = ds.cf.axes.get(axis)
        except Exception:  # noqa: BLE001 -- an unresolvable axis is the finding, not a failure
            resolved = None
        if not resolved:
            missing.append(axis)
    return missing


def cf_dim(obj, cf_name: str):
    """Name of the coord cf-xarray resolves for ``cf_name`` on ``obj``, or None.

    ``cf_name`` is a cf-xarray key (``"latitude"``, ``"longitude"``,
    ``"time"``, an axis letter); ``obj`` is a Dataset or DataArray. Unlike
    :func:`detect_spatial_dims`/:func:`detect_time_dim`, this is a bare lookup
    with no name-heuristic fallback and no error: an unresolvable key returns
    None and the caller decides what that means.
    """
    import cf_xarray  # noqa: F401 -- registers the .cf accessor

    try:
        return obj.cf[cf_name].name
    except KeyError:
        return None


def auto_variable(ds):
    """First real data var, skipping CF grid-mapping (CRS) containers.

    A CF grid-mapping variable (e.g. ``latitude_longitude``) is a zero-data
    CRS container: it carries a ``grid_mapping_name`` attr and is named by
    another var's ``grid_mapping`` attr. Skip those so a no-flag auto-pick
    lands on a real data var. Prefer a var with >= 2 dims (spatial/data),
    falling back to the first remaining candidate; None when no candidate
    remains.
    """
    mapping_targets = {
        ds[d].attrs.get("grid_mapping") for d in ds.data_vars if ds[d].attrs.get("grid_mapping")
    }
    candidates = [
        v
        for v in ds.data_vars
        if "grid_mapping_name" not in ds[v].attrs and v not in mapping_targets
    ]
    if not candidates:
        return None
    multidim = [v for v in candidates if len(ds[v].dims) >= 2]
    return (multidim or candidates)[0]


def lat_slice(lat_vals, north, south) -> slice:
    """Return a ``slice`` for ``ds.sel`` that works for ascending or descending lat.

    ``lat_vals`` is the latitude coord's values array; a descending axis gets
    ``slice(north, south)``, an ascending (or empty) one ``slice(south,
    north)``.
    """
    if lat_vals.size and lat_vals[0] > lat_vals[-1]:
        return slice(north, south)
    return slice(south, north)


def polygon_from_geojson(path, *, flag: str = "--mask-geojson"):
    """Return the unioned shapely polygon from a GeoJSON file.

    Accepts a FeatureCollection (every feature's geometry is unioned), a
    single Feature, or a bare geometry object. Raises :class:`UsageError`
    naming ``flag`` (the CLI flag the path arrived on) when the file is
    missing, unreadable/not JSON, or has no usable geometry.
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

    if data.get("type") == "FeatureCollection":
        geoms = [f["geometry"] for f in data.get("features", []) if f.get("geometry")]
    elif data.get("type") == "Feature":
        geoms = [data["geometry"]] if data.get("geometry") else []
    else:
        # A bare geometry object.
        geoms = [data]

    if not geoms:
        raise UsageError(f"{flag} {path} has no usable geometry.")

    from shapely.geometry import shape
    from shapely.ops import unary_union

    return unary_union([shape(g) for g in geoms])


def normalize_longitude(ds, lon_dim: str = "longitude"):
    """Map a 0..360 longitude axis onto [-180, 180) and sort ascending.

    Sources such as ERA5, OISST, and many CMIP6 grids store longitude as
    0..360; normalizing lets an N/W/S/E bbox with negative west/east values
    (the convention resolve-region and the fetchers use) select the right
    cells. The mapping is applied unconditionally -- values already in
    [-180, 180) are unchanged by it -- and the dataset is returned sorted
    ascending along ``lon_dim``.
    """
    lon = ((ds[lon_dim] + 180) % 360) - 180
    ds = ds.assign_coords({lon_dim: lon})
    return ds.sortby(lon_dim)


def stamp_cf_dsg(ds, var_attrs: dict, *, station_id_long_name: str, name_long_name: str):
    """Stamp CF timeSeries DSG attributes onto a station dataset in place.

    The coordinate attrs are fixed by the DSG shape: ``latitude`` and
    ``longitude`` get ``standard_name``/``long_name`` (``"station latitude"`` /
    ``"station longitude"``)/``units``/``axis``; ``time`` gets
    ``standard_name``/``long_name="time"``/``axis`` (its units/calendar belong
    in the write encoding); ``station_id`` gets ``cf_role="timeseries_id"``
    with ``station_id_long_name``; an optional ``name`` coord or variable gets
    ``name_long_name`` (absent, it is skipped).

    Every data variable's attrs are updated with
    ``coordinates="latitude longitude time"`` -- the load-bearing DSG attr
    tying the variable to its auxiliary coords -- followed by that variable's
    entry from ``var_attrs``, a mapping of data-variable name to its attr dict
    (``units``, ``long_name``, ``cell_methods``, any ``standard_name``); the
    per-variable values, including udunits validity of the units, are the
    caller's to build and validate. A data variable missing from ``var_attrs``
    raises :class:`KeyError`. Returns ``ds``.
    """
    ds["latitude"].attrs.update(
        standard_name="latitude", long_name="station latitude", units="degrees_north", axis="Y"
    )
    ds["longitude"].attrs.update(
        standard_name="longitude", long_name="station longitude", units="degrees_east", axis="X"
    )
    ds["time"].attrs.update(standard_name="time", long_name="time", axis="T")
    ds["station_id"].attrs.update(cf_role="timeseries_id", long_name=station_id_long_name)
    if "name" in ds.coords or "name" in ds.variables:
        ds["name"].attrs.update(long_name=name_long_name)

    for var in ds.data_vars:
        ds[var].attrs.update({"coordinates": "latitude longitude time", **var_attrs[var]})
    return ds


def verify_cf_dsg(ds) -> None:
    """Confirm cf-xarray resolves the timeSeries geometry before writing.

    cf-xarray identifies the DSG off ``cf_role="timeseries_id"`` (which must
    resolve to ``station_id``) and resolves the latitude/longitude/time
    coordinates off the coord attrs. If any of those do not resolve, the
    stamping is wrong and the store would falsely claim CF-1.13 compliance;
    raises :class:`DataError` listing every problem rather than write it.
    """
    import cf_xarray  # noqa: F401 -- registers the .cf accessor

    problems = []
    cf_roles = ds.cf.cf_roles
    # Membership, not exact list-equality: cf-xarray returns the resolved role
    # as a list, and a correctly-stamped store must not be rejected over that
    # list's shape or order -- only over station_id being absent from it.
    if "station_id" not in cf_roles.get("timeseries_id", []):
        problems.append(f"cf_role timeseries_id did not resolve to station_id (got {cf_roles})")
    for name in ("latitude", "longitude", "time"):
        try:
            ds.cf[name]
        except KeyError:
            problems.append(f"cf-xarray could not resolve the {name} coordinate")
    if problems:
        raise DataError(
            "CF-1.13 DSG verification failed before write:\n  - " + "\n  - ".join(problems)
        )


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
