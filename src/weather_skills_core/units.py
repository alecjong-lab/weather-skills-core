"""Units helpers: equivalence, conversion, and standard display units.

Standard display targets (fetchers / plots / ``unit-convert --to-standard``):

- temperature → ``degree_Celsius`` (keeps existing ``standard_name``)
- precip rate / flux → ``mm day-1`` (``lwe_precipitation_rate``)
- precip amount / depth → ``mm`` (``lwe_thickness_of_precipitation_amount``)

``classify_variable`` precedence: CF ``standard_name``, then units fingerprint,
then variable-name hints. Liquid-water density (1000 kg m-3) bridges
mass-flux/depth ↔ rate/depth when dimensions differ. Requires the optional
``weather-skills-core[units]`` extra (``cf-units``).
"""

from __future__ import annotations

from weather_skills_core.errors import UsageError

# Kind → stamped units / standard_name (None = leave existing standard_name).
STANDARD = {
    "temperature": {"units": "degree_Celsius", "standard_name": None},
    "precip_rate": {
        "units": "mm day-1",
        "standard_name": "lwe_precipitation_rate",
    },
    "precip_amount": {
        "units": "mm",
        "standard_name": "lwe_thickness_of_precipitation_amount",
    },
}

# Public aliases used by skills / tests.
TEMP_UNITS = STANDARD["temperature"]["units"]
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
PRECIP_RATE_BRIDGED_CANDIDATES = ("m s-1", "mm day-1")
PRECIP_AMOUNT_BRIDGED_CANDIDATES = ("m", "mm")


def require_cf_units():
    """Import ``cf_units`` or raise UsageError pointing at the ``[units]`` extra."""
    try:
        import cf_units
    except ImportError as exc:
        raise UsageError(
            "cf-units is required for units conversion; install weather-skills-core[units]"
        ) from exc
    return cf_units


def water_density():
    """Liquid-water density as a cf-units quantity (1000 kg m-3)."""
    cf_units = require_cf_units()
    return cf_units.Unit("kg m-3") * 1000.0


def units_match(units: str, candidates) -> bool:
    """True if ``units`` is udunits-equivalent to any string in ``candidates``."""
    cf_units = require_cf_units()
    try:
        u = cf_units.Unit(units)
    except ValueError:
        return False
    for cand in candidates:
        try:
            if u == cf_units.Unit(cand):
                return True
        except ValueError:
            continue
    return False


def units_match_bridged(units: str, candidates) -> bool:
    """True if ``units / water_density`` matches any candidate (mass ↔ depth)."""
    cf_units = require_cf_units()
    try:
        bridged = cf_units.Unit(units) / water_density()
    except (ValueError, TypeError):
        return False
    for cand in candidates:
        try:
            if bridged == cf_units.Unit(cand):
                return True
        except ValueError:
            continue
    return False


def kind_from_units(units: str) -> str | None:
    """Fingerprint units as ``temperature``, ``precip_rate``, ``precip_amount``, or None."""
    if units_match(units, TEMP_UNIT_CANDIDATES):
        return "temperature"
    # Prefer rate over amount when both could match (flux is rate-like).
    if units_match(units, PRECIP_RATE_UNIT_CANDIDATES) or units_match_bridged(
        units, PRECIP_RATE_BRIDGED_CANDIDATES
    ):
        return "precip_rate"
    if units_match(units, PRECIP_AMOUNT_UNIT_CANDIDATES) or units_match_bridged(
        units, PRECIP_AMOUNT_BRIDGED_CANDIDATES
    ):
        return "precip_amount"
    return None


def units_equal(a, b) -> bool:
    """True if both strings are udunits-equivalent (spelling-independent)."""
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    a, b = a.strip(), b.strip()
    if not a or not b:
        return False
    if a == b:
        return True
    cf_units = require_cf_units()
    try:
        return bool(cf_units.Unit(a) == cf_units.Unit(b))
    except ValueError:
        return False


def convert_values(values, src_units: str, dst_units: str):
    """Convert array values with cf-units; liquid-water density bridge on dim mismatch.

    Returns ``(converted_array, dimensional_bridge_used)``.
    """
    import numpy as np

    cf_units = require_cf_units()
    src = cf_units.Unit(src_units)
    dst = cf_units.Unit(dst_units)
    arr = np.asarray(values)
    try:
        return src.convert(arr, dst), False
    except ValueError:
        pass
    rho = water_density()
    for bridged in (src / rho, src * rho):
        try:
            return bridged.convert(arr, dst), True
        except (ValueError, TypeError):
            continue
    raise UsageError(f"{src_units!r} not convertible to {dst_units!r}")


def classify_variable(name: str, *, units=None, standard_name=None) -> str | None:
    """Return ``temperature``, ``precip_rate``, ``precip_amount``, or None.

    Precedence: CF ``standard_name``, then units fingerprint, then name hints.
    Precip name hints default to ``precip_rate`` (amount vs rate is decided by
    ``standard_name`` / units earlier).
    """
    sn = standard_name.strip().lower() if isinstance(standard_name, str) else ""
    if sn in TEMP_STANDARD_NAMES or sn.endswith("_temperature"):
        return "temperature"
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
        return "temperature"
    if any(h == key or key.startswith(h) or key.endswith(h) for h in PRECIP_NAME_HINTS) or any(
        h in key for h in ("precip", "rainfall")
    ):
        return "precip_rate"
    return None


def to_standard_units(ds, variables=None):
    """Convert recognized temp/precip data vars to standard display units.

    Unrecognized or unitless variables are left unchanged. ``variables`` limits
    which names to consider (default: all data vars). Raises ``UsageError`` if a
    classified variable cannot be converted.
    """
    names = list(variables) if variables is not None else list(ds.data_vars)
    out = ds
    dirty = False
    for name in names:
        if name not in ds.data_vars:
            raise UsageError(f"variable {name!r} not in dataset (have {list(ds.data_vars)})")
        da = ds[name]
        units = da.attrs.get("units")
        kind = classify_variable(name, units=units, standard_name=da.attrs.get("standard_name"))
        if kind is None:
            continue
        if not (isinstance(units, str) and units.strip()):
            continue
        target = STANDARD[kind]
        dst_units = target["units"]
        dst_sn = target["standard_name"]
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
        converted, _ = convert_values(da.values, units, dst_units)
        if not dirty:
            out = ds.copy()
            dirty = True
        attrs = {**da.attrs, "units": dst_units}
        if dst_sn:
            attrs["standard_name"] = dst_sn
        out[name] = da.copy(data=converted)
        out[name].attrs = attrs
    return out
