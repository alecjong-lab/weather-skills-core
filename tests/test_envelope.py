import numpy as np
import pytest
import xarray as xr
from conftest import make_forecast, make_gridded, make_station

from weather_skills_core import envelope
from weather_skills_core.errors import DataError, UsageError


class TestParseBbox:
    def test_valid(self):
        assert envelope.parse_bbox("1/2/3/4") == (1.0, 2.0, 3.0, 4.0)

    def test_negative_values(self):
        assert envelope.parse_bbox("-1/32/-5/42") == (-1.0, 32.0, -5.0, 42.0)

    @pytest.mark.parametrize("value", ["1/2/3", "1/2/3/4/5", "a/b/c/d", ""])
    def test_malformed(self, value):
        with pytest.raises(UsageError, match="N/W/S/E"):
            envelope.parse_bbox(value)


class TestDetectSpatialDims:
    def test_canonical_names(self):
        assert envelope.detect_spatial_dims(make_gridded()) == ("latitude", "longitude")

    def test_alias_names(self):
        ds = make_gridded().rename({"latitude": "lat", "longitude": "lon"})
        assert envelope.detect_spatial_dims(ds) == ("lat", "lon")

    def test_override_wins(self):
        ds = make_gridded().rename({"latitude": "yy", "longitude": "xx"})
        assert envelope.detect_spatial_dims(ds, "yy,xx") == ("yy", "xx")

    def test_override_names_must_exist(self):
        with pytest.raises(UsageError, match="not in dataset dims"):
            envelope.detect_spatial_dims(make_gridded(), "a,b")

    def test_override_must_be_two_names(self):
        with pytest.raises(UsageError, match="LAT,LON"):
            envelope.detect_spatial_dims(make_gridded(), "onlyone")

    def test_unidentifiable(self):
        ds = make_gridded().rename({"latitude": "row", "longitude": "col"})
        with pytest.raises(UsageError, match="Pass --dims"):
            envelope.detect_spatial_dims(ds)

    def test_cf_attrs_resolve_nonstandard_names(self):
        ds = make_gridded().rename({"latitude": "yy", "longitude": "xx"})
        ds["yy"].attrs.update(standard_name="latitude", units="degrees_north")
        ds["xx"].attrs.update(standard_name="longitude", units="degrees_east")
        assert envelope.detect_spatial_dims(ds) == ("yy", "xx")


class TestDetectTimeDim:
    def test_literal_time(self):
        assert envelope.detect_time_dim(make_gridded()) == "time"

    def test_override(self):
        ds = make_gridded().rename({"time": "t"})
        assert envelope.detect_time_dim(ds, "t") == "t"

    def test_override_must_exist(self):
        with pytest.raises(UsageError, match="not in dataset dims"):
            envelope.detect_time_dim(make_gridded(), "t")

    def test_unidentifiable(self):
        ds = make_gridded().rename({"time": "record"}).drop_vars("record")
        with pytest.raises(UsageError, match="Pass --time-dim"):
            envelope.detect_time_dim(ds)


class TestDetectType:
    def test_gridded(self):
        assert envelope.detect_type(make_gridded()) == envelope.GRIDDED

    def test_forecast(self):
        assert envelope.detect_type(make_forecast()) == envelope.FORECAST

    def test_station(self):
        assert envelope.detect_type(make_station()) == envelope.STATION

    def test_step_with_time_dim_is_not_forecast(self):
        # A forecast envelope is a step dim plus a SCALAR time coord; a step
        # dim alongside a time dim does not classify as forecast.
        ds = make_forecast()
        ds = ds.drop_vars("time")
        ds = ds.expand_dims(time=np.array(["2026-01-01"], dtype="datetime64[ns]"))
        assert envelope.detect_type(ds) == envelope.GRIDDED


