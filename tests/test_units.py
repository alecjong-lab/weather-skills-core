"""Tests for units equivalence, quantify, aggregation_period, and standard conversion."""

import numpy as np
import pytest
import xarray as xr
from conftest import make_gridded

from weather_skills_core.errors import UsageError
from weather_skills_core.units import (
    AGGREGATION_PERIOD_ATTR,
    STANDARD,
    assert_timestep_ge_aggregation_period,
    classify_variable,
    convert_values,
    dequantify_dataset,
    format_cell_methods,
    parse_aggregation_period,
    quantify_dataset,
    rate_to_total,
    to_standard_units,
    units_equal,
    ureg,
)


def test_units_equal_spelling():
    assert units_equal("mm/day", "mm day-1")
    assert units_equal("degC", "degree_Celsius")
    assert not units_equal("mm", "mm day-1")


def test_pentad_dekad_registry():
    assert ureg.Quantity(1, "mm/pentad").to("mm/day").magnitude == pytest.approx(0.2)
    assert parse_aggregation_period("1 dekad").to("day").magnitude == pytest.approx(10.0)
    assert parse_aggregation_period("7 day").to("day").magnitude == pytest.approx(7.0)


def test_convert_values_temp_and_precip_density():
    k, _ = convert_values(np.array([273.15]), "K", STANDARD["temp"]["units"])
    np.testing.assert_allclose(k, [0.0], atol=1e-6)
    mm, density_converted = convert_values(
        np.array([1.0]), "kg m-2", STANDARD["precip_amount"]["units"]
    )
    assert density_converted
    np.testing.assert_allclose(mm, [1.0], atol=1e-6)
    rate, density_converted = convert_values(
        np.array([1e-3]), "kg m-2 s-1", STANDARD["precip"]["units"]
    )
    assert density_converted
    np.testing.assert_allclose(rate, [86.4], rtol=1e-5)


def test_classify_variable():
    assert classify_variable("t2m", units="K", standard_name="air_temperature") == "temp"
    assert classify_variable("2m_temperature", units="K") == "temp"
    assert classify_variable("tp", units="kg m-2", standard_name="precipitation_amount") == (
        "precip_amount"
    )
    assert classify_variable("precip", units="mm/day") == "precip"
    assert classify_variable("tp", units="kg m-2 s-1") == "precip"  # named hint
    assert classify_variable("flux", units="kg m-2 s-1") is None  # units alone are not enough
    assert classify_variable("humidity", units="1") is None
    # Not air / 2 m temperature
    assert classify_variable("sst", units="K", standard_name="sea_surface_temperature") is None
    assert classify_variable("d2m", units="K", standard_name="dew_point_temperature") is None
    assert classify_variable("skt", units="K", standard_name="surface_temperature") is None


def test_to_standard_units_name_hint_sets_standard_name_keeps_name():
    ds = make_gridded(name="tp", fill=1.0, units="mm/day")
    out = to_standard_units(ds)
    assert "tp" in out.data_vars
    assert list(out.data_vars) == ["tp"]
    assert out["tp"].attrs["units"] == STANDARD["precip"]["units"]
    assert out["tp"].attrs["standard_name"] == "lwe_precipitation_rate"


def test_to_standard_units_skips_amount_normalizes_rate_and_temp():
    # Amounts are not normalized on the rate path.
    ds = make_gridded(name="tp", fill=2.0, units=None)
    ds["tp"].attrs.update(units="kg m-2", standard_name="precipitation_amount")
    out = to_standard_units(ds)
    assert out["tp"].attrs["units"] == "kg m-2"

    tds = make_gridded(name="t2m", fill=300.0, units="K")
    tds["t2m"].attrs["standard_name"] = "air_temperature"
    tout = to_standard_units(tds)
    assert tout["t2m"].attrs["units"] == STANDARD["temp"]["units"]
    np.testing.assert_allclose(tout["t2m"].values, 300.0 - 273.15, rtol=1e-5)


