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

# Data-variable attr: the time window each value spans, as a pint duration
# string (``"1 day"``, ``"1 dekad"``). Complements CF ``cell_methods`` (which
# says *how* values combined) and drives ``convert-to-totals`` (rate × period).
AGGREGATION_PERIOD_ATTR = "aggregation_period"

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
        "name_hints": ("t2m", "2m_temperature", "temp", "tmax", "tmin", "tavg"),
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

    def _from_int_diffs(diffs, unit: str):
        positive = diffs[diffs > 0]
        if positive.size == 0:
            raise UsageError(f"no positive spacings on {dim!r}")
        return ureg.Quantity(float(np.median(positive)), unit).to("day")

    # Use the array's native datetime/timedelta unit. Casting timedelta64[us] to
    # datetime64[ns] "succeeds" but treats µs counts as ns (7 day → 0.007 day).
    if np.issubdtype(values.dtype, np.timedelta64) or np.issubdtype(
        values.dtype, np.datetime64
    ):
        unit, _ = np.datetime_data(values.dtype)
        diffs = np.diff(values.astype(values.dtype).astype(np.int64))
        return _from_int_diffs(diffs, unit)

    failure = None
    for dtype in ("datetime64[ns]", "timedelta64[ns]"):
        try:
            diffs = np.diff(values.astype(dtype).astype(np.int64))
        except (TypeError, ValueError) as exc:
            failure = exc
            continue
        return _from_int_diffs(diffs, "nanosecond")
    raise UsageError(f"could not infer timestep on {dim!r}: {failure}")


def assert_timestep_ge_aggregation_period(ds, dim: str, period: str) -> None:
    """Refuse convert-to-totals when sample spacing is finer than aggregation_period.

    A singleton axis is allowed: spacing cannot be inferred, and one sample
    cannot be an overlapping series. Typical after aggregating a short fetch
    to a single weekly/dekadal/monthly bin.
    """
    if dim not in ds.dims and dim not in ds.coords:
        raise UsageError(f"dimension/coord {dim!r} not in dataset")
    if ds.sizes.get(dim, 0) < 2:
        return
    dt = infer_timestep(ds, dim)
    base = parse_aggregation_period(period)
    if dt < base:
        raise UsageError(
            f"timestep on {dim!r} ({format_duration(dt)}) is smaller than "
            f"aggregation_period {period!r}; refusing convert-to-totals "
            "(overlapping/rolling windows would overcount). "
            f"Run aggregate-temporal --period {period!r} onto non-overlapping "
            "bins first, then convert-to-totals."
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
        total = total.pint.to(STANDARD["precip_amount"]["units"])
    except (pint_xarray.pint.DimensionalityError, pint_xarray.errors.PintExceptionGroup):
        pass
    return total


def quantify_dataset(ds, *, allow_precip_totals: bool = False):
    """Attach pint units to data vars; leave unitless vars and coords alone.

    Variables whose kind has ``units_required`` in ``STANDARD`` (known kinds
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
        if (
            not allow_precip_totals
            and kind in ("precip", "precip_amount")
            and (
                kind == "precip_amount"
                or (has_units and kind_from_units(units) == "precip_amount")
                or cell_methods_has_sum(da.attrs.get("cell_methods"))
            )
        ):
            raise UsageError(
                f"variable {name!r} looks like a precip total "
                "(amount units or cell_methods sum); most skills expect rates — "
                "convert with convert-to-totals / rate_to_total, or set "
                "allow_precip_totals=True if this skill handles amounts"
            )
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


def stamp_precip_amounts(ds):
    """Stamp amount CF metadata when units are precip depth/mass (overwrite rate names)."""
    amount_sn = STANDARD["precip_amount"]["standard_name"]
    for name in ds.data_vars:
        units = ds[name].attrs.get("units")
        if not isinstance(units, str):
            continue
        if kind_from_units(units) == "precip_amount":
            ds[name].attrs["standard_name"] = amount_sn
            ds[name].attrs.setdefault("long_name", "Total precipitation")
    return ds
