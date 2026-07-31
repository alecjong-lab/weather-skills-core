import numpy as np
import pytest
import xarray as xr


def make_data(
    n_time=2,
    lats=(1.0, 2.0, 3.0),
    lons=(10.0, 11.0, 12.0, 13.0),
    name="precip",
    fill=1.0,
    start="2026-01-01",
    units="mm",
):
    times = np.arange(np.datetime64(start), np.datetime64(start) + np.timedelta64(n_time, "D"))
    data = np.full((n_time, len(lats), len(lons)), fill)
    ds = xr.Dataset(
        {name: (("time", "latitude", "longitude"), data)},
        coords={
            "time": times.astype("datetime64[ns]"),
            "latitude": list(lats),
            "longitude": list(lons),
        },
    )
    if units is not None:
        ds[name].attrs["units"] = units
    return ds


# Back-compat alias for older test names during migration
make_gridded = make_data


def make_forecast(n_number=3, n_step=4):
    coords = {
        "step": np.array([np.timedelta64(i, "D") for i in range(n_step)]),
        "time": np.datetime64("2026-01-01", "ns"),
        "latitude": [0.0, 1.0],
        "longitude": [10.0, 11.0],
    }
    if n_number:
        data = np.ones((n_number, n_step, 2, 2))
        coords["number"] = np.arange(n_number)
        ds = xr.Dataset(
            {"tp": (("number", "step", "latitude", "longitude"), data)},
            coords=coords,
        )
    else:
        data = np.ones((n_step, 2, 2))
        ds = xr.Dataset(
            {"tp": (("step", "latitude", "longitude"), data)},
            coords=coords,
        )
    ds["tp"].attrs["units"] = "mm"
    return ds


def make_vertical_forecast(n_level=3, n_step=4):
    levels = [850.0, 700.0, 500.0][:n_level]
    return xr.Dataset(
        {
            "t": (
                ("step", "level", "latitude", "longitude"),
                np.ones((n_step, n_level, 2, 2)),
            )
        },
        coords={
            "step": np.array([np.timedelta64(i, "D") for i in range(n_step)]),
            "time": np.datetime64("2026-01-01", "ns"),
            "level": levels,
            "latitude": [0.0, 1.0],
            "longitude": [10.0, 11.0],
        },
    )


def make_station(n_station=3, n_time=2):
    ids = [f"TA{i:04d}" for i in range(n_station)]
    times = np.arange(
        np.datetime64("2026-01-01"), np.datetime64("2026-01-01") + np.timedelta64(n_time, "D")
    )
    return xr.Dataset(
        {"precip": (("time", "station_id"), np.ones((n_time, n_station)))},
        coords={
            "time": times.astype("datetime64[ns]"),
            "station_id": ids,
            "latitude": ("station_id", np.linspace(-1.0, 1.0, n_station)),
            "longitude": ("station_id", np.linspace(36.0, 38.0, n_station)),
        },
    )


@pytest.fixture
def data_store(tmp_path):
    path = tmp_path / "in.zarr"
    make_data().to_zarr(path, mode="w", consolidated=True)
    return path


@pytest.fixture
def gridded_store(data_store):
    return data_store
