"""Tests for units equivalence, quantify, aggregation_period, and standard conversion."""

import numpy as np
import pytest
import xarray as xr
from conftest import make_gridded

from weather_skills_core.errors import UsageError
from weather_skills_core.units import (
    AGGREGATION_COVERAGE_COORD,
    AGGREGATION_PERIOD_ATTR,
    DATA_INTERVAL_ATTR,
    PRECIP_AMOUNT_LONG_NAME,
    STANDARD,
    assert_timestep_ge_aggregation_period,
    classify_variable,
    convert_values,
    dequantify_dataset,
    expected_samples_in_period,
    filter_min_coverage,
    format_cell_methods,
    format_duration,
    infer_timestep,
    looks_like_rate_display_name,
    parse_aggregation_period,
    precip_amounts_to_rates,
    quantify_dataset,
    rate_to_total,
    stamp_data_interval,
    stamp_precip_amounts,
    to_standard_units,
    units_convertible,
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
    assert classify_variable("tas", units="K") == "temp"
    assert classify_variable("tp", units="kg m-2", standard_name="precipitation_amount") == (
        "precip_amount"
    )
    assert classify_variable("precip", units="mm/day") == "precip"
    assert classify_variable("tp", units="kg m-2 s-1") == "precip"  # named hint
    assert classify_variable("tp", units="kg m-2") == "precip_amount"  # amount units win
    assert classify_variable("flux", units="kg m-2 s-1") is None  # units alone are not enough
    assert classify_variable("humidity", units="1") is None
    # Prefix "precip" must not classify a dimensionless companion field.
    assert classify_variable("precipitation_quality_index_surface", units="1") is None
    assert classify_variable("precipitation_surface", units="kg m-2 s-1") == "precip"
    assert classify_variable("precipitation_surface", units="mm/h") == "precip"
    # Not air / 2 m temperature
    assert classify_variable("sst", units="K", standard_name="sea_surface_temperature") is None
    assert classify_variable("d2m", units="K", standard_name="dew_point_temperature") is None
    assert classify_variable("skt", units="K", standard_name="surface_temperature") is None


def test_to_standard_units_converts_amount_named_tp_to_mm():
    ds = make_gridded(name="tp", fill=2.0, units="kg m-2")
    out = to_standard_units(ds)
    assert out["tp"].attrs["units"] == STANDARD["precip_amount"]["units"]
    assert out["tp"].attrs["standard_name"] == STANDARD["precip_amount"]["standard_name"]
    np.testing.assert_allclose(out["tp"].values, 2.0, atol=1e-6)


def test_to_standard_units_name_hint_sets_standard_name_keeps_name():
    ds = make_gridded(name="tp", fill=1.0, units="mm/day")
    out = to_standard_units(ds)
    assert "tp" in out.data_vars
    assert list(out.data_vars) == ["tp"]
    assert out["tp"].attrs["units"] == STANDARD["precip"]["units"]
    assert out["tp"].attrs["standard_name"] == "lwe_precipitation_rate"


def test_to_standard_units_normalizes_amount_and_temp():
    ds = make_gridded(name="tp", fill=2.0, units=None)
    ds["tp"].attrs.update(units="kg m-2", standard_name="precipitation_amount")
    out = to_standard_units(ds)
    assert out["tp"].attrs["units"] == STANDARD["precip_amount"]["units"]
    assert out["tp"].attrs["standard_name"] == STANDARD["precip_amount"]["standard_name"]

    tds = make_gridded(name="t2m", fill=300.0, units="K")
    tds["t2m"].attrs["standard_name"] = "air_temperature"
    tout = to_standard_units(tds)
    assert tout["t2m"].attrs["units"] == STANDARD["temp"]["units"]
    np.testing.assert_allclose(tout["t2m"].values, 300.0 - 273.15, rtol=1e-5)


def test_to_standard_units_noop_unknown():
    ds = make_gridded(name="humidity", fill=0.5, units="1")
    out = to_standard_units(ds)
    assert out["humidity"].attrs["units"] == "1"


def test_to_standard_units_skips_dimensionless_precip_named_companion():
    ds = make_gridded(name="precipitation_quality_index_surface", fill=0.8, units="1")
    out = to_standard_units(ds)
    assert out["precipitation_quality_index_surface"].attrs["units"] == "1"
    np.testing.assert_allclose(out["precipitation_quality_index_surface"].values, 0.8)


def test_units_convertible():
    assert units_convertible("kg m-2 s-1", "mm day-1")
    assert units_convertible("mm/h", "mm day-1")
    assert not units_convertible("1", "mm day-1")


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


def test_quantify_dataset_accepts_amount_totals():
    ds = make_gridded(name="precip", units="mm")
    q = quantify_dataset(ds)
    assert q["precip"].pint.units is not None


def test_quantify_dataset_accepts_cell_methods_sum():
    ds = make_gridded(name="precip", units="mm day-1")
    ds["precip"].attrs["cell_methods"] = "time: sum"
    q = quantify_dataset(ds)
    assert q["precip"].pint.units is not None


def test_rate_to_total_refuses_amount_totals():
    ds = make_gridded(name="precip", units="mm")
    q = quantify_dataset(ds)
    with pytest.raises(UsageError, match="precip total"):
        rate_to_total(q["precip"], "1 day")


def test_rate_to_total_refuses_cell_methods_sum():
    ds = make_gridded(name="precip", units="mm day-1")
    ds["precip"].attrs["cell_methods"] = "time: sum"
    q = quantify_dataset(ds)
    with pytest.raises(UsageError, match="precip total"):
        rate_to_total(q["precip"], "1 day")


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
    assert format_cell_methods("time", "mean", interval="1 day") == ("time: mean (interval: 1 day)")


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
    with pytest.raises(UsageError, match="select"):
        assert_timestep_ge_aggregation_period(daily, "time", "7 day")


def test_timestep_gate_allows_singleton():
    """One aggregated bin has no adjacent labels; conversion is still well-defined."""
    ds = xr.Dataset(
        {
            "precip": (
                ("time",),
                [1.0],
                {AGGREGATION_PERIOD_ATTR: "7 day", "units": "mm day-1"},
            )
        },
        coords={"time": np.array(["2026-01-07"], dtype="datetime64[D]")},
    )
    assert_timestep_ge_aggregation_period(ds, "time", "7 day")
    q = quantify_dataset(ds)
    total = rate_to_total(q["precip"], "7 day")
    np.testing.assert_allclose(total.pint.dequantify().values, [7.0])

    step_ds = xr.Dataset(
        {"precip": (("step",), [2.0], {"units": "mm day-1"})},
        coords={"step": np.array([np.timedelta64(7, "D")])},
    )
    assert_timestep_ge_aggregation_period(step_ds, "step", "7 day")


def test_infer_timestep_timedelta64_us_weekly():
    """dynamical-fetch writes step as timedelta64[us]; must not read 7 day as 0.007 d."""
    steps = (np.arange(1, 5) * np.timedelta64(7, "D")).astype("timedelta64[us]")
    ds = xr.Dataset(
        {"precip": (("step",), np.ones(4), {"units": "mm day-1"})},
        coords={"step": steps},
    )
    dt = infer_timestep(ds, "step")
    assert abs(float(dt.to("day").magnitude) - 7.0) < 1e-9
    assert_timestep_ge_aggregation_period(ds, "step", "7 day")


def test_format_duration_subday():
    assert format_duration(ureg.Quantity("30 minute")) == "30 minute"
    assert format_duration(ureg.Quantity("7 day")) == "7 day"
    assert format_duration(parse_aggregation_period("1 hour")) == "1 hour"


def test_stamp_data_interval_explicit_and_inferred():
    ds = make_gridded(n_time=2)
    stamp_data_interval(ds, period="30 minute")
    assert ds["precip"].attrs[DATA_INTERVAL_ATTR] == "30 minute"
    assert AGGREGATION_PERIOD_ATTR not in ds["precip"].attrs
    assert AGGREGATION_COVERAGE_COORD not in ds.coords

    daily = make_gridded(n_time=3)
    stamp_data_interval(daily)
    assert daily["precip"].attrs[DATA_INTERVAL_ATTR] == "1 day"


def test_expected_samples_and_filter_min_coverage():
    assert expected_samples_in_period("7 day", "30 minute") == 336
    assert expected_samples_in_period("21 day", "1 day") == 21

    times = np.array(["2026-01-21", "2026-02-11"], dtype="datetime64[D]")
    ds = xr.Dataset(
        {
            "precip": (
                ("time",),
                [1.0, 2.0],
                {
                    AGGREGATION_PERIOD_ATTR: "21 day",
                    DATA_INTERVAL_ATTR: "1 day",
                    "units": "mm day-1",
                },
            )
        },
        coords={
            "time": times,
            AGGREGATION_COVERAGE_COORD: ("time", [0.9, 1.0]),
        },
    )
    with pytest.raises(UsageError, match="min-coverage"):
        filter_min_coverage(ds.isel(time=slice(0, 1)), "time", 1.0)
    kept = filter_min_coverage(ds, "time", 0.6)
    assert kept.sizes["time"] == 2
    one = filter_min_coverage(ds, "time", 1.0)
    assert one.sizes["time"] == 1
    assert float(one["precip"].values[0]) == pytest.approx(2.0)


def test_looks_like_rate_display_name():
    assert looks_like_rate_display_name("precipitation rate")
    assert looks_like_rate_display_name("Precipitation rate")
    assert looks_like_rate_display_name("daily precipitation rate")
    assert looks_like_rate_display_name("precipitation_flux")
    assert not looks_like_rate_display_name("Total precipitation")
    assert not looks_like_rate_display_name("CHIRPS daily precipitation")
    assert not looks_like_rate_display_name("")
    assert not looks_like_rate_display_name(None)


def test_stamp_precip_amounts_overwrites_rate_display_names():
    ds = make_gridded(n_time=1, fill=1.0, units="mm")
    ds["precip"].attrs.update(
        standard_name="lwe_precipitation_rate",
        long_name="precipitation rate",
        GRIB_name="Precipitation rate",
    )
    out = stamp_precip_amounts(ds)
    assert out["precip"].attrs["standard_name"] == STANDARD["precip_amount"]["standard_name"]
    assert out["precip"].attrs["long_name"] == PRECIP_AMOUNT_LONG_NAME
    assert out["precip"].attrs["GRIB_name"] == PRECIP_AMOUNT_LONG_NAME

    kept = make_gridded(n_time=1, fill=1.0, units="mm")
    kept["precip"].attrs["long_name"] = "CHIRPS daily precipitation"
    stamped = stamp_precip_amounts(kept)
    assert stamped["precip"].attrs["long_name"] == "CHIRPS daily precipitation"


def test_precip_amounts_to_rates_daily_and_step():
    daily = make_gridded(n_time=2, fill=4.0, units="mm")
    daily["precip"].attrs["standard_name"] = "precipitation_amount"
    out = precip_amounts_to_rates(daily, interval="1 day")
    assert out["precip"].attrs["units"] == STANDARD["precip"]["units"]
    np.testing.assert_allclose(out["precip"].values, daily["precip"].values)

    steps = np.array([np.timedelta64(d, "D") for d in (1, 2, 3)])
    tp = xr.Dataset(
        {
            "tp": (
                ("step",),
                np.array([1.0, 3.0, 6.0]),
                {"units": "mm", "standard_name": "precipitation_amount"},
            )
        },
        coords={"step": steps},
    )
    rates = precip_amounts_to_rates(tp)
    assert rates.sizes["step"] == 2
    np.testing.assert_allclose(rates["tp"].values, [2.0, 3.0])
    assert rates["tp"].attrs["units"] == STANDARD["precip"]["units"]

    mixed = tp.assign(t2m=("step", [280.0, 281.0, 282.0]))
    mixed["t2m"].attrs.update(units="K", standard_name="air_temperature")
    mixed_out = precip_amounts_to_rates(mixed)
    assert mixed_out.sizes["step"] == 2
    np.testing.assert_allclose(mixed_out["tp"].values, [2.0, 3.0])
    np.testing.assert_allclose(mixed_out["t2m"].values, [281.0, 282.0])


def test_precip_convertible_names_skips_temp_with_leftover_precip_standard_name():
    ds = make_gridded(n_time=2, name="2m_temperature", fill=280.0, units="K")
    ds["2m_temperature"].attrs["standard_name"] = "lwe_precipitation_rate"
    from weather_skills_core.units import precip_convertible_names

    assert precip_convertible_names(ds) == []

    rain = make_gridded(n_time=2, fill=1.0, units="mm day-1")
    assert precip_convertible_names(rain) == ["precip"]