def test_to_standard_units_noop_unknown():
    ds = make_gridded(name="humidity", fill=0.5, units="1")
    out = to_standard_units(ds)
    assert out["humidity"].attrs["units"] == "1"


def test_to_standard_units_missing_variable():
    ds = make_gridded()
    with pytest.raises(UsageError, match="not in dataset"):
        to_standard_units(ds, variables=["nope"])


def test_to_standard_units_normalizes_already_standard_spelling():
    ds = make_gridded(name="precip", fill=1.0, units="mm/day")
    ds["precip"].attrs["standard_name"] = "precipitation_flux"
    values_before = ds["precip"].values.copy()
    out = to_standard_units(ds)
    assert out["precip"].attrs["units"] == STANDARD["precip"]["units"]
    assert out["precip"].attrs["standard_name"] == "lwe_precipitation_rate"
    np.testing.assert_array_equal(out["precip"].values, values_before)


def test_to_standard_units_raises_when_classified_but_not_convertible():
    ds = make_gridded(name="t2m", fill=1.0, units="m s-1")
    ds["t2m"].attrs["standard_name"] = "air_temperature"
    with pytest.raises(UsageError, match="not convertible"):
        to_standard_units(ds)


def test_quantify_dataset_requires_units_for_temp_precip():
    ds = make_gridded(name="precip", units=None)
    with pytest.raises(UsageError, match="requires a units attribute"):
        quantify_dataset(ds)


def test_quantify_dataset_refuses_amount_totals():
    ds = make_gridded(name="precip", units="mm")
    with pytest.raises(UsageError, match="precip total"):
        quantify_dataset(ds)
    q = quantify_dataset(ds, allow_precip_totals=True)
    assert q["precip"].pint.units is not None


def test_quantify_dataset_refuses_cell_methods_sum():
    ds = make_gridded(name="precip", units="mm day-1")
    ds["precip"].attrs["cell_methods"] = "time: sum"
    with pytest.raises(UsageError, match="precip total"):
        quantify_dataset(ds)
    q = quantify_dataset(ds, allow_precip_totals=True)
    assert q["precip"].pint.units is not None


def test_quantify_dataset_passes_unitless_other_vars():
    ds = make_gridded(name="humidity", units=None)
    ds["humidity"].attrs.pop("units", None)
    q = quantify_dataset(ds)
    assert q["humidity"].pint.units is None
    assert not hasattr(q["humidity"].data, "units")


def test_quantify_dataset_quantifies_and_preserves_coord_attrs():
    ds = make_gridded(name="precip", units="mm day-1")
    ds["latitude"].attrs["units"] = "degrees_north"
    q = quantify_dataset(ds)
    assert q["precip"].pint.units is not None
    assert q["latitude"].pint.units is None
    assert q["latitude"].attrs["units"] == "degrees_north"
    plain = dequantify_dataset(q)
    assert "units" in plain["precip"].attrs


def test_format_cell_methods():
    assert format_cell_methods("time", "mean") == "time: mean"
    assert format_cell_methods("time", "mean", interval="1 day") == (
        "time: mean (interval: 1 day)"
    )


def test_timestep_gate_and_rate_to_total():
    times = np.array(["2026-01-07", "2026-01-14"], dtype="datetime64[D]")
    ds = xr.Dataset(
        {
            "precip": (
                ("time",),
                [1.0, 2.0],
                {AGGREGATION_PERIOD_ATTR: "7 day", "units": "mm day-1"},
            )
        },
        coords={"time": times},
    )
    assert_timestep_ge_aggregation_period(ds, "time", "7 day")
    q = quantify_dataset(ds)
    total = rate_to_total(q["precip"], "7 day")
    np.testing.assert_allclose(total.pint.dequantify().values, [7.0, 14.0])

    daily = xr.Dataset(
        {"precip": (("time",), np.ones(7), {"units": "mm day-1"})},
        coords={"time": np.arange("2026-01-01", "2026-01-08", dtype="datetime64[D]")},
    )
    with pytest.raises(UsageError, match="smaller than aggregation_period"):
        assert_timestep_ge_aggregation_period(daily, "time", "7 day")
