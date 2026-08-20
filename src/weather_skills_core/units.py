"""Physical units for skill datasets: equivalence, conversion, and quantification.

Skills carry real units on every data variable so datasets can be combined,
converted, and compared without guessing from a variable's name. Units live in
the CF ``units`` attr on disk and become pint quantities in memory
(``quantify_dataset`` on input, ``dequantify_dataset`` before writing Zarr).

A few known standard kinds (``STANDARD`` below) get explicit treatment: they
must carry parseable units and have a standard display unit skills convert to.
Everything else may include units optionally and is passed through untouched.
Fetch writes accumulated variables as rates; ``rate_to_total`` refuses precip
amounts (see UNITS.md).

See ``skills/weather-skill-authoring/references/UNITS.md`` for the full policy.
Built on pint-xarray with CF/UDUNITS strings via ``cf_xarray.units``.
"""

from __future__ import annotations

import re

import cf_xarray.units  # noqa: F401 — CF/UDUNITS strings on the pint registry
import numpy as np
import pint_xarray

from weather_skills_core.errors import UsageError

ureg = pint_xarray.unit_registry
if "pentad" not in ureg:
    ureg.define("pentad = 5 * day")
if "dekad" not in ureg:
    ureg.define("dekad = 10 * day")

# Data-variable attr: native sample spacing as a pint duration string
# (``"30 minute"``, ``"1 day"``). Uniform axes only; irregular axes use CF
# ``{dim}_bounds`` instead (XOR). Stamped by fetchers; kept through aggregate.
DATA_INTERVAL_ATTR = "data_interval"

# Vertex dim on a CF bounds coordinate ``{dim}_bounds`` with shape (dim, 2).
_BOUNDS_NV_DIM = "nv"

# Data-variable attr: the time window each aggregated value spans, as a pint
# duration string (``"1 day"``, ``"1 dekad"``). Complements CF ``cell_methods``
# (which says *how* values combined) and drives ``convert-to-totals``
# (rate × period). Stamped by aggregate-temporal only.
AGGREGATION_PERIOD_ATTR = "aggregation_period"

# Coordinate on the aggregated time/step axis: completeness of each interval
# vs ``data_interval`` (0–1). Stamped by aggregate-temporal only.
AGGREGATION_COVERAGE_COORD = "aggregation_coverage"

_TIME = r"(?:second|sec|minute|min|hour|hr|day|s|h|d)"
_RATE_RE = re.compile(
    rf"(?:/\s*{_TIME}\b|\b{_TIME}(?:\*\*|\^)?-1\b|\b(?:W|watts?)\b)",
    re.IGNORECASE,
)

# CLI period label → the aggregation_period string stamped for that window.
PERIOD_TO_AGGREGATION = {
    "daily": "1 day",
    "weekly": "7 day",
    "dekadal": "1 dekad",
    "monthly": "1 month",
}

# The vocabulary for every standard kind, in classification precedence order.
#   units_required         must carry a units attr for explicit skill treatment
#   units / standard_name  what to_standard_units stamps (None keeps existing)
#   standard_names, standard_name_endswith, standard_name_contains
#                          CF standard_name matches, tried first
#   name_hints             variable-name whole/prefix/suffix match (exact names
#                          only — not units fingerprinting)
#   unit_candidates        units for convert/fingerprint helpers (not classify)
#   depth_candidates       matched after ÷ liquid-water density in converts
STANDARD = {
    "temp": {
        "units_required": True,
        "units": "degree_Celsius",
        "standard_name": None,
        # Air temperature only — not SST, dew point, skin, etc.
        "standard_names": frozenset({"air_temperature"}),
        "standard_name_endswith": (),
        "standard_name_contains": (),
        "unit_candidates": ("degree_Celsius", "kelvin", "degree_Fahrenheit"),
        "depth_candidates": (),
        "name_hints": ("t2m", "2m_temperature", "temp", "tmax", "tmin", "tavg", "tas"),
    },
    # Listed before precip_amount so CF rate names win over amount names.
    "precip": {
        "units_required": True,
        "units": "mm day-1",
        "standard_name": "lwe_precipitation_rate",
        "standard_names": frozenset(
            {"lwe_precipitation_rate", "precipitation_flux", "rainfall_rate", "rainfall_flux"}
        ),
        "standard_name_endswith": ("_precipitation_rate", "_rainfall_rate"),
        "standard_name_contains": (),
        "unit_candidates": ("mm day-1", "mm/day", "m s-1", "kg m-2 s-1", "kg m**-2 s**-1"),
        "depth_candidates": ("m s-1", "mm day-1"),
        "name_hints": ("precip", "prcp", "rainfall", "rain", "tp", "pr"),
    },
    # Amount metadata for the totals utilities (rate × period → mm).
    "precip_amount": {
        "units_required": False,
        "units": "mm",
        "standard_name": "lwe_thickness_of_precipitation_amount",
        "standard_names": frozenset(
            {
                "lwe_thickness_of_precipitation_amount",
                "precipitation_amount",
                "thickness_of_rainfall_amount",
                "lwe_thickness_of_rainfall_amount",
            }
        ),
        "standard_name_endswith": (),
        "standard_name_contains": ("precipitation_amount", "rainfall_amount"),
        "unit_candidates": ("mm", "m", "kg m-2", "kg m**-2"),
        "depth_candidates": ("m", "mm"),
        "name_hints": (),
    },
}

