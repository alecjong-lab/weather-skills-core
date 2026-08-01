"""Units helpers: equivalence, conversion, quantify, and standard display units.

On skill input, ``quantify_dataset`` attaches pint units to data variables.
Temp / precip_rate variables must have a ``units`` attr; other variables without
units pass through. Precip **totals** (amount units or ``cell_methods`` with
``sum``) are refused so rate-path skills stay rates-only, unless
``allow_precip_totals=True`` (plotters / ``deaccumulate``). The decorator
dequantifies before writing Zarr.

Standard display targets (rate path / ``unit-convert --to-standard``):

- temp → ``degree_Celsius`` (keeps existing ``standard_name``)
- precip rate / flux → ``mm day-1`` (``lwe_precipitation_rate``)

Amount units (``mm``) are produced only by the ``convert-to-totals`` skill.
Mass precip flux (``kg m-2 s-1``) converts to depth rate via liquid-water
density (1000 kg m-3). Uses ``pint-xarray`` with CF/UDUNITS via ``cf_xarray.units``.
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

AGGREGATION_PERIOD_ATTR = "aggregation_period"

# Period label → pint duration string stamped as aggregation_period.
PERIOD_TO_AGGREGATION = {
    "daily": "1 day",
    "weekly": "7 day",
    "dekadal": "1 dekad",
    "monthly": "1 month",
}

# Kinds that must carry parseable units when a dataset is quantified on input.
REQUIRED_UNIT_KINDS = frozenset({"temp", "precip_rate"})

# Kind → stamped units / standard_name (None = leave existing standard_name).
STANDARD = {
    "temp": {"units": "degree_Celsius", "standard_name": None},
    "precip_rate": {
        "units": "mm day-1",
        "standard_name": "lwe_precipitation_rate",
    },
    # Amount metadata for convert-to-totals output (not a rate-path standard target).
    "precip_amount": {
        "units": "mm",
        "standard_name": "lwe_thickness_of_precipitation_amount",
    },
}

# Public aliases used by skills / tests.
TEMP_UNITS = STANDARD["temp"]["units"]
PRECIP_RATE_UNITS = STANDARD["precip_rate"]["units"]
PRECIP_AMOUNT_UNITS = STANDARD["precip_amount"]["units"]
PRECIP_RATE_STANDARD_NAME = STANDARD["precip_rate"]["standard_name"]
PRECIP_AMOUNT_STANDARD_NAME = STANDARD["precip_amount"]["standard_name"]

TEMP_STANDARD_NAMES = frozenset(
    {
        "air_temperature",
        "surface_temperature",
        "sea_surface_temperature",
        "dew_point_temperature",
        "dew_point_temperature_difference",
    }
)
PRECIP_RATE_STANDARD_NAMES = frozenset(
    {
        "lwe_precipitation_rate",
        "precipitation_flux",
        "rainfall_rate",
        "rainfall_flux",
    }
)
PRECIP_AMOUNT_STANDARD_NAMES = frozenset(
    {
        "lwe_thickness_of_precipitation_amount",
        "precipitation_amount",
        "thickness_of_rainfall_amount",
        "lwe_thickness_of_rainfall_amount",
    }
)

TEMP_NAME_HINTS = ("temp", "t2m", "tmax", "tmin", "tavg", "sst", "skt")
PRECIP_NAME_HINTS = ("precip", "prcp", "rainfall", "rain", "tp", "pr")

TEMP_UNIT_CANDIDATES = ("degree_Celsius", "kelvin", "degree_Fahrenheit")
PRECIP_RATE_UNIT_CANDIDATES = (
    "mm day-1",
    "mm/day",
    "m s-1",
    "kg m-2 s-1",
    "kg m**-2 s**-1",
)
PRECIP_AMOUNT_UNIT_CANDIDATES = ("mm", "m", "kg m-2", "kg m**-2")
# Depth/rate targets: mass units match these after ÷ liquid-water density.
PRECIP_RATE_DENSITY_CANDIDATES = ("m s-1", "mm day-1")
PRECIP_AMOUNT_DENSITY_CANDIDATES = ("m", "mm")

_CELL_METHOD_SUM_RE = re.compile(r":\s*sum\b", re.IGNORECASE)


def water_density():
    """Liquid-water density as a pint quantity (1000 kg m-3)."""
    return ureg.Quantity(1000.0, "kg m-3")


def units_match(units: str, candidates) -> bool:
    """True if ``units`` is pint-equivalent to any string in ``candidates``."""
    try:
        u = ureg.Unit(units)
    except Exception:  # noqa: BLE001 — invalid unit string is a non-match
        return False
    for cand in candidates:
        try:
            if u == ureg.Unit(cand):
                return True
        except Exception:  # noqa: BLE001, S112 — skip unparseable candidates
            continue
    return False


def units_match_density_converted(units: str, candidates) -> bool:
    """True if ``units / water_density`` is compatible with any candidate (mass → depth)."""
    try:
        as_depth = ureg.Quantity(1.0, units) / water_density()
    except Exception:  # noqa: BLE001 — invalid unit string is a non-match
        return False
    for cand in candidates:
        try:
            if as_depth.units.is_compatible_with(ureg.Unit(cand)):
                return True
        except Exception:  # noqa: BLE001, S112 — skip unparseable candidates
            continue
    return False


def kind_from_units(units: str) -> str | None:
    """Fingerprint units as ``temp``, ``precip_rate``, ``precip_amount``, or None."""
    if units_match(units, TEMP_UNIT_CANDIDATES):
        return "temp"
    # Prefer rate over amount when both could match (flux is rate-like).
    if units_match(units, PRECIP_RATE_UNIT_CANDIDATES) or units_match_density_converted(
        units, PRECIP_RATE_DENSITY_CANDIDATES
    ):
        return "precip_rate"
    if units_match(units, PRECIP_AMOUNT_UNIT_CANDIDATES) or units_match_density_converted(
        units, PRECIP_AMOUNT_DENSITY_CANDIDATES
    ):
        return "precip_amount"
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


def cell_methods_has_sum(cell_methods) -> bool:
    """True if CF ``cell_methods`` includes a ``sum`` method."""
    if not isinstance(cell_methods, str) or not cell_methods.strip():
        return False
    return bool(_CELL_METHOD_SUM_RE.search(cell_methods))


def format_cell_methods(dim: str, method: str, *, interval: str | None = None) -> str:
    """Build a CF ``cell_methods`` string, optionally with ``(interval: ...)``."""
    base = f"{dim}: {method}"
    if interval is None or not str(interval).strip():
        return base
    return f"{base} (interval: {str(interval).strip()})"


def parse_aggregation_period(period: str):
    """Parse an ``aggregation_period`` string to a pint duration Quantity."""
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
    """Format a pint duration for ``interval:`` / human-readable stamps."""
    try:
        as_day = q.to("day")
        mag = float(as_day.magnitude)
        if mag.is_integer():
            return f"{int(mag)} day"
    except (AttributeError, TypeError, ValueError):
        return f"{q:~P}"
    return f"{q:~P}"


def infer_timestep(ds, dim: str):
    """Median positive spacing along ``dim`` as a pint duration Quantity."""
    if dim not in ds.dims and dim not in ds.coords:
        raise UsageError(f"dimension/coord {dim!r} not in dataset")
    values = np.asarray(ds[dim].values)
    if values.size < 2:
        raise UsageError(f"need at least 2 points on {dim!r} to infer timestep")
    # Timedelta / datetime diffs.
    try:
        diffs = np.diff(values.astype("datetime64[ns]").astype(np.int64))
        positive = diffs[diffs > 0]
        if positive.size == 0:
            raise UsageError(f"no positive spacings on {dim!r}")
        median_ns = float(np.median(positive))
        return ureg.Quantity(median_ns, "nanosecond").to("day")
    except (TypeError, ValueError):
        pass
    # Timedelta64 step axis.
    try:
        diffs = np.diff(values.astype("timedelta64[ns]").astype(np.int64))
        positive = diffs[diffs > 0]
        if positive.size == 0:
            raise UsageError(f"no positive spacings on {dim!r}")
        median_ns = float(np.median(positive))
        return ureg.Quantity(median_ns, "nanosecond").to("day")
    except (TypeError, ValueError) as exc:
        raise UsageError(f"could not infer timestep on {dim!r}: {exc}") from None


def assert_timestep_ge_aggregation_period(ds, dim: str, period: str) -> None:
    """Refuse convert-to-totals when sample spacing is finer than aggregation_period."""
    dt = infer_timestep(ds, dim)
    base = parse_aggregation_period(period)
    if dt < base:
        raise UsageError(
            f"timestep on {dim!r} ({format_duration(dt)}) is smaller than "
            f"aggregation_period {period!r}; refusing convert-to-totals "
            "(overlapping/rolling windows would overcount)"
        )


def rate_to_total(da, period: str):
    """Multiply a rate DataArray by ``aggregation_period`` → amount (quantified)."""
    base = parse_aggregation_period(period)
    quantified_in = da.pint.units is not None
    qda = da if quantified_in else da.pint.quantify()
    if qda.pint.units is None:
        raise UsageError(f"variable {da.name!r} has no units to convert to totals")
    total = qda * base
    # Prefer mm for precip depth rates.
    try:
        total = total.pint.to(PRECIP_AMOUNT_UNITS)
    except (pint_xarray.pint.DimensionalityError, pint_xarray.errors.PintExceptionGroup):
        pass
    return total


def quantify_dataset(ds, *, allow_precip_totals: bool = False):
    """Attach pint units to data vars; leave unitless vars and coords alone.

    Temp / precip_rate variables (see ``REQUIRED_UNIT_KINDS``) must have a
    non-empty ``units`` attr. Unless ``allow_precip_totals`` is True, precip
    totals (amount units or ``cell_methods`` with ``sum``) are refused so
    rate-path skills stay rates-only. Coordinate ``units`` attrs are kept as
    attrs.
    """
    for name, da in ds.data_vars.items():
        units = da.attrs.get("units")
        kind = classify_variable(
            name, units=units, standard_name=da.attrs.get("standard_name")
        )
        cm = da.attrs.get("cell_methods")
        precip_kind = kind
        if precip_kind is None and isinstance(units, str) and units.strip():
            precip_kind = kind_from_units(units)
        if (
            not allow_precip_totals
            and precip_kind in ("precip_rate", "precip_amount")
            and (precip_kind == "precip_amount" or cell_methods_has_sum(cm))
        ):
            raise UsageError(
                f"variable {name!r} looks like a precip total "
                "(amount units or cell_methods sum); rate-path skills refuse totals"
            )
        if kind not in REQUIRED_UNIT_KINDS:
            continue
        if not (isinstance(units, str) and units.strip()):
            raise UsageError(
                f"variable {name!r} ({kind}) requires a units attribute to quantify"
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


def classify_variable(name: str, *, units=None, standard_name=None) -> str | None:
    """Return ``temp``, ``precip_rate``, ``precip_amount``, or None.

    Precedence: CF ``standard_name``, then units fingerprint, then name hints.
    Precip name hints default to ``precip_rate`` (amount vs rate is decided by
    ``standard_name`` / units earlier).
    """
    sn = standard_name.strip().lower() if isinstance(standard_name, str) else ""
    if sn in TEMP_STANDARD_NAMES or sn.endswith("_temperature"):
        return "temp"
    if sn in PRECIP_RATE_STANDARD_NAMES or sn.endswith(("_precipitation_rate", "_rainfall_rate")):
        return "precip_rate"
    if (
        sn in PRECIP_AMOUNT_STANDARD_NAMES
        or "precipitation_amount" in sn
        or "rainfall_amount" in sn
    ):
        return "precip_amount"

    if isinstance(units, str) and units.strip():
        kind = kind_from_units(units)
        if kind is not None:
            return kind

    key = name.lower()
    if any(h == key or key.startswith(h) or key.endswith(h) for h in TEMP_NAME_HINTS):
        return "temp"
    if any(h == key or key.startswith(h) or key.endswith(h) for h in PRECIP_NAME_HINTS) or any(
        h in key for h in ("precip", "rainfall")
    ):
        return "precip_rate"
    return None


def to_standard_units(ds, variables=None):
    """Convert recognized temp/precip_rate data vars to standard display units.

    Rate-path only: ``precip_amount`` is left unchanged (use convert-to-totals).
    Unrecognized or unitless variables are left unchanged. Raises ``UsageError``
    if a classified rate/temp variable cannot be converted.
    """
    names = list(variables) if variables is not None else list(ds.data_vars)
    out = ds
    dirty = False
    for name in names:
        if name not in ds.data_vars:
            raise UsageError(f"variable {name!r} not in dataset (have {list(ds.data_vars)})")
        da = ds[name]
        units = variable_units(da)
        kind = classify_variable(
            name, units=units, standard_name=da.attrs.get("standard_name")
        )
        if kind is None or units is None or kind == "precip_amount":
            continue
        if kind not in STANDARD:
            continue
        target = STANDARD[kind]
        dst_units = target["units"]
        dst_sn = target["standard_name"]
        quantified = da.pint.units is not None
        if units_equal(units, dst_units):
            if not dirty:
                out = ds.copy()
                dirty = True
            if quantified:
                converted, _ = convert_dataarray(da, dst_units)
                attrs = {**converted.attrs}
                if dst_sn:
                    attrs["standard_name"] = dst_sn
                out[name] = converted
                out[name].attrs = attrs
            else:
                attrs = {**out[name].attrs, "units": dst_units}
                if dst_sn:
                    attrs["standard_name"] = dst_sn
                out[name].attrs = attrs
            continue
        converted, _ = convert_dataarray(da, dst_units)
        if not dirty:
            out = ds.copy()
            dirty = True
        attrs = {**converted.attrs}
        if dst_sn:
            attrs["standard_name"] = dst_sn
        out[name] = converted
        out[name].attrs = attrs
    return out
