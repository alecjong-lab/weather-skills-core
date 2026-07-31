"""Units helpers: equivalence, conversion, and standard display units.

Standard display units (for fetchers/plots / ``unit-convert --to-standard``):

- temperature → ``degree_Celsius``
- precipitation rate / flux → ``mm day-1`` (``lwe_precipitation_rate``)
- precipitation amount / depth → ``mm`` (``lwe_thickness_of_precipitation_amount``)

Incoming CF-compliant datasets are accepted as-is; these helpers normalize
common temp/precip variables when asked.
"""

from __future__ import annotations

from weather_skills_core.errors import UsageError

# Written forms we stamp after conversion.
TEMP_UNITS = "degree_Celsius"
PRECIP_RATE_UNITS = "mm day-1"
PRECIP_AMOUNT_UNITS = "mm"
PRECIP_RATE_STANDARD_NAME = "lwe_precipitation_rate"
PRECIP_AMOUNT_STANDARD_NAME = "lwe_thickness_of_precipitation_amount"

_TEMP_STANDARD_NAMES = frozenset(
    {
        "air_temperature",
        "surface_temperature",
        "sea_surface_temperature",
        "dew_point_temperature",
        "dew_point_temperature_difference",
    }
)
_PRECIP_RATE_STANDARD_NAMES = frozenset(
    {
        "lwe_precipitation_rate",
        "precipitation_flux",
        "rainfall_rate",
        "rainfall_flux",
    }
)
_PRECIP_AMOUNT_STANDARD_NAMES = frozenset(
    {
        "lwe_thickness_of_precipitation_amount",
        "precipitation_amount",
        "thickness_of_rainfall_amount",
        "lwe_thickness_of_rainfall_amount",
    }
)
_TEMP_NAME_HINTS = ("temp", "t2m", "tmax", "tmin", "tavg", "sst", "skt")
_PRECIP_NAME_HINTS = ("precip", "prcp", "rainfall", "rain", "tp", "pr")


def _cf_units():
    try:
        import cf_units
    except ImportError as exc:
        raise UsageError(
            "cf-units is required for units conversion; install weather-skills-core[units]"
        ) from exc
    return cf_units


def units_equal(a, b) -> bool:
    """True if both strings are udunits-equivalent (spelling-independent)."""
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    a, b = a.strip(), b.strip()
    if not a or not b:
        return False
    if a == b:
        return True
    cf_units = _cf_units()
    try:
        return bool(cf_units.Unit(a) == cf_units.Unit(b))
    except ValueError:
        return False


def convert_values(values, src_units: str, dst_units: str):
    """Convert array values with cf-units; liquid-water density bridge on dim mismatch.

    Returns ``(converted_array, dimensional_bridge_used)``.
    """
    import numpy as np

    cf_units = _cf_units()
    src = cf_units.Unit(src_units)
    dst = cf_units.Unit(dst_units)
    arr = np.asarray(values)
    try:
        return src.convert(arr, dst), False
    except ValueError:
        pass
    # 1000 kg m-3: 1 kg m-2 liquid water ≡ 1 mm depth (and same for rates).
    rho = cf_units.Unit("kg m-3") * 1000.0
    for bridged in (src / rho, src * rho):
        try:
            return bridged.convert(arr, dst), True
        except (ValueError, TypeError):
            continue
    raise UsageError(f"{src_units!r} not convertible to {dst_units!r}")


def _looks_temp_units(units: str) -> bool:
    cf_units = _cf_units()
    try:
        u = cf_units.Unit(units)
    except ValueError:
        return False
    for cand in ("degree_Celsius", "kelvin", "degree_Fahrenheit"):
        try:
            if u == cf_units.Unit(cand):
                return True
        except ValueError:
            continue
    return False


def _looks_precip_rate_units(units: str) -> bool:
    cf_units = _cf_units()
    try:
        u = cf_units.Unit(units)
    except ValueError:
        return False
    for cand in ("mm day-1", "mm/day", "m s-1", "kg m-2 s-1", "kg m**-2 s**-1"):
        try:
            if u == cf_units.Unit(cand):
                return True
        except ValueError:
            continue
    # Density-bridged flux ↔ rate
    try:
        bridged = u / (cf_units.Unit("kg m-3") * 1000.0)
        if bridged == cf_units.Unit("m s-1") or bridged == cf_units.Unit("mm day-1"):
            return True
    except (ValueError, TypeError):
        pass
    return False