# Colorbar / CF long_name for precip amounts (rate × period). Overwrites leftover
# rate display names; fetchers may still set a more specific long_name.
PRECIP_AMOUNT_LONG_NAME = "Total precipitation"


def water_density():
    """Liquid-water density as a pint quantity (1000 kg m-3)."""
    return ureg.Quantity(1000.0, "kg m-3")


def units_match(units: str, candidates, *, as_depth: bool = False) -> bool:
    """True if ``units`` matches any candidate string.

    Exact pint equality by default; with ``as_depth`` the units are divided by
    liquid-water density first and compared for dimensional compatibility, so
    mass precip reads as a depth.
    """
    try:
        parsed = ureg.Unit(units)
        if as_depth:
            parsed = (ureg.Quantity(1.0, units) / water_density()).units
    except Exception:  # noqa: BLE001 — an invalid unit string is a non-match
        return False
    for cand in candidates:
        try:
            other = ureg.Unit(cand)
        except Exception:  # noqa: BLE001, S112 — skip unparseable candidates
            continue
        if parsed.is_compatible_with(other) if as_depth else parsed == other:
            return True
    return False


def kind_from_units(units: str) -> str | None:
    """Fingerprint units as a ``STANDARD`` kind, or None if nothing matches."""
    for kind, spec in STANDARD.items():
        if units_match(units, spec["unit_candidates"]) or units_match(
            units, spec["depth_candidates"], as_depth=True
        ):
            return kind
    return None


def units_equal(a, b) -> bool:
    """True if both strings are pint-equivalent (spelling-independent)."""
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    a, b = a.strip(), b.strip()
    if not a or not b:
        return False
    if a == b:
        return True
    try:
        return ureg.Unit(a) == ureg.Unit(b)
    except Exception:  # noqa: BLE001
        return False


def variable_units(da) -> str | None:
    """Units string from a pint Quantity, else from the ``units`` attr."""
    units = getattr(da.pint, "units", None)
    if units is not None:
        return str(units)
    raw = da.attrs.get("units")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def classify_variable(name: str, *, units=None, standard_name=None) -> str | None:
    """Return the ``STANDARD`` kind for a variable, or None.

    Precedence: CF ``standard_name``, then named variable hints. Units alone do
    not classify a variable (e.g. bare ``kg m-2 s-1`` is not precip). When a
    name hint matches ``precip``, amount vs rate is taken from ``units`` when
    that fingerprint is unambiguous (``kg m-2`` / ``mm`` → ``precip_amount``,
    ``kg m-2 s-1`` / ``mm day-1`` → ``precip``). If units are present but not
    convertible to a precip rate or amount (e.g. IMERG
    ``precipitation_quality_index_surface`` with ``units="1"``), the name hint
    is ignored.
    """
    sn = standard_name.strip().lower() if isinstance(standard_name, str) else ""
    for kind, spec in STANDARD.items():
        if (
            sn in spec["standard_names"]
            or sn.endswith(spec["standard_name_endswith"])
            or any(part in sn for part in spec["standard_name_contains"])
        ):
            return kind

    key = name.lower()
    for kind, spec in STANDARD.items():
        if any(h == key or key.startswith(h) or key.endswith(h) for h in spec["name_hints"]):
            if kind == "precip" and isinstance(units, str) and units.strip():
                return _precip_kind_from_hint_units(units)
            return kind
    return None


