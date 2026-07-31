"""Tests for units equivalence and standard display conversion."""

import numpy as np
import pytest
from conftest import make_gridded

from weather_skills_core.errors import UsageError
from weather_skills_core.units import (
    PRECIP_AMOUNT_UNITS,
    PRECIP_RATE_UNITS,
    TEMP_UNITS,
    classify_variable,
    convert_values,
    dequantify_dataset,
    quantify_dataset,
    to_standard_units,
    units_equal,
)


def test_units_equal_spelling():
    assert units_equal("mm/day", "mm day-1")
    assert units_equal("degC", "degree_Celsius")
    assert not units_equal("mm", "mm day-1")


def test_convert_values_temp_and_precip_density():
    k, _ = convert_values(np.array([273.15]), "K", TEMP_UNITS)
    np.testing.assert_allclose(k, [0.0], atol=1e-6)
    mm, density_converted = convert_values(np.array([1.0]), "kg m-2", PRECIP_AMOUNT_UNITS)
    assert density_converted
    np.testing.assert_allclose(mm, [1.0], atol=1e-6)
    rate, density_converted = convert_values(
        np.array([1e-3]), "kg m-2 s-1", PRECIP_RATE_UNITS
    )
    assert density_converted
    # 1e-3 kg m-2 s-1 / (1000 kg m-3) → 1e-3 mm/s ≡ 86.4 mm/day
    np.testing.assert_allclose(rate, [86.4], rtol=1e-5)


def test_classify_variable():
    assert classify_variable("t2m", units="K", standard_name="air_temperature") == "temp"
    assert classify_variable("tp", units="kg m-2", standard_name="precipitation_amount") == (
        "precip_amount"
    )
    assert classify_variable("precip", units="mm/day") == "precip_rate"
    assert classify_variable("flux", units="kg m-2 s-1") == "precip_rate"
    assert classify_variable("humidity", units="1") is None


def test_to_standard_units_temp_and_amount():
    ds = make_gridded(name="tp", fill=2.0)
    ds["tp"].attrs.update(units="kg m-2", standard_name="precipitation_amount")
    out = to_standard_units(ds)
    assert out["tp"].attrs["units"] == PRECIP_AMOUNT_UNITS
    assert out["tp"].attrs["standard_name"] == "lwe_thickness_of_precipitation_amount"
    np.testing.assert_allclose(out["tp"].values, 2.0)

    tds = make_gridded(name="t2m", fill=300.0)
    tds["t2m"].attrs.update(units="K", standard_name="air_temperature")
    tout = to_standard_units(tds)
    assert tout["t2m"].attrs["units"] == TEMP_UNITS
    np.testing.assert_allclose(tout["t2m"].values, 300.0 - 273.15, rtol=1e-5)


def test_to_standard_units_noop_unknown():
    ds = make_gridded(name="humidity", fill=0.5)
    ds["humidity"].attrs["units"] = "1"
    out = to_standard_units(ds)
    assert out["humidity"].attrs["units"] == "1"


def test_to_standard_units_missing_variable():
    ds = make_gridded()
    with pytest.raises(UsageError, match="not in dataset"):
        to_standard_units(ds, variables=["nope"])


def test_to_standard_units_normalizes_already_standard_spelling():
    ds = make_gridded(name="precip", fill=1.0)
    ds["precip"].attrs.update(units="mm/day", standard_name="precipitation_flux")
    values_before = ds["precip"].values.copy()
    out = to_standard_units(ds)
    assert out["precip"].attrs["units"] == PRECIP_RATE_UNITS
    assert out["precip"].attrs["standard_name"] == "lwe_precipitation_rate"
    np.testing.assert_array_equal(out["precip"].values, values_before)


def test_to_standard_units_raises_when_classified_but_not_convertible():
    ds = make_gridded(name="t2m", fill=1.0)
    # Classified as temperature via standard_name, but units are not convertible to °C.
    ds["t2m"].attrs.update(units="m s-1", standard_name="air_temperature")
    with pytest.raises(UsageError, match="not convertible"):
        to_standard_units(ds)


def test_quantify_dataset_requires_units_for_temp_precip():
    ds = make_gridded(name="precip", units=None)
    with pytest.raises(UsageError, match="requires a units attribute"):
        quantify_dataset(ds)


def test_quantify_dataset_passes_unitless_other_vars():
    ds = make_gridded(name="humidity", units=None)
    ds["humidity"].attrs.pop("units", None)
    q = quantify_dataset(ds)
    assert q["humidity"].pint.units is None
    assert not hasattr(q["humidity"].data, "units")


def test_quantify_dataset_quantifies_and_preserves_coord_attrs():
    ds = make_gridded(name="precip", units="mm")
    ds["latitude"].attrs["units"] = "degrees_north"
    q = quantify_dataset(ds)
    assert q["precip"].pint.units is not None
    assert str(q["precip"].pint.units) in ("millimeter", "mm")
    assert q["latitude"].pint.units is None
    assert q["latitude"].attrs["units"] == "degrees_north"
    plain = dequantify_dataset(q)
    assert "units" in plain["precip"].attrs