class TestValidateInput:
    def test_matching_type_passes(self):
        assert envelope.validate_input(make_gridded(), "gridded", "in.zarr") == "gridded"

    def test_any_passes_everything(self):
        for ds in (make_gridded(), make_forecast(), make_station()):
            envelope.validate_input(ds, "any", "in.zarr")

    def test_list_of_alternatives(self):
        assert (
            envelope.validate_input(make_forecast(), ["gridded", "forecast"], "in.zarr")
            == "forecast"
        )

    def test_gridded_rejected_when_forecast_expected(self):
        with pytest.raises(UsageError, match="no 'step' dim"):
            envelope.validate_input(make_gridded(), "forecast", "in.zarr")

    def test_forecast_rejected_when_station_expected(self):
        with pytest.raises(UsageError, match="no 'station_id' dim"):
            envelope.validate_input(make_forecast(), "station", "in.zarr")

    def test_error_names_the_input(self):
        with pytest.raises(UsageError, match="my/path.zarr"):
            envelope.validate_input(make_gridded(), "station", "my/path.zarr")

    def test_station_missing_latitude_coord(self):
        ds = make_station().drop_vars("latitude")
        with pytest.raises(UsageError, match="'latitude'"):
            envelope.validate_input(ds, "station", "in.zarr")

    def test_station_latitude_on_wrong_dim(self):
        ds = make_station()
        ds = ds.assign_coords(latitude=("time", np.zeros(ds.sizes["time"])))
        with pytest.raises(UsageError, match="station_id"):
            envelope.validate_input(ds, "station", "in.zarr")

    def test_unknown_declared_type_is_a_programming_error(self):
        with pytest.raises(ValueError, match="unknown envelope type"):
            envelope.validate_input(make_gridded(), "grid", "in.zarr")


class TestBboxSubset:
    def test_ascending_latitude(self):
        ds = make_gridded(lats=(1.0, 2.0, 3.0), lons=(10.0, 11.0, 12.0, 13.0))
        sub = envelope.bbox_subset(ds, (2.5, 10.5, 0.5, 12.5))
        assert list(sub["latitude"].values) == [1.0, 2.0]
        assert list(sub["longitude"].values) == [11.0, 12.0]

    def test_descending_latitude_same_bbox(self):
        ds = make_gridded(lats=(3.0, 2.0, 1.0))
        sub = envelope.bbox_subset(ds, (2.5, 10.5, 0.5, 12.5))
        assert list(sub["latitude"].values) == [2.0, 1.0]

    def test_lon_0_360_normalized(self):
        ds = make_gridded(lons=(0.0, 90.0, 180.0, 270.0, 359.0))
        sub = envelope.bbox_subset(ds, (3.0, -95.0, 1.0, -85.0))
        assert list(sub["longitude"].values) == [-90.0]

    def test_antimeridian_keeps_wings_drops_interior(self):
        ds = make_gridded(lons=(-179.0, -100.0, 0.0, 100.0, 179.0))
        sub = envelope.bbox_subset(ds, (3.0, 170.0, 1.0, -170.0))
        assert list(sub["longitude"].values) == [-179.0, 179.0]

    def test_single_row_latitude_passes_through(self):
        ds = make_gridded(lats=(1.0,))
        sub = envelope.bbox_subset(ds, (60.0, 10.5, 50.0, 12.5))
        assert list(sub["latitude"].values) == [1.0]

    def test_non_monotonic_latitude_rejected(self):
        ds = make_gridded(lats=(1.0, 3.0, 2.0))
        with pytest.raises(UsageError, match="non-monotonic"):
            envelope.bbox_subset(ds, (3.0, 10.0, 1.0, 13.0))

    def test_empty_result_is_data_error(self):
        ds = make_gridded()
        with pytest.raises(DataError, match="selects no grid cells"):
            envelope.bbox_subset(ds, (60.0, 10.0, 50.0, 13.0))

    def test_empty_antimeridian_result_names_the_crossing(self):
        ds = make_gridded(lons=(-10.0, 0.0, 10.0))
        with pytest.raises(DataError, match="antimeridian"):
            envelope.bbox_subset(ds, (3.0, 170.0, 1.0, -170.0))

    def test_string_bbox_accepted(self):
        sub = envelope.bbox_subset(make_gridded(), "2.5/10.5/0.5/12.5")
        assert list(sub["latitude"].values) == [1.0, 2.0]

    def test_explicit_dims(self):
        ds = make_gridded().rename({"latitude": "yy", "longitude": "xx"})
        sub = envelope.bbox_subset(ds, (2.5, 10.5, 0.5, 12.5), lat_dim="yy", lon_dim="xx")
        assert list(sub["yy"].values) == [1.0, 2.0]

    def test_data_selected_matches_coords(self):
        ds = make_gridded()
        sub = envelope.bbox_subset(ds, (2.5, 10.5, 0.5, 12.5))
        assert sub["precip"].shape == (2, 2, 2)
        assert isinstance(sub, xr.Dataset)