def _precip_kind_from_hint_units(units: str) -> str | None:
    """Map precip name-hint units to a kind, or None if they are not precip."""
    from_units = kind_from_units(units)
    if from_units in ("precip", "precip_amount"):
        return from_units
    if units_convertible(units, STANDARD["precip"]["units"]):
        return "precip"
    if units_convertible(units, STANDARD["precip_amount"]["units"]):
        return "precip_amount"
    return None


def cell_methods_has_sum(cell_methods) -> bool:
    """True if CF ``cell_methods`` includes a ``sum`` method."""
    if not isinstance(cell_methods, str) or not cell_methods.strip():
        return False
    return bool(re.search(r":\s*sum\b", cell_methods, re.IGNORECASE))


def format_cell_methods(dim: str, method: str, *, interval: str | None = None) -> str:
    """Build a CF ``cell_methods`` string, optionally with ``(interval: ...)``."""
    base = f"{dim}: {method}"
    if interval is None or not str(interval).strip():
        return base
    return f"{base} (interval: {str(interval).strip()})"


def parse_aggregation_period(period: str):
    """Parse an ``aggregation_period`` string to a pint duration Quantity.

    ``period`` is the value of the ``aggregation_period`` attr, e.g. ``"1 day"``
    or ``"1 dekad"``. Raises ``UsageError`` if it is empty, unparseable, or not
    a time duration (e.g. ``"5 kg"``).
    """
    if not isinstance(period, str) or not period.strip():
        raise UsageError(f"invalid aggregation_period {period!r}")
    try:
        q = ureg.Quantity(period.strip())
    except Exception as exc:  # noqa: BLE001
        raise UsageError(f"invalid aggregation_period {period!r}: {exc}") from None
    if q.dimensionality != ureg.Quantity(1, "day").dimensionality:
        raise UsageError(f"aggregation_period {period!r} is not a time duration")
    return q


def format_duration(q) -> str:
    """Format a pint duration as a compact stamp (``1 day``, ``30 minute``)."""
    for unit in ("day", "hour", "minute", "second"):
        try:
            mag = float(q.to(unit).magnitude)
        except (AttributeError, TypeError, ValueError):
            continue
        if mag >= 1 and abs(mag - round(mag)) <= 1e-6:
            return f"{round(mag)} {unit}"
    try:
        days = float(q.to("day").magnitude)
    except (AttributeError, TypeError, ValueError):
        return f"{q:~P}"
    return f"{int(days)} day" if days.is_integer() else f"{q:~P}"


def infer_timestep(ds, dim: str):
    """Median positive spacing along ``dim`` as a pint duration Quantity."""
    if dim not in ds.dims and dim not in ds.coords:
        raise UsageError(f"dimension/coord {dim!r} not in dataset")
    values = np.asarray(ds[dim].values)
    if values.size < 2:
        raise UsageError(f"need at least 2 points on {dim!r} to infer timestep")
    diffs, unit = _axis_tick_diffs(values)
    positive = diffs[diffs > 0]
    if positive.size == 0:
        raise UsageError(f"no positive spacings on {dim!r}")
    return ureg.Quantity(float(np.median(positive)), unit).to("day")


def _axis_tick_diffs(values):
    """Positive consecutive spacings as (diffs, numpy datetime/timedelta unit)."""
    values = np.asarray(values)
    if values.size < 2:
        raise UsageError("need at least 2 points to infer spacing")
    if np.issubdtype(values.dtype, np.timedelta64) or np.issubdtype(values.dtype, np.datetime64):
        unit, _ = np.datetime_data(values.dtype)
        diffs = np.diff(values.astype(values.dtype).astype(np.int64))
        return diffs, unit
    failure = None
    for dtype in ("datetime64[ns]", "timedelta64[ns]"):
        try:
            diffs = np.diff(values.astype(dtype).astype(np.int64))
        except (TypeError, ValueError) as exc:
            failure = exc
            continue
        return diffs, "nanosecond"
    raise UsageError(f"could not infer spacing: {failure}")


