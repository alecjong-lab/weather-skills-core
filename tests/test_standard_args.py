from typing import ClassVar

import numpy as np
import pytest
import xarray as xr
from conftest import make_forecast, make_gridded, make_station

from weather_skills_core import standard_args as dataset
from weather_skills_core.errors import DataError, UsageError


class TestParseBbox:
    def test_valid(self):
        assert dataset.parse_bbox("1/2/3/4") == (1.0, 2.0, 3.0, 4.0)

    def test_negative_values(self):
        assert dataset.parse_bbox("-1/32/-5/42") == (-1.0, 32.0, -5.0, 42.0)

    @pytest.mark.parametrize("value", ["1/2/3", "1/2/3/4/5", "a/b/c/d", ""])
    def test_malformed(self, value):
        with pytest.raises(UsageError, match="N/W/S/E"):
            dataset.parse_bbox(value)


class TestBboxSubset:
    def test_ascending_latitude(self):
        ds = make_gridded(lats=(1.0, 2.0, 3.0), lons=(10.0, 11.0, 12.0, 13.0))
        sub = dataset.bbox_subset(ds, (2.5, 10.5, 0.5, 12.5))
        assert list(sub["latitude"].values) == [1.0, 2.0]
        assert list(sub["longitude"].values) == [11.0, 12.0]

    def test_descending_latitude_same_bbox(self):
        ds = make_gridded(lats=(3.0, 2.0, 1.0))
        sub = dataset.bbox_subset(ds, (2.5, 10.5, 0.5, 12.5))
        assert list(sub["latitude"].values) == [2.0, 1.0]

    def test_lon_0_360_normalized(self):
        ds = make_gridded(lons=(0.0, 90.0, 180.0, 270.0, 359.0))
        sub = dataset.bbox_subset(ds, (3.0, -95.0, 1.0, -85.0))
        assert list(sub["longitude"].values) == [-90.0]

    def test_antimeridian_keeps_wings_drops_interior(self):
        ds = make_gridded(lons=(-179.0, -100.0, 0.0, 100.0, 179.0))
        sub = dataset.bbox_subset(ds, (3.0, 170.0, 1.0, -170.0))
        assert list(sub["longitude"].values) == [-179.0, 179.0]

    def test_single_row_latitude_passes_through(self):
        ds = make_gridded(lats=(1.0,))
        sub = dataset.bbox_subset(ds, (60.0, 10.5, 50.0, 12.5))
        assert list(sub["latitude"].values) == [1.0]

    def test_non_monotonic_latitude_rejected(self):
        ds = make_gridded(lats=(1.0, 3.0, 2.0))
        with pytest.raises(UsageError, match="lat axis is non-monotonic"):
            dataset.bbox_subset(ds, (3.0, 10.0, 1.0, 13.0))

    def test_non_monotonic_longitude_rejected(self):
        ds = make_gridded(lons=(10.0, 12.0, 11.0, 13.0))
        with pytest.raises(UsageError, match="lon axis is non-monotonic"):
            dataset.bbox_subset(ds, (3.0, 10.0, 1.0, 13.0))

    def test_empty_longitude_axis_rejected(self):
        ds = make_gridded(lons=())
        with pytest.raises(UsageError, match="lon axis has length 0"):
            dataset.bbox_subset(ds, (3.0, 10.0, 1.0, 13.0))

    def test_descending_longitude_contiguous_span(self):
        ds = make_gridded(lons=(13.0, 12.0, 11.0, 10.0))
        sub = dataset.bbox_subset(ds, (2.5, 10.5, 0.5, 12.5))
        assert list(sub["longitude"].values) == [12.0, 11.0]

    def test_antimeridian_preserves_integer_dtype(self):
        ds = make_gridded(lons=(-179.0, -100.0, 0.0, 100.0, 179.0))
        ds["count"] = (
            ("time", "latitude", "longitude"),
            np.ones((2, 3, 5), dtype=np.int32),
        )
        sub = dataset.bbox_subset(ds, (3.0, 170.0, 1.0, -170.0))
        assert sub["count"].dtype == np.int32
        assert list(sub["longitude"].values) == [-179.0, 179.0]

    def test_antimeridian_descending_longitude_keeps_native_order(self):
        ds = make_gridded(lons=(179.0, 100.0, 0.0, -100.0, -179.0))
        sub = dataset.bbox_subset(ds, (3.0, 170.0, 1.0, -170.0))
        assert list(sub["longitude"].values) == [179.0, -179.0]

    def test_antimeridian_leaves_non_longitude_variables_alone(self):
        ds = make_gridded(lons=(-179.0, 0.0, 179.0))
        ds["tavg"] = (("time",), np.array([5, 6], dtype=np.int16))
        sub = dataset.bbox_subset(ds, (3.0, 170.0, 1.0, -170.0))
        assert sub["tavg"].dims == ("time",)
        assert sub["tavg"].dtype == np.int16
        assert list(sub["tavg"].values) == [5, 6]

    def test_empty_result_is_data_error(self):
        ds = make_gridded()
        with pytest.raises(DataError, match="selects no grid cells"):
            dataset.bbox_subset(ds, (60.0, 10.0, 50.0, 13.0))

    def test_empty_antimeridian_result_names_the_crossing(self):
        ds = make_gridded(lons=(-10.0, 0.0, 10.0))
        with pytest.raises(DataError, match="antimeridian"):
            dataset.bbox_subset(ds, (3.0, 170.0, 1.0, -170.0))

    def test_string_bbox_accepted(self):
        sub = dataset.bbox_subset(make_gridded(), "2.5/10.5/0.5/12.5")
        assert list(sub["latitude"].values) == [1.0, 2.0]

    def test_explicit_dims(self):
        ds = make_gridded().rename({"latitude": "yy", "longitude": "xx"})
        sub = dataset.bbox_subset(ds, (2.5, 10.5, 0.5, 12.5), lat_dim="yy", lon_dim="xx")
        assert list(sub["yy"].values) == [1.0, 2.0]

    def test_data_selected_matches_coords(self):
        ds = make_gridded()
        sub = dataset.bbox_subset(ds, (2.5, 10.5, 0.5, 12.5))
        assert sub["precip"].shape == (2, 2, 2)
        assert isinstance(sub, xr.Dataset)


