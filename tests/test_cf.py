from typing import ClassVar

import numpy as np
import pytest
import xarray as xr
from conftest import make_forecast, make_gridded, make_station

from weather_skills_core import cf as dataset
from weather_skills_core.errors import DataError, UsageError


class TestStampCfAttrs:
    def test_canonical_names(self):
        ds = dataset.stamp_cf_attrs(make_gridded())
        assert ds["latitude"].attrs == {
            "standard_name": "latitude",
            "units": "degrees_north",
            "axis": "Y",
        }
        assert ds["longitude"].attrs == {
            "standard_name": "longitude",
            "units": "degrees_east",
            "axis": "X",
        }
        assert ds["time"].attrs == {"standard_name": "time", "axis": "T"}

    def test_alias_names(self):
        ds = dataset.stamp_cf_attrs(make_gridded().rename({"latitude": "lat", "longitude": "lon"}))
        assert ds["lat"].attrs["standard_name"] == "latitude"
        assert ds["lon"].attrs["standard_name"] == "longitude"

    def test_setdefault_preserves_source_values(self):
        ds = make_gridded()
        ds["latitude"].attrs["units"] = "degree_north"
        ds["time"].attrs["standard_name"] = "forecast_reference_time"
        dataset.stamp_cf_attrs(ds)
        assert ds["latitude"].attrs["units"] == "degree_north"
        assert ds["latitude"].attrs["axis"] == "Y"
        assert ds["time"].attrs["standard_name"] == "forecast_reference_time"

    def test_missing_coords_are_skipped(self):
        ds = make_gridded().rename({"latitude": "row", "longitude": "col"}).drop_vars("time")
        out = dataset.stamp_cf_attrs(ds)
        assert out["row"].attrs == {}
        assert out["col"].attrs == {}

    def test_returns_dataset(self):
        ds = make_gridded()
        assert dataset.stamp_cf_attrs(ds) is ds


class TestStampCfCoords:
    def test_overwrites_prior_values(self):
        ds = make_gridded()
        ds["latitude"].attrs.update(standard_name="wrong", units="wrong", axis="Z")
        dataset.stamp_cf_coords(ds)
        assert ds["latitude"].attrs == {
            "standard_name": "latitude",
            "units": "degrees_north",
            "axis": "Y",
        }
        assert ds["longitude"].attrs == {
            "standard_name": "longitude",
            "units": "degrees_east",
            "axis": "X",
        }

    def test_time_gets_no_units(self):
        ds = dataset.stamp_cf_coords(make_gridded())
        assert ds["time"].attrs == {"standard_name": "time", "axis": "T"}

    def test_long_names_applied_with_setdefault(self):
        ds = make_gridded()
        ds["latitude"].attrs["long_name"] = "source latitude"
        dataset.stamp_cf_coords(
            ds, long_names={"latitude": "Latitude", "longitude": "Longitude", "time": "Time"}
        )
        assert ds["latitude"].attrs["long_name"] == "source latitude"
        assert ds["longitude"].attrs["long_name"] == "Longitude"
        assert ds["time"].attrs["long_name"] == "Time"

    def test_no_long_name_by_default(self):
        ds = dataset.stamp_cf_coords(make_gridded())
        assert "long_name" not in ds["latitude"].attrs

    def test_missing_coords_are_skipped(self):
        ds = make_gridded().drop_vars("time")
        out = dataset.stamp_cf_coords(ds)
        assert out["latitude"].attrs["axis"] == "Y"

    def test_alias_names_are_not_stamped(self):
        # Only the canonical post-rename names are asserted; a fetcher stamps
        # after renaming to latitude/longitude.
        ds = dataset.stamp_cf_coords(make_gridded().rename({"latitude": "lat"}))
        assert ds["lat"].attrs == {}

    def test_returns_dataset(self):
        ds = make_gridded()
        assert dataset.stamp_cf_coords(ds) is ds