def assert_nonoverlapping_intervals(ds, dim: str, period: str) -> None:
    """Refuse convert-to-totals when consecutive labels are finer than ``period``.

    A singleton axis is allowed: spacing cannot be inferred, and one sample
    cannot be an overlapping series. Typical after aggregating a short fetch
    to a single weekly/dekadal/monthly bin. With CF bounds, uses the minimum
    label spacing; otherwise the median (``infer_timestep``).
    """
    if dim not in ds.dims and dim not in ds.coords:
        raise UsageError(f"dimension/coord {dim!r} not in dataset")
    if ds.sizes.get(dim, 0) < 2:
        return
    base = parse_aggregation_period(period)
    bounds_name = ds[dim].attrs.get("bounds") if dim in ds.coords or dim in ds.dims else None
    if isinstance(bounds_name, str) and bounds_name in ds:
        diffs, unit = _axis_tick_diffs(ds[dim].values)
        positive = diffs[diffs > 0]
        if positive.size == 0:
            raise UsageError(f"no positive spacings on {dim!r}")
        dt = ureg.Quantity(float(np.min(positive)), unit).to("day")
    else:
        dt = infer_timestep(ds, dim)
    if dt < base:
        raise UsageError(
            f"spacing on {dim!r} ({format_duration(dt)}) is smaller than "
            f"aggregation_period {period!r}; refusing convert-to-totals "
            "(overlapping intervals would overcount). "
            f"Run select --dim {dim} to keep a non-overlapping subset, "
            "then convert-to-totals."
        )


def looks_like_precip_total(da) -> bool:
    """True if ``da`` is a precip amount (amount units or ``cell_methods`` sum)."""
    name = da.name if isinstance(da.name, str) else ""
    units = variable_units(da)
    has_units = isinstance(units, str) and bool(units.strip())
    kind = classify_variable(
        name, units=units, standard_name=da.attrs.get("standard_name")
    )
    if kind not in ("precip", "precip_amount"):
        return False
    return (
        kind == "precip_amount"
        or (has_units and kind_from_units(units) == "precip_amount")
        or cell_methods_has_sum(da.attrs.get("cell_methods"))
    )


def rate_to_total(da, period: str):
    """Multiply a rate DataArray by ``aggregation_period`` → amount (quantified).

    Refuses precip totals: multiplying an amount by a period would double-count.
    """
    if looks_like_precip_total(da):
        raise UsageError(
            f"variable {da.name!r} looks like a precip total "
            "(amount units or cell_methods sum); rate_to_total converts "
            "rates × period and cannot take amounts"
        )
    base = parse_aggregation_period(period)
    quantified_in = da.pint.units is not None
    qda = da if quantified_in else da.pint.quantify()
    if qda.pint.units is None:
        raise UsageError(f"variable {da.name!r} has no units to convert to totals")
    total = qda * base
    # Prefer mm for precip depth rates.
    try:
        total = total.pint.to(STANDARD["precip_amount"]["units"])
    except (pint_xarray.pint.DimensionalityError, pint_xarray.errors.PintExceptionGroup):
        pass
    return total


def quantify_dataset(ds):
    """Attach pint units to data vars; leave unitless vars and coords alone.

    Variables whose kind has ``units_required`` in ``STANDARD`` (known kinds
    skills treat explicitly) must have a non-empty ``units`` attr. Other
    variables may omit units. Precip amounts are quantified like any other
    variable; ``rate_to_total`` is what refuses them. Coordinate ``units``
    attrs are kept as attrs.
    """
    for name, da in ds.data_vars.items():
        units = da.attrs.get("units")
        has_units = isinstance(units, str) and bool(units.strip())
        kind = classify_variable(name, units=units, standard_name=da.attrs.get("standard_name"))
        if kind is not None and STANDARD[kind]["units_required"] and not has_units:
            raise UsageError(
                f"variable {name!r} is a standard kind ({kind}) and requires "
                "a units attribute for explicit skill treatment"
            )

    out = ds.copy(deep=False)
    saved_coord_units = {}
    for name in list(out.coords):
        if "units" in out[name].attrs:
            saved_coord_units[name] = out[name].attrs.pop("units")
    for name in list(out.data_vars):
        units = out[name].attrs.get("units")
        if isinstance(units, str) and not units.strip():
            out[name].attrs.pop("units", None)

    try:
        out = out.pint.quantify()
    except Exception as exc:  # noqa: BLE001 — surface pint parse failures cleanly
        raise UsageError(f"could not quantify dataset units: {exc}") from None

    for name, units in saved_coord_units.items():
        out[name].attrs["units"] = units
    return out


