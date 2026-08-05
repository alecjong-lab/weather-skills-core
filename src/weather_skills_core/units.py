"""Physical units for skill datasets: equivalence, conversion, and quantification.

Skills carry real units on every data variable so datasets can be combined,
converted, and compared without guessing from a variable's name. Units live in
the CF ``units`` attr on disk and become pint quantities in memory
(``quantify_dataset`` on input, ``dequantify_dataset`` before writing Zarr).

A few known standard kinds (``STANDARD`` below) get explicit treatment: they
must carry parseable units and have a standard display unit skills convert to.
Everything else may include units optionally and is passed through untouched.
(Accumulated variables default to rates; see UNITS.md for that detail.)

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

AGGREGATION_PERIOD_ATTR = "aggregation_period"

# Period label → pint duration string stamped as aggregation_period.
PERIOD_TO_AGGREGATION = {
    "daily": "1 day",
    "weekly": "7 day",
    "dekadal": "1 dekad",
    "monthly": "1 month",
}

# Standard kinds that must carry parseable units so skills can treat them
# explicitly. Other variables may include units optionally.
REQUIRED_UNIT_KINDS = frozenset({"temp", "precip"})

# The vocabulary for every standard kind, in classification precedence order.
#   units / standard_name  what to_standard_units stamps (None keeps existing)
#   standard_names, standard_name_endswith, standard_name_contains
#                          CF standard_name matches, tried first
#   unit_candidates        units that fingerprint this kind (pint equality)
#   depth_candidates       matched after ÷ liquid-water density, so mass precip
#                          (kg m-2 s-1) counts as a depth rate
#   name_hints             variable-name whole/prefix/suffix match, tried last
#   name_contains          variable-name substring match, tried last
STANDARD = {
    "temp": {
        "units": "degree_Celsius",
        "standard_name": None,
        "standard_names": frozenset(
            {
                "air_temperature",
                "surface_temperature",
                "sea_surface_temperature",
                "dew_point_temperature",
                "dew_point_temperature_difference",
            }
        ),
        "standard_name_endswith": ("_temperature",),
        "standard_name_contains": (),
        "unit_candidates": ("degree_Celsius", "kelvin", "degree_Fahrenheit"),
        "depth_candidates": (),
        "name_hints": ("temp", "t2m", "tmax", "tmin", "tavg", "sst", "skt"),
        "name_contains": (),
    },
    # Listed before precip_amount so flux-like units resolve to a rate.
    "precip": {
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
        "name_contains": ("precip", "rainfall"),
    },
    # Amount metadata for the totals utilities (rate × period → mm).
    "precip_amount": {
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
        "name_contains": (),
    },
}

# Public aliases used by skills / tests.
TEMP_UNITS = STANDARD["temp"]["units"]
PRECIP_UNITS = STANDARD["precip"]["units"]
PRECIP_AMOUNT_UNITS = STANDARD["precip_amount"]["units"]
PRECIP_STANDARD_NAME = STANDARD["precip"]["standard_name"]
PRECIP_AMOUNT_STANDARD_NAME = STANDARD["precip_amount"]["standard_name"]

_CELL_METHOD_SUM_RE = re.compile(r":\s*sum\b", re.IGNORECASE)


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

    Precedence: CF ``standard_name``, then the units fingerprint, then
    variable-name hints. Name hints never resolve to ``precip_amount`` — rate
    vs amount is decided by ``standard_name`` or units.
    """
    sn = standard_name.strip().lower() if isinstance(standard_name, str) else ""
    for kind, spec in STANDARD.items():
        if (
            sn in spec["standard_names"]
            or sn.endswith(spec["standard_name_endswith"])
            or any(part in sn for part in spec["standard_name_contains"])
        ):
            return kind

    if isinstance(units, str) and units.strip():
        kind = kind_from_units(units)
        if kind is not None:
            return kind

    key = name.lower()
    for kind, spec in STANDARD.items():
        hints = spec["name_hints"]
        if any(h == key or key.startswith(h) or key.endswith(h) for h in hints) or any(
            part in key for part in spec["name_contains"]
        ):
            return kind
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
    failure = None
    # Dates, then a timedelta64 step axis.
    for dtype in ("datetime64[ns]", "timedelta64[ns]"):
        try:
            diffs = np.diff(values.astype(dtype).astype(np.int64))
        except (TypeError, ValueError) as exc:
            failure = exc
            continue
        positive = diffs[diffs > 0]
        if positive.size == 0:
            raise UsageError(f"no positive spacings on {dim!r}")
        return ureg.Quantity(float(np.median(positive)), "nanosecond").to("day")
    raise UsageError(f"could not infer timestep on {dim!r}: {failure}")


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

    Variables classified into ``REQUIRED_UNIT_KINDS`` (known standard kinds
    skills treat explicitly) must have a non-empty ``units`` attr. Other
    variables may omit units. By default, precip totals (amount units or
    ``cell_methods`` with ``sum``) raise — most skills expect rates; set
    ``allow_precip_totals=True`` when the skill handles amounts. Coordinate
    ``units`` attrs are kept as attrs.
    """
    for name, da in ds.data_vars.items():
        units = da.attrs.get("units")
        has_units = isinstance(units, str) and bool(units.strip())
        kind = classify_variable(name, units=units, standard_name=da.attrs.get("standard_name"))
        precip_kind = kind
        if precip_kind is None and has_units:
            precip_kind = kind_from_units(units)
        if (
            not allow_precip_totals
            and precip_kind in ("precip", "precip_amount")
            and (
                precip_kind == "precip_amount" or cell_methods_has_sum(da.attrs.get("cell_methods"))
            )
        ):
            raise UsageError(
                f"variable {name!r} looks like a precip total "
                "(amount units or cell_methods sum); most skills expect rates — "
                "convert with convert-to-totals / rate_to_total, or set "
                "allow_precip_totals=True if this skill handles amounts"
            )
        if kind in REQUIRED_UNIT_KINDS and not has_units:
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
        kind = classify_variable(name, units=units, standard_name=da.attrs.get("standard_name"))
        if units is None or kind == "precip_amount" or kind not in STANDARD:
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
        out[name] = converted
        out[name].attrs = attrs
    return out