class TestLatSlice:
    def test_ascending(self):
        assert dataset.lat_slice(np.array([1.0, 2.0, 3.0]), 3.0, 1.0) == slice(1.0, 3.0)

    def test_descending(self):
        assert dataset.lat_slice(np.array([3.0, 2.0, 1.0]), 3.0, 1.0) == slice(3.0, 1.0)

    def test_empty_axis_defaults_to_ascending(self):
        assert dataset.lat_slice(np.array([]), 3.0, 1.0) == slice(1.0, 3.0)

    def test_single_value(self):
        assert dataset.lat_slice(np.array([2.0]), 3.0, 1.0) == slice(1.0, 3.0)


class TestPolygonFromGeojson:
    square: ClassVar[dict] = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]],
    }
    east_square: ClassVar[dict] = {
        "type": "Polygon",
        "coordinates": [[[2, 0], [2, 1], [3, 1], [3, 0], [2, 0]]],
    }

    def write(self, tmp_path, payload):
        import json

        p = tmp_path / "mask.geojson"
        p.write_text(json.dumps(payload))
        return p

    def test_feature_collection_unions_all_features(self, tmp_path):
        payload = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": self.square},
                {"type": "Feature", "geometry": self.east_square},
                {"type": "Feature", "geometry": None},
            ],
        }
        poly = dataset.polygon_from_geojson(self.write(tmp_path, payload))
        assert poly.area == pytest.approx(2.0)

    def test_single_feature(self, tmp_path):
        payload = {"type": "Feature", "geometry": self.square}
        poly = dataset.polygon_from_geojson(self.write(tmp_path, payload))
        assert poly.area == pytest.approx(1.0)

    def test_bare_geometry(self, tmp_path):
        poly = dataset.polygon_from_geojson(self.write(tmp_path, self.square))
        assert poly.area == pytest.approx(1.0)

    def test_missing_file(self, tmp_path):
        with pytest.raises(UsageError, match="--mask-geojson file not found"):
            dataset.polygon_from_geojson(tmp_path / "nope.geojson")

    def test_unreadable_json(self, tmp_path):
        p = tmp_path / "mask.geojson"
        p.write_text("{not json")
        with pytest.raises(UsageError, match="could not read --mask-geojson"):
            dataset.polygon_from_geojson(p)

    def test_no_usable_geometry(self, tmp_path):
        payload = {"type": "FeatureCollection", "features": []}
        with pytest.raises(UsageError, match="has no usable geometry"):
            dataset.polygon_from_geojson(self.write(tmp_path, payload))

    def test_top_level_array_is_no_usable_geometry(self, tmp_path):
        with pytest.raises(UsageError, match="has no usable geometry"):
            dataset.polygon_from_geojson(self.write(tmp_path, [self.square]))

    def test_top_level_scalar_is_no_usable_geometry(self, tmp_path):
        with pytest.raises(UsageError, match="has no usable geometry"):
            dataset.polygon_from_geojson(self.write(tmp_path, "Polygon"))

    def test_flag_names_the_source_flag(self, tmp_path):
        with pytest.raises(UsageError, match="--clip-geojson file not found"):
            dataset.polygon_from_geojson(tmp_path / "nope.geojson", flag="--clip-geojson")

    def test_non_list_features_value_raises_usage_error(self, tmp_path):
        payload = {"type": "FeatureCollection", "features": {"not": "a list"}}
        with pytest.raises(UsageError, match="'features' is not a list"):
            dataset.polygon_from_geojson(self.write(tmp_path, payload))

    def test_non_object_feature_entry_raises_usage_error(self, tmp_path):
        payload = {"type": "FeatureCollection", "features": ["not-an-object"]}
        with pytest.raises(UsageError, match="a feature is not a JSON object"):
            dataset.polygon_from_geojson(self.write(tmp_path, payload))

    def test_unknown_geometry_type_raises_usage_error_naming_the_flag(self, tmp_path):
        payload = {"type": "Bogus", "coordinates": [0, 0]}
        with pytest.raises(UsageError, match="--mask-geojson.*has no usable geometry"):
            dataset.polygon_from_geojson(self.write(tmp_path, payload))

    def test_geometry_missing_coordinates_raises_usage_error(self, tmp_path):
        payload = {"type": "Feature", "geometry": {"type": "Point"}}
        with pytest.raises(UsageError, match="has no usable geometry"):
            dataset.polygon_from_geojson(self.write(tmp_path, payload))

    def test_malformed_coordinates_raise_usage_error_not_a_traceback(self, tmp_path):
        # A string where a coordinate array is expected makes shape() raise a
        # TypeError; it must convert to a flag-named UsageError.
        payload = {"type": "Point", "coordinates": "nope"}
        with pytest.raises(UsageError, match="has no usable geometry"):
            dataset.polygon_from_geojson(self.write(tmp_path, payload))