def dequantify_dataset(ds):
    """Strip pint quantities back to plain arrays with ``units`` attrs."""
    return ds.pint.dequantify()


def convert_values(values, src_units: str, dst_units: str):
    """Convert array values with pint (CF strings via cf_xarray.units).

    If dimensions do not match directly, retry after dividing or multiplying by
    liquid-water density (1000 kg m-3) so mass precip (``kg m-2``) can convert
    to depth (``mm``) and flux (``kg m-2 s-1``) to depth rate (``mm day-1``).

    Returns ``(converted_array, density_converted)`` where the flag is True when
    the density step was required.
    """
    q = ureg.Quantity(np.asarray(values), src_units)
    try:
        return np.asarray(q.to(dst_units).magnitude), False
    except pint_xarray.pint.DimensionalityError:
        pass
    rho = water_density()
    for density_scaled in (q / rho, q * rho):
        try:
            return np.asarray(density_scaled.to(dst_units).magnitude), True
        except pint_xarray.pint.DimensionalityError:
            continue
    raise UsageError(f"{src_units!r} not convertible to {dst_units!r}")


def units_convertible(src_units: str, dst_units: str) -> bool:
    """True if ``convert_values`` can take ``src_units`` to ``dst_units``."""
    try:
        convert_values(np.array([1.0]), src_units, dst_units)
    except Exception:  # noqa: BLE001 — unparseable or incompatible units
        return False
    return True


def convert_dataarray(da, dst_units: str):
    """Convert a DataArray with pint-xarray; density step when dims do not match.

    Accepts already-quantified or attrs-based units. Returns
    ``(converted_dataarray, density_converted)``. Result stays quantified when
    the input was quantified; otherwise returns dequantified with ``units`` attr
    stamped to ``dst_units``.
    """
    quantified_in = da.pint.units is not None
    qda = da if quantified_in else da.pint.quantify()
    try:
        out = qda.pint.to(dst_units)
        if quantified_in:
            return out, False
        out = out.pint.dequantify()
        out.attrs = {**out.attrs, "units": dst_units}
        return out, False
    except (pint_xarray.pint.DimensionalityError, pint_xarray.errors.PintExceptionGroup):
        pass

    rho = water_density()
    data = qda.data
    for density_scaled in (data / rho, data * rho):
        try:
            converted = density_scaled.to(dst_units)
        except pint_xarray.pint.DimensionalityError:
            continue
        if quantified_in:
            return da.copy(data=converted), True
        out = da.copy(data=np.asarray(converted.magnitude))
        out.attrs = {**da.attrs, "units": dst_units}
        return out, True
    raise UsageError(f"{variable_units(da)!r} not convertible to {dst_units!r}")


def to_standard_units(ds, variables=None):
    """Convert recognized temp/precip data vars to standard display units.

    Classification is by CF ``standard_name`` or named variable hints (with
    precip amount-vs-rate from units when needed). On a match, stamps the
    kind's CF ``standard_name`` (when set) and converts units; the variable
    **name** is left untouched. Unrecognized or unitless variables are left
    unchanged. Raises ``UsageError`` if a classified variable cannot be
    converted.
    """
    names = list(variables) if variables is not None else list(ds.data_vars)
    out = ds
    dirty = False
    for name in names:
        if name not in ds.data_vars:
            raise UsageError(f"variable {name!r} not in dataset (have {list(ds.data_vars)})")
        da = ds[name]
        units = variable_units(da)
        kind = classify_variable(name, units=units, standard_name=da.attrs.get("standard_name"))
        if units is None or kind not in STANDARD:
            continue
        dst_units = STANDARD[kind]["units"]
        dst_standard_name = STANDARD[kind]["standard_name"]
        if units_equal(units, dst_units) and da.pint.units is None:
            # Already standard; only the spelling and standard_name change.
            converted, attrs = da, {**da.attrs, "units": dst_units}
        else:
            converted, _ = convert_dataarray(da, dst_units)
            attrs = dict(converted.attrs)
        if dst_standard_name:
            attrs["standard_name"] = dst_standard_name
        if not dirty:
            out = ds.copy()
            dirty = True
        out[name] = converted  # keep the original variable name
        out[name].attrs = attrs
    return out