class TestCfDim:
    def test_resolves_stamped_coord(self):
        ds = make_gridded().rename({"latitude": "yy"})
        ds["yy"].attrs.update(standard_name="latitude", units="degrees_north")
        assert dataset.cf_dim(ds, "latitude") == "yy"

    def test_unresolvable_returns_none(self):
        ds = make_gridded().rename({"latitude": "yy"})
        assert dataset.cf_dim(ds, "latitude") is None

    def test_works_on_dataarrays(self):
        ds = dataset.stamp_cf_attrs(make_gridded())
        assert dataset.cf_dim(ds["precip"], "longitude") == "longitude"


class TestAutoVariable:
    def test_picks_the_first_data_var(self):
        assert dataset.auto_variable(make_gridded()) == "precip"

    def test_skips_grid_mapping_container_and_targets(self):
        ds = make_gridded()
        ds["crs"] = xr.DataArray(0, attrs={"grid_mapping_name": "latitude_longitude"})
        ds["precip"].attrs["grid_mapping"] = "crs"
        assert dataset.auto_variable(ds) == "precip"
        # A var NAMED by another var's grid_mapping attr is skipped even
        # without its own grid_mapping_name attr.
        ds2 = make_gridded()
        ds2["other"] = xr.DataArray(0)
        ds2["precip"].attrs["grid_mapping"] = "other"
        assert dataset.auto_variable(ds2) == "precip"

    def test_prefers_multidim_vars(self):
        ds = make_gridded()
        ds = ds[["precip"]]
        ds["scalar_first"] = xr.DataArray(1.0)
        ds = ds[["scalar_first", "precip"]]
        assert dataset.auto_variable(ds) == "precip"

    def test_falls_back_to_one_dim_candidate(self):
        ds = make_gridded()
        ds["series"] = ("time", np.ones(ds.sizes["time"]))
        ds = ds[["series"]]
        assert dataset.auto_variable(ds) == "series"

    def test_no_candidates_returns_none(self):
        ds = make_gridded()[[]]
        assert dataset.auto_variable(ds) is None


class TestStampCfDsg:
    def stamped(self, ds=None, var_attrs=None):
        ds = ds if ds is not None else make_station()
        var_attrs = (
            var_attrs
            if var_attrs is not None
            else {
                "precip": {
                    "standard_name": "lwe_thickness_of_precipitation_amount",
                    "long_name": "daily precipitation total",
                    "units": "mm",
                    "cell_methods": "time: sum",
                }
            }
        )
        return dataset.stamp_cf_dsg(
            ds,
            var_attrs,
            station_id_long_name="GHCN station identifier",
            name_long_name="station name",
        )

    def test_coordinate_attrs(self):
        ds = self.stamped()
        assert ds["latitude"].attrs == {
            "standard_name": "latitude",
            "long_name": "station latitude",
            "units": "degrees_north",
            "axis": "Y",
        }
        assert ds["longitude"].attrs == {
            "standard_name": "longitude",
            "long_name": "station longitude",
            "units": "degrees_east",
            "axis": "X",
        }
        assert ds["time"].attrs == {"standard_name": "time", "long_name": "time", "axis": "T"}
        assert ds["station_id"].attrs == {
            "cf_role": "timeseries_id",
            "long_name": "GHCN station identifier",
        }

    def test_data_variable_attrs_follow_the_coordinates_attr(self):
        ds = self.stamped()
        assert ds["precip"].attrs == {
            "coordinates": "latitude longitude time",
            "standard_name": "lwe_thickness_of_precipitation_amount",
            "long_name": "daily precipitation total",
            "units": "mm",
            "cell_methods": "time: sum",
        }
        # The load-bearing DSG attr is injected first; the caller's attrs
        # follow in their own insertion order.
        assert list(ds["precip"].attrs) == [
            "coordinates",
            "standard_name",
            "long_name",
            "units",
            "cell_methods",
        ]

    def test_var_attrs_without_standard_name(self):
        # A variable whose unit family backs no CF standard_name entry is
        # stamped without one (units + long_name alone is CF-valid).
        ds = self.stamped(
            var_attrs={
                "precip": {"units": "mm", "long_name": "precip", "cell_methods": "time: mean"}
            }
        )
        assert "standard_name" not in ds["precip"].attrs
        assert ds["precip"].attrs["coordinates"] == "latitude longitude time"

    def test_optional_name_coord(self):
        ds = make_station()
        ds = ds.assign_coords(name=("station_id", ["a", "b", "c"]))
        self.stamped(ds=ds)
        assert ds["name"].attrs == {"long_name": "station name"}

    def test_name_absent_is_skipped(self):
        ds = self.stamped()
        assert "name" not in ds.variables

    def test_missing_var_attrs_entry_raises_keyerror(self):
        with pytest.raises(KeyError):
            self.stamped(var_attrs={})

    def test_returns_dataset(self):
        ds = make_station()
        assert self.stamped(ds=ds) is ds