def _looks_precip_amount_units(units: str) -> bool:
    cf_units = _cf_units()
    try:
        u = cf_units.Unit(units)
    except ValueError:
        return False
    for cand in ("mm", "m", "kg m-2", "kg m**-2"):
        try:
            if u == cf_units.Unit(cand):
                return True
        except ValueError:
            continue
    try:
        bridged = u / (cf_units.Unit("kg m-3") * 1000.0)
        if bridged == cf_units.Unit("m") or bridged == cf_units.Unit("mm"):
            return True
    except (ValueError, TypeError):
        pass
    return False


def classify_variable(name: str, *, units=None, standard_name=None) -> str | None:
    """Return ``temperature``, ``precip_rate``, ``precip_amount``, or None."""
    sn = standard_name.strip().lower() if isinstance(standard_name, str) else ""
    if sn in _TEMP_STANDARD_NAMES or sn.endswith("_temperature"):
        return "temperature"
    if sn in _PRECIP_RATE_STANDARD_NAMES or sn.endswith("_precipitation_rate") or sn.endswith(
        "_rainfall_rate"
    ):
        return "precip_rate"
    if sn in _PRECIP_AMOUNT_STANDARD_NAMES or "precipitation_amount" in sn or "rainfall_amount" in sn:
        return "precip_amount"

    if isinstance(units, str) and units.strip():
        if _looks_temp_units(units):
            return "temperature"
        # Prefer rate over amount when both could match (flux is rate-like).
        if _looks_precip_rate_units(units):
            return "precip_rate"
        if _looks_precip_amount_units(units):
            return "precip_amount"

    key = name.lower()
    if any(h == key or key.startswith(h) or key.endswith(h) for h in _TEMP_NAME_HINTS):
        return "temperature"
    if key in {"tp", "pr", "precip", "prcp", "rain", "rainfall"} or any(
        h in key for h in ("precip", "rainfall")
    ):
        # Ambiguous name: amount if units look like depth, else rate.
        if isinstance(units, str) and _looks_precip_amount_units(units) and not _looks_precip_rate_units(
            units
        ):
            return "precip_amount"
        return "precip_rate"
    return None


def _target_for(kind: str) -> tuple[str, str]:
    if kind == "temperature":
        return TEMP_UNITS, ""  # keep existing standard_name
    if kind == "precip_rate":
        return PRECIP_RATE_UNITS, PRECIP_RATE_STANDARD_NAME
    if kind == "precip_amount":
        return PRECIP_AMOUNT_UNITS, PRECIP_AMOUNT_STANDARD_NAME
    raise ValueError(kind)


def to_standard_units(ds, variables=None):
    """Convert recognized temp/precip data vars to standard display units.

    Unrecognized variables are left unchanged. ``variables`` limits which names
    to consider (default: all data vars).
    """
    names = list(variables) if variables is not None else list(ds.data_vars)
    out = ds
    dirty = False
    for name in names:
        if name not in ds.data_vars:
            raise UsageError(f"variable {name!r} not in dataset (have {list(ds.data_vars)})")
        da = ds[name]
        units = da.attrs.get("units")
        kind = classify_variable(
            name, units=units, standard_name=da.attrs.get("standard_name")
        )
        if kind is None:
            continue
        if not (isinstance(units, str) and units.strip()):
            continue
        dst_units, dst_sn = _target_for(kind)
        if units_equal(units, dst_units):
            # Already standard; normalize spelling + standard_name.
            if not dirty:
                out = ds.copy()
                dirty = True
            attrs = {**out[name].attrs, "units": dst_units}
            if dst_sn:
                attrs["standard_name"] = dst_sn
            out[name].attrs = attrs
            continue
        try:
            converted, _ = convert_values(da.values, units, dst_units)
        except UsageError:
            continue
        if not dirty:
            out = ds.copy()
            dirty = True
        attrs = {**da.attrs, "units": dst_units}
        if dst_sn:
            attrs["standard_name"] = dst_sn
        elif kind == "temperature":
            # Keep existing temperature standard_name if present.
            pass
        out[name] = da.copy(data=converted)
        out[name].attrs = attrs
    return out