def normalize_unit_strings(ds):
    """Rewrite GRIB-style ``kg m**-2`` unit strings to pint/CF form (in place).

    Only touches compact ``token**-N`` spellings; leaves pint pretty-print forms
    like ``kilogram / meter ** 2`` alone.
    """
    for name in list(ds.data_vars) + list(ds.coords):
        units = ds[name].attrs.get("units")
        if isinstance(units, str) and "**" in units and " ** " not in units:
            ds[name].attrs["units"] = units.replace("**", "")
    return ds


def looks_like_rate_display_name(value) -> bool:
    """True if a CF/GRIB display name describes a rate or flux, not an amount."""
    if not isinstance(value, str) or not value.strip():
        return False
    lowered = value.casefold()
    return "rate" in lowered or "flux" in lowered


def stamp_precip_amounts(ds):
    """Stamp amount CF metadata when units are precip depth/mass (overwrite rate names)."""
    amount_sn = STANDARD["precip_amount"]["standard_name"]
    for name in ds.data_vars:
        units = ds[name].attrs.get("units")
        if not isinstance(units, str):
            continue
        if kind_from_units(units) == "precip_amount":
            ds[name].attrs["standard_name"] = amount_sn
            long_name = ds[name].attrs.get("long_name")
            if not (isinstance(long_name, str) and long_name.strip()) or looks_like_rate_display_name(
                long_name
            ):
                ds[name].attrs["long_name"] = PRECIP_AMOUNT_LONG_NAME
            if looks_like_rate_display_name(ds[name].attrs.get("GRIB_name")):
                ds[name].attrs["GRIB_name"] = PRECIP_AMOUNT_LONG_NAME
    return ds


def _time_or_step_dim(ds, dim=None) -> str | None:
    if dim is not None:
        if dim not in ds.dims:
            raise UsageError(f"dimension {dim!r} not in dataset (have {list(ds.dims)})")
        return dim
    if "time" in ds.dims:
        if ds.sizes["time"] == 1 and "step" in ds.dims:
            return "step"
        return "time"
    if "step" in ds.dims:
        return "step"
    return None


def data_interval_of(ds) -> str | None:
    """First stamped ``data_interval`` on a data variable, or None."""
    for name in ds.data_vars:
        val = ds[name].attrs.get(DATA_INTERVAL_ATTR)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def stamp_data_interval(ds, period=None, dim=None, origin=None):
    """Stamp native cell geometry: scalar ``data_interval`` or CF bounds.

    ``period`` is a pint duration string (``"1 day"``, ``"30 minute"``) and
    always writes the scalar attr (caller knows the axis is uniform). When
    omitted, spacing is inferred from ``dim`` (or ``time`` / ``step``): equal
    steps get ``data_interval``; unequal steps get ``{dim}_bounds`` (start,
    end) and no ``data_interval``. ``origin`` is the left edge of the first
    cell when writing bounds (timedelta ``step`` defaults to 0).
    """
    if period is not None:
        period = format_duration(parse_aggregation_period(period))
        for name in ds.data_vars:
            ds[name].attrs[DATA_INTERVAL_ATTR] = period
        return ds

    axis = _time_or_step_dim(ds, dim)
    if axis is None:
        raise UsageError("cannot stamp data_interval: no time/step dim and no period given")
    values = np.asarray(ds[axis].values)
    if values.size < 2:
        raise UsageError(
            f"cannot stamp data_interval: need at least 2 points on {axis!r} "
            "to infer spacing (or pass period=)"
        )
    diffs, unit = _axis_tick_diffs(values)
    if diffs.size == 0 or np.any(diffs <= 0):
        raise UsageError(f"{axis!r} must be strictly increasing to stamp spacing")
    if np.all(diffs == diffs[0]):
        period = format_duration(ureg.Quantity(float(diffs[0]), unit).to("day"))
        for name in ds.data_vars:
            ds[name].attrs[DATA_INTERVAL_ATTR] = period
        return ds

    if origin is None:
        if np.issubdtype(values.dtype, np.timedelta64):
            origin = np.asarray(0).astype(values.dtype)
        else:
            raise UsageError(
                f"irregular {axis!r} axis needs an origin for CF bounds "
                "(timedelta step defaults to 0)"
            )
    n = values.size
    pairs = np.empty((n, 2), dtype=values.dtype)
    pairs[0, 0] = np.asarray(origin).astype(values.dtype, copy=False)
    pairs[1:, 0] = values[:-1]
    pairs[:, 1] = values
    bound_name = f"{axis}_bounds"
    out = ds.assign_coords({bound_name: ((axis, _BOUNDS_NV_DIM), pairs)})
    out[axis].attrs["bounds"] = bound_name
    for name in out.data_vars:
        out[name].attrs.pop(DATA_INTERVAL_ATTR, None)
    return out