class TestVerifyCfDsg:
    def test_stamped_dataset_passes(self):
        ds = make_station()
        dataset.stamp_cf_dsg(
            ds,
            {"precip": {"units": "mm", "long_name": "precip"}},
            station_id_long_name="id",
            name_long_name="name",
        )
        dataset.verify_cf_dsg(ds)

    def test_unstamped_dataset_lists_every_problem(self):
        ds = make_station()
        ds = ds.rename({"latitude": "row", "longitude": "col", "time": "record"})
        with pytest.raises(DataError) as excinfo:
            dataset.verify_cf_dsg(ds)
        message = str(excinfo.value)
        assert message.startswith("CF-1.13 DSG verification failed before write:")
        assert "cf_role timeseries_id did not resolve to station_id" in message
        for name in ("latitude", "longitude", "time"):
            assert f"cf-xarray could not resolve the {name} coordinate" in message

    def test_missing_cf_role_alone(self):
        ds = make_station()
        dataset.stamp_cf_dsg(
            ds,
            {"precip": {"units": "mm"}},
            station_id_long_name="id",
            name_long_name="name",
        )
        del ds["station_id"].attrs["cf_role"]
        with pytest.raises(DataError, match="timeseries_id did not resolve"):
            dataset.verify_cf_dsg(ds)


class TestUdunitsError:
    @pytest.fixture(autouse=True)
    def _require_pint(self):
        pytest.importorskip("pint")
        pytest.importorskip("pint_xarray")

    @pytest.mark.parametrize("units", ["mm", "degC", "kg m-3", "mm day-1", "1"])
    def test_valid_units_return_none(self, units):
        assert dataset.udunits_error(units) is None

    def test_invalid_units_return_the_exception(self):
        from pint import UndefinedUnitError

        exc = dataset.udunits_error("definitely ! not a unit")
        assert isinstance(exc, UndefinedUnitError)
        assert "definitely" in str(exc)

    def test_blank_units_pass_through(self):
        # None / "" pass through without raising; rejecting blanks is the caller's guard.
        assert dataset.udunits_error(None) is None
        assert dataset.udunits_error("") is None

    def test_catch_widens_the_converted_failures(self):
        from pint import UndefinedUnitError

        class Boom(Exception):
            pass

        # Default catch converts pint parse errors; a narrow catch re-raises.
        assert dataset.udunits_error("degC", catch=(Exception,)) is None
        exc = dataset.udunits_error("definitely ! not a unit", catch=(Exception,))
        assert isinstance(exc, UndefinedUnitError)
        with pytest.raises(UndefinedUnitError):
            dataset.udunits_error("definitely ! not a unit", catch=(Boom,))


class TestCfAxesMissing:
    def test_all_resolved(self):
        ds = dataset.stamp_cf_attrs(make_gridded())
        assert dataset.cf_axes_missing(ds) == []

    def test_partially_stamped_dataset_misses_x_and_y(self):
        # Axis resolution keys on the CF attrs (an unrenamed bare `time` name
        # resolves the "time" coordinate, not the "T" axis), so only the
        # stamped coord resolves.
        ds = make_gridded().rename({"latitude": "row", "longitude": "col"})
        ds["time"].attrs["axis"] = "T"
        assert dataset.cf_axes_missing(ds) == ["X", "Y"]

    def test_all_missing_on_unstamped_dataset(self):
        assert dataset.cf_axes_missing(make_gridded()) == ["X", "Y", "T"]

    def test_custom_axes(self):
        ds = make_gridded()
        ds["time"].attrs["axis"] = "T"
        assert dataset.cf_axes_missing(ds, axes=("T",)) == []
        assert dataset.cf_axes_missing(ds, axes=("Y",)) == ["Y"]
