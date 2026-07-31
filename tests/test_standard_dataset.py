from typing import ClassVar

import numpy as np
import pytest
import xarray as xr
from conftest import make_forecast, make_gridded, make_station, make_vertical_forecast

from weather_skills_core import standard_dataset as dataset
from weather_skills_core.errors import DataError, UsageError


class TestDetectSpatialDims:
    def test_canonical_names(self):
        assert dataset.detect_spatial_dims(make_gridded()) == ("latitude", "longitude")

    def test_alias_names(self):
        ds = make_gridded().rename({"latitude": "lat", "longitude": "lon"})
        assert dataset.detect_spatial_dims(ds) == ("lat", "lon")

    def test_override_wins(self):
        ds = make_gridded().rename({"latitude": "yy", "longitude": "xx"})
        assert dataset.detect_spatial_dims(ds, "yy,xx") == ("yy", "xx")

    def test_override_names_must_exist(self):
        with pytest.raises(UsageError, match="not in dataset dims"):
            dataset.detect_spatial_dims(make_gridded(), "a,b")

    def test_override_must_be_two_names(self):
        with pytest.raises(UsageError, match="LAT,LON"):
            dataset.detect_spatial_dims(make_gridded(), "onlyone")

    def test_unidentifiable(self):
        ds = make_gridded().rename({"latitude": "row", "longitude": "col"})
        with pytest.raises(UsageError, match="Pass --dims"):
            dataset.detect_spatial_dims(ds)

    def test_cf_attrs_resolve_nonstandard_names(self):
        ds = make_gridded().rename({"latitude": "yy", "longitude": "xx"})
        ds["yy"].attrs.update(standard_name="latitude", units="degrees_north")
        ds["xx"].attrs.update(standard_name="longitude", units="degrees_east")
        assert dataset.detect_spatial_dims(ds) == ("yy", "xx")


class TestDetectTimeDim:
    def test_literal_time(self):
        assert dataset.detect_time_dim(make_gridded()) == "time"

    def test_override(self):
        ds = make_gridded().rename({"time": "t"})
        assert dataset.detect_time_dim(ds, "t") == "t"

    def test_override_must_exist(self):
        with pytest.raises(UsageError, match="not in dataset dims"):
            dataset.detect_time_dim(make_gridded(), "t")

    def test_unidentifiable(self):
        ds = make_gridded().rename({"time": "record"}).drop_vars("record")
        with pytest.raises(UsageError, match="Pass --time-dim"):
            dataset.detect_time_dim(ds)


class TestDetectType:
    def test_gridded(self):
        assert dataset.detect_type(make_gridded()) == "observations"

    def test_forecast(self):
        assert dataset.detect_type(make_forecast(n_number=0)) == "forecast"
        # Default fixture includes a member dim → ensemble_forecast
        assert dataset.detect_type(make_forecast()) == "ensemble_forecast"

    def test_vertical_forecast(self):
        ds = make_vertical_forecast()
        assert dataset.detect_type(ds) == "vertical_forecast"
        assert dataset.validate_input(ds, "vertical_forecast", "in.zarr") == "vertical_forecast"
        with pytest.raises(UsageError, match="vertical"):
            dataset.validate_input(make_forecast(n_number=0), "vertical_forecast", "in.zarr")

    def test_point_obs(self):
        assert dataset.detect_type(make_station()) == "point_obs"

    def test_station_alias_same_as_point_obs(self):
        assert dataset.parse_alternatives("station") == dataset.parse_alternatives(
            "point_obs"
        )

    def test_step_with_time_dim_is_not_forecast(self):
        # Forecast needs scalar init (time) + step; a step dim alongside a
        # time *dim* does not classify as forecast.
        ds = make_forecast()
        ds = ds.drop_vars("time")
        ds = ds.expand_dims(time=np.array(["2026-01-01"], dtype="datetime64[ns]"))
        assert dataset.detect_type(ds) == "observations"