def expected_samples_in_period(aggregation_period: str, data_interval: str) -> int:
    """How many native samples a complete ``aggregation_period`` should hold."""
    period = parse_aggregation_period(aggregation_period)
    native = parse_aggregation_period(data_interval)
    ratio = float((period / native).to("dimensionless").magnitude)
    if ratio <= 0:
        raise UsageError(
            f"expected samples for {aggregation_period!r} / {data_interval!r} is not positive"
        )
    return max(1, round(ratio))


def coverage_values(ds, dim: str):
    """``aggregation_coverage`` along ``dim``, or ones if the coord is missing."""
    if AGGREGATION_COVERAGE_COORD in ds.coords and dim in ds[AGGREGATION_COVERAGE_COORD].dims:
        return np.asarray(ds[AGGREGATION_COVERAGE_COORD].values, dtype=float)
    n = ds.sizes.get(dim, 0)
    return np.ones(n, dtype=float)


def filter_min_coverage(ds, dim: str, min_coverage: float):
    """Drop intervals whose ``aggregation_coverage`` is below ``min_coverage``.

    Raises if nothing remains. Missing coverage is treated as 1.0.
    """
    if min_coverage < 0 or min_coverage > 1:
        raise UsageError(f"--min-coverage must be in [0, 1]; got {min_coverage}")
    if dim not in ds.dims:
        raise UsageError(f"dimension {dim!r} not in dataset (have {list(ds.dims)})")
    cov = coverage_values(ds, dim)
    if cov.size != ds.sizes[dim]:
        raise UsageError(
            f"{AGGREGATION_COVERAGE_COORD!r} length {cov.size} does not match "
            f"{dim!r} size {ds.sizes[dim]}"
        )
    keep = cov >= min_coverage - 1e-12
    if not np.any(keep):
        raise UsageError(
            f"no {dim} intervals meet --min-coverage {min_coverage} "
            f"(coverage {np.array2string(cov, precision=3)})"
        )
    if np.all(keep):
        return ds
    return ds.isel({dim: keep})


def _step_delta_days(ds):
    steps = np.asarray(ds["step"].values)
    if steps.size < 2:
        raise UsageError("need at least 2 steps to deaccumulate")
    try:
        diffs_ns = np.diff(steps.astype("timedelta64[ns]").astype(np.int64))
    except (TypeError, ValueError) as exc:
        raise UsageError(f"could not diff step axis: {exc}") from None
    if np.any(diffs_ns <= 0):
        raise UsageError("step axis must be strictly increasing")
    return diffs_ns.astype(np.float64) / 1e9 / 86400.0


def _broadcast_along_step(delta_days, dims):
    shape = [1] * len(dims)
    shape[dims.index("step")] = -1
    return delta_days.reshape(shape)