class TestNormalizeLongitude:
    def test_0_360_axis_wraps_and_sorts(self):
        ds = make_gridded(lons=(0.0, 90.0, 180.0, 270.0))
        out = dataset.normalize_longitude(ds)
        assert list(out["longitude"].values) == [-180.0, -90.0, 0.0, 90.0]

    def test_values_follow_their_cells(self):
        ds = make_gridded(lons=(0.0, 90.0, 180.0, 270.0))
        ds["precip"][:, :, 3] = 7.0  # the 270 column
        out = dataset.normalize_longitude(ds)
        assert float(out["precip"].sel(longitude=-90.0).isel(time=0, latitude=0)) == 7.0

    def test_already_normalized_axis_is_unchanged(self):
        ds = make_gridded(lons=(-90.0, 0.0, 90.0))
        out = dataset.normalize_longitude(ds)
        assert list(out["longitude"].values) == [-90.0, 0.0, 90.0]

    def test_custom_dim_name(self):
        ds = make_gridded(lons=(0.0, 270.0)).rename({"longitude": "lon"})
        out = dataset.normalize_longitude(ds, lon_dim="lon")
        assert list(out["lon"].values) == [-90.0, 0.0]

    def test_longitude_attrs_preserved_across_the_wrap(self):
        ds = make_gridded(lons=(0.0, 90.0, 180.0, 270.0))
        ds["longitude"].attrs = {"standard_name": "longitude", "units": "degrees_east", "axis": "X"}
        out = dataset.normalize_longitude(ds)
        assert out["longitude"].attrs == {
            "standard_name": "longitude",
            "units": "degrees_east",
            "axis": "X",
        }

    def test_duplicate_endpoint_is_dropped_and_axis_stays_sorted(self):
        # 0.0 and 360.0 both wrap onto 0.0; the duplicate is dropped and the
        # axis remains a valid, ascending index.
        ds = make_gridded(lons=(0.0, 90.0, 180.0, 270.0, 360.0))
        out = dataset.normalize_longitude(ds)
        lons = list(out["longitude"].values)
        assert lons == [-180.0, -90.0, 0.0, 90.0]
        assert len(lons) == len(set(lons))

    def test_duplicate_drop_keeps_the_first_occurrence(self):
        # The 0.0 column carries a distinct value from the 360.0 column; the
        # first occurrence (input order) is the one kept.
        ds = make_gridded(lons=(0.0, 90.0, 180.0, 270.0, 360.0))
        ds["precip"][:, :, 0] = 5.0  # the original 0.0 column
        ds["precip"][:, :, 4] = 9.0  # the original 360.0 column
        out = dataset.normalize_longitude(ds)
        assert float(out["precip"].sel(longitude=0.0).isel(time=0, latitude=0)) == 5.0