class TestParseIoSpec:
    def test_canonical_observations(self):
        assert dataset.parse_alternatives("observations") == (
            frozenset({"lat", "lon", "time"}),
        )

    def test_obs_aliases_same_as_observations(self):
        expected = dataset.parse_alternatives("observations")
        for alias in ("obs", "analysis", "retrieval", "field", "data"):
            assert dataset.parse_alternatives(alias) == expected

    def test_or_list(self):
        alts = dataset.parse_alternatives(["forecast", "ensemble_forecast"])
        assert len(alts) == 2
        assert frozenset({"lat", "lon", "init_time", "prediction_timedelta"}) in alts
        assert (
            frozenset({"lat", "lon", "init_time", "prediction_timedelta", "member"}) in alts
        )

    def test_and_tuple(self):
        assert dataset.parse_alternatives(("lat", "lon", "time")) == (
            frozenset({"lat", "lon", "time"}),
        )

    def test_spatial_alias(self):
        assert dataset.parse_alternatives("spatial") == (frozenset({"lat", "lon"}),)
        assert dataset.parse_alternatives("space") == (frozenset({"lat", "lon"}),)

    def test_vertical_forecast(self):
        assert dataset.parse_alternatives("vertical_forecast") == (
            frozenset({"lat", "lon", "init_time", "prediction_timedelta", "vertical"}),
        )
        assert dataset.parse_alternatives("vertical") == (frozenset({"vertical"}),)

    def test_single_dim(self):
        assert dataset.parse_alternatives("time") == (frozenset({"time"}),)
        assert dataset.parse_alternatives("lat") == (frozenset({"lat"}),)


class TestValidateInput:
    def test_matching_type_passes(self):
        assert (
            dataset.validate_input(make_gridded(), "observations", "in.zarr")
            == "observations"
        )

    def test_legacy_data_alias(self):
        assert dataset.validate_input(make_gridded(), "data", "in.zarr") == "observations"

    def test_list_of_alternatives(self):
        assert (
            dataset.validate_input(
                make_forecast(n_number=0), ["observations", "forecast"], "in.zarr"
            )
            == "forecast"
        )

    def test_or_canonical_list(self):
        assert (
            dataset.validate_input(
                make_forecast(), ["forecast", "ensemble_forecast"], "in.zarr"
            )
            == "ensemble_forecast"
        )

    def test_dim_only_spatial(self):
        assert dataset.validate_input(make_gridded(), "spatial", "in.zarr") == "observations"
        assert dataset.validate_input(make_gridded(), "space", "in.zarr") == "observations"

    def test_gridded_rejected_when_forecast_expected(self):
        with pytest.raises(UsageError, match="prediction_timedelta|init_time"):
            dataset.validate_input(make_gridded(), "forecast", "in.zarr")

    def test_forecast_rejected_when_station_expected(self):
        with pytest.raises(UsageError, match="point_id"):
            dataset.validate_input(make_forecast(), "station", "in.zarr")

    def test_error_names_the_input(self):
        with pytest.raises(UsageError, match="my/path.zarr"):
            dataset.validate_input(make_gridded(), "station", "my/path.zarr")

    def test_station_missing_latitude_coord(self):
        ds = make_station().drop_vars("latitude")
        with pytest.raises(UsageError, match="point_id"):
            dataset.validate_input(ds, "station", "in.zarr")

    def test_station_latitude_on_wrong_dim(self):
        ds = make_station()
        ds = ds.assign_coords(latitude=("time", np.zeros(ds.sizes["time"])))
        with pytest.raises(UsageError, match="point_id"):
            dataset.validate_input(ds, "station", "in.zarr")

    def test_point_obs_accepts_lat_lon_aliases(self):
        ds = make_station().rename({"latitude": "lat", "longitude": "lon"})
        assert dataset.validate_input(ds, "point_obs", "in.zarr") == "point_obs"

    def test_unknown_declared_type_is_a_programming_error(self):
        with pytest.raises(ValueError, match="unknown type or dimension"):
            dataset.validate_input(make_gridded(), "grid", "in.zarr")

    def test_dims_override_validates_undetectable_gridded(self):
        ds = make_gridded().rename({"latitude": "yy", "longitude": "xx"})
        with pytest.raises(UsageError, match="lat|lon"):
            dataset.validate_input(ds, "observations", "in.zarr")
        assert (
            dataset.validate_input(ds, "observations", "in.zarr", dims="yy,xx")
            == "observations"
        )

    def test_dims_override_names_must_exist(self):
        ds = make_gridded().rename({"latitude": "yy", "longitude": "xx"})
        with pytest.raises(UsageError, match="lat|lon"):
            dataset.validate_input(ds, "observations", "in.zarr", dims="a,b")

    def test_time_dim_override_must_exist(self):
        with pytest.raises(UsageError, match="time"):
            dataset.validate_input(make_gridded(), "observations", "in.zarr", time_dim="t")
        ds = make_gridded().rename({"time": "t"})
        assert (
            dataset.validate_input(ds, "observations", "in.zarr", time_dim="t")
            == "observations"
        )

    def test_any_accepts_all(self):
        for ds in (make_gridded(), make_forecast(), make_station()):
            dataset.validate_input(ds, "any", "in.zarr")