def deaccumulate_along_step(ds, names=None):
    """Per-step diff along ``step``. Precip amounts become ``mm day-1`` rates.

    Drops the first step. Refuses variables that already look like rates.
    """
    if "step" not in ds.dims:
        raise UsageError("deaccumulate requires a step dim")
    names = list(names) if names is not None else list(ds.data_vars)
    out = ds.isel(step=slice(1, None))
    delta_days = _step_delta_days(ds)
    for name in names:
        if name not in ds.data_vars:
            raise UsageError(f"variable {name!r} not in dataset (have {list(ds.data_vars)})")
        da = ds[name]
        units = variable_units(da) or da.attrs.get("units")
        std = da.attrs.get("standard_name")
        if (isinstance(std, str) and std.strip().lower().endswith(("_rate", "_flux"))) or (
            isinstance(units, str) and _RATE_RE.search(units)
        ):
            raise UsageError(f"'{name}' looks like a rate; refuse to deaccumulate")
        plain = da.pint.dequantify() if da.pint.units is not None else da
        src_units = plain.attrs.get("units") or units
        sliced = plain.isel(step=slice(1, None))
        diffs = np.clip(
            sliced.values - plain.isel(step=slice(0, -1)).values,
            a_min=0,
            a_max=None,
        )
        kind = classify_variable(name, units=src_units, standard_name=std)
        if kind is None and isinstance(src_units, str) and src_units.strip():
            kind = kind_from_units(src_units)
        attrs = dict(plain.attrs)
        if kind == "precip_amount":
            if "step" not in sliced.dims:
                raise UsageError(f"variable {name!r} has no step dim")
            mm, _ = convert_values(diffs, src_units, STANDARD["precip_amount"]["units"])
            rate = mm / _broadcast_along_step(delta_days, sliced.dims)
            diffed = sliced.copy(data=rate)
            attrs["units"] = STANDARD["precip"]["units"]
            attrs["standard_name"] = STANDARD["precip"]["standard_name"]
        else:
            diffed = sliced.copy(data=diffs)
        diffed.attrs = attrs
        out[name] = diffed
    origin = np.asarray(ds["step"].values)[0]
    return stamp_data_interval(out, dim="step", origin=origin)


def precip_amounts_to_rates(ds, *, interval=None):
    """Convert precip-amount data vars to ``mm day-1`` rates.

    Cumulative-since-init forecast amounts on ``step`` are deaccumulated
    (companion non-amount vars keep their values but share the shortened
    step axis). Otherwise amounts are divided by ``interval`` or
    stamped/inferred ``data_interval``. Already-rate precip is unchanged.
    """
    amount_names = []
    for name in ds.data_vars:
        da = ds[name]
        units = variable_units(da) or da.attrs.get("units")
        kind = classify_variable(name, units=units, standard_name=da.attrs.get("standard_name"))
        if kind == "precip_amount":
            amount_names.append(name)
    if not amount_names:
        return ds

    if "step" in ds.dims and ds.sizes["step"] >= 2:
        return deaccumulate_along_step(ds, names=amount_names)

    period = interval or data_interval_of(ds)
    if period is None:
        axis = _time_or_step_dim(ds)
        if axis is None:
            raise UsageError(
                "cannot convert precip amounts to rates: no data_interval and no time/step dim"
            )
        period = format_duration(infer_timestep(ds, axis))
    duration = parse_aggregation_period(period)
    out = ds.copy(deep=False)
    dirty = False
    for name in amount_names:
        da = ds[name]
        quantified_in = da.pint.units is not None
        qda = da if quantified_in else da.pint.quantify()
        rate = qda / duration
        try:
            rate = rate.pint.to(STANDARD["precip"]["units"])
        except (pint_xarray.pint.DimensionalityError, pint_xarray.errors.PintExceptionGroup):
            pass
        if not quantified_in:
            rate = rate.pint.dequantify()
        attrs = dict(rate.attrs)
        attrs["units"] = STANDARD["precip"]["units"]
        attrs["standard_name"] = STANDARD["precip"]["standard_name"]
        if not dirty:
            out = ds.copy()
            dirty = True
        out[name] = rate
        out[name].attrs = attrs
    return out


def precip_convertible_names(ds):
    """Data-var names classified as precip whose units convert to a rate or amount.

    Skips companions that inherit a precip ``standard_name`` or name hint but
    carry non-precip units (e.g. temperature in ``K``).
    """
    names = []
    for name in ds.data_vars:
        da = ds[name]
        units = variable_units(da) or da.attrs.get("units")
        kind = classify_variable(name, units=units, standard_name=da.attrs.get("standard_name"))
        if kind not in ("precip", "precip_amount"):
            continue
        if not isinstance(units, str) or not units.strip():
            continue
        if kind_from_units(units) in ("precip", "precip_amount"):
            names.append(name)
            continue
        if units_convertible(units, STANDARD["precip"]["units"]) or units_convertible(
            units, STANDARD["precip_amount"]["units"]
        ):
            names.append(name)
    return names
