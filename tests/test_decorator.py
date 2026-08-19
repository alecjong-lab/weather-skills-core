"""Decorator behavior for the stacked @weather_skill.argument API."""

import json
from pathlib import Path

import numpy as np
import pytest
import xarray as xr
from conftest import make_data, make_forecast
from PIL import Image

from weather_skills_core import Dataset
from weather_skills_core.decorator import weather_skill
from weather_skills_core.provenance import HISTORY_ATTR, load_figure_history, load_history
from weather_skills_core.standard_args import rewrite_bbox_argv


class TestRewriteBbox:
    def test_equals_form(self):
        assert rewrite_bbox_argv(["--bbox", "-10/20/-20/30"]) == ["--bbox=-10/20/-20/30"]


class TestParser:
    def test_basic_flags(self):
        @weather_skill(name="s", version="1.0.0")
        @weather_skill.argument("-i", "--input", type=Dataset("observations"), required=True)
        @weather_skill.argument("--bbox")
        def skill(input, output, bbox, **kwargs):
            return input

        dests = {a.dest for a in skill.parser._actions if a.dest != "help"}
        assert dests == {"input", "output", "bbox"}

    def test_dates_range(self):
        @weather_skill(name="s", version="1.0.0")
        @weather_skill.argument("--start-time", required=True)
        @weather_skill.argument("--end-time", required=True)
        def skill(output, start_time, end_time, **kwargs):
            return make_data()

        dests = {a.dest for a in skill.parser._actions if a.dest != "help"}
        assert "start_time" in dests and "end_time" in dests

    def test_variable_append(self):
        @weather_skill(name="s", version="1.0.0")
        @weather_skill.argument("--variable", "-v", action="append", required=True)
        def skill(output, variable, **kwargs):
            return make_data()

        action = next(a for a in skill.parser._actions if a.dest == "variable")
        assert action.required is True
        assert action.option_strings == ["--variable", "-v"]

    def test_extra_argument(self):
        @weather_skill(name="s", version="1.0.0")
        @weather_skill.argument("--smoothing", "-s", type=int, default=1)
        def skill(output, smoothing, **kwargs):
            return make_data()

        action = next(a for a in skill.parser._actions if a.dest == "smoothing")
        assert action.default == 1

    def test_requires_kwargs(self):
        with pytest.raises(TypeError, match=r"\*\*kwargs"):

            @weather_skill(name="s", version="1.0.0")
            def skill(ds):
                return ds

    def test_argument_order_matches_source(self):
        @weather_skill(name="s", version="1.0.0")
        @weather_skill.argument("--date", required=True)
        @weather_skill.argument("--bbox", help="Study area.")
        def skill(output, date, bbox, **kwargs):
            return make_data()

        dests = [a.dest for a in skill.parser._actions if a.dest in ("date", "bbox")]
        assert dests == ["date", "bbox"]

    def test_canonical_bbox_help_appended(self):
        @weather_skill(name="s", version="1.0.0")
        @weather_skill.argument("--bbox", help="Study area.")
        def skill(output, bbox, **kwargs):
            return make_data()

        action = next(a for a in skill.parser._actions if a.dest == "bbox")
        assert "Study area." in action.help
        assert "N/W/S/E" in action.help

    def test_name_and_version_are_keyword_only(self):
        with pytest.raises(TypeError):
            weather_skill("s", "1.0.0")  # type: ignore[misc]

    def test_dataset_and_comma_and(self):
        d = Dataset("lat, lon")
        assert d.io_spec.alternatives == (frozenset({"lat", "lon"}),)
        d2 = Dataset(["forecast", "ensemble_forecast"])
        assert len(d2.io_spec.alternatives) == 2


class TestRunLoop:
    def test_copy_dataset(self, tmp_path):
        src = tmp_path / "in.zarr"
        out = tmp_path / "out.zarr"
        make_data().to_zarr(src, mode="w", consolidated=True)

        @weather_skill(name="copy", version="0.1.0")
        @weather_skill.argument("-i", "--input", type=Dataset("observations"), required=True)
        def copy(input, output, **kwargs):
            return input

        copy(["-i", str(src), "-o", str(out)])
        assert out.exists()
        assert load_history(out)[-1]["skill"] == "copy"

    def test_bbox_passed_as_tuple(self, tmp_path):
        from datetime import date

        src = tmp_path / "in.zarr"
        out = tmp_path / "out.zarr"
        make_data().to_zarr(src, mode="w", consolidated=True)
        seen = {}

        @weather_skill(name="s", version="0.1.0")
        @weather_skill.argument("-i", "--input", type=Dataset("observations"), required=True)
        @weather_skill.argument("--bbox", required=True)
        @weather_skill.argument("--start-time", required=True)
        @weather_skill.argument("--end-time", required=True)
        def skill(input, output, bbox, start_time, end_time, **kwargs):
            seen["bbox"] = bbox
            seen["start_time"] = start_time
            seen["end_time"] = end_time
            return input

        skill(
            [
                "-i",
                str(src),
                "-o",
                str(out),
                "--bbox",
                "10/20/0/30",
                "--start-time",
                "2026-01-01",
                "--end-time",
                "2026-01-10",
            ]
        )
        assert seen["bbox"] == (10.0, 20.0, 0.0, 30.0)
        assert seen["start_time"] == date(2026, 1, 1)
        assert isinstance(seen["start_time"], date)
        assert seen["end_time"] == date(2026, 1, 10)

    def test_region_cli_string_becomes_geodataframe(self, tmp_path):
        import geopandas as gpd

        src = tmp_path / "in.zarr"
        out = tmp_path / "out.zarr"
        make_data().to_zarr(src, mode="w", consolidated=True)
        seen = {}

        @weather_skill(name="s", version="0.1.0")
        @weather_skill.argument("-i", "--input", type=Dataset("observations"), required=True)
        @weather_skill.argument("--region")
        def skill(input, output, region=None, **kwargs):
            seen["region"] = region
            seen["bbox"] = kwargs.get("bbox")
            return input

        skill(["-i", str(src), "-o", str(out), "--region", "Kenya"])
        assert isinstance(seen["region"], gpd.GeoDataFrame)
        assert list(seen["region"]["name"]) == ["Kenya"]
        assert seen["bbox"] is not None
        n, w, s, e = seen["bbox"]
        assert n > s and w < e
        assert load_history(out)[-1]["args"]["region"] == "Kenya"

    def test_start_after_end_exits(self, tmp_path):
        out = tmp_path / "out.zarr"

        @weather_skill(name="s", version="0.1.0")
        @weather_skill.argument("--start-time", required=True)
        @weather_skill.argument("--end-time", required=True)
        def skill(output, start_time, end_time, **kwargs):
            return make_data()

        with pytest.raises(SystemExit) as exc:
            skill(
                [
                    "-o",
                    str(out),
                    "--start-time",
                    "2026-01-10",
                    "--end-time",
                    "2026-01-01",
                ]
            )
        assert exc.value.code == 2

    def test_date_parsed(self, tmp_path):
        out = tmp_path / "out.zarr"
        seen = {}

        @weather_skill(name="s", version="0.1.0")
        @weather_skill.argument("--date", required=True)
        def skill(output, date, **kwargs):
            seen["date"] = date.isoformat()
            return make_data()

        skill(["-o", str(out), "--date", "2026-01-15"])
        assert seen["date"] == "2026-01-15"
        assert load_history(out)[-1]["args"]["date"] == "2026-01-15"

    def test_two_inputs(self, tmp_path):
        a = tmp_path / "a.zarr"
        b = tmp_path / "b.zarr"
        out = tmp_path / "out.zarr"
        make_data().to_zarr(a, mode="w", consolidated=True)
        make_data(fill=2.0).to_zarr(b, mode="w", consolidated=True)

        @weather_skill(name="s", version="1.0.0")
        @weather_skill.argument("-i", "--input", type=Dataset("observations"), nargs=2, required=True)
        def skill(input, output, **kwargs):
            return input[0]

        skill(["-i", str(a), str(b), "-o", str(out)])
        assert out.exists()

    def test_path_input_not_dataset(self, tmp_path):
        raw = tmp_path / "raw.bin"
        raw.write_bytes(b"abc")
        out = tmp_path / "out.zarr"

        @weather_skill(name="wrap", version="0.1.0")
        @weather_skill.argument("-i", "--input", type=Path, required=True)
        def wrap(input, output, **kwargs):
            assert input == raw
            return make_data()

        wrap(["-i", str(raw), "-o", str(out)])
        assert out.exists()

    def test_figure_output(self, tmp_path):
        src = tmp_path / "in.zarr"
        out = tmp_path / "plot.png"
        make_data().to_zarr(src, mode="w", consolidated=True)

        @weather_skill(name="plot", version="0.1.0")
        @weather_skill.argument("-i", "--input", type=Dataset("observations"), required=True)
        def plot(input, output, **kwargs):
            Image.new("RGB", (8, 8), color=(1, 2, 3)).save(output)
            return output

        plot(["-i", str(src), "-o", str(out)])
        assert out.exists()
        assert load_figure_history(out)[-1]["skill"] == "plot"

    def test_figure_wrong_path_exits(self, tmp_path):
        out = tmp_path / "plot.png"
        wrong = tmp_path / "other.png"

        @weather_skill(name="plot", version="0.1.0")
        def plot(output, **kwargs):
            Image.new("RGB", (4, 4)).save(wrong)
            return wrong

        with pytest.raises(SystemExit) as exc:
            plot(["-o", str(out)])
        assert exc.value.code == 1

    def test_output_kwarg_single(self, tmp_path):
        src = tmp_path / "in.zarr"
        out = tmp_path / "out.zarr"
        make_data().to_zarr(src, mode="w", consolidated=True)
        seen = {}

        @weather_skill(name="copy", version="0.1.0")
        @weather_skill.argument("-i", "--input", type=Dataset("observations"), required=True)
        def copy(input, output, **kwargs):
            seen["output"] = output
            return input

        copy(["-i", str(src), "-o", str(out)])
        assert seen["output"] == out

    def test_no_artifact(self, tmp_path):
        src = tmp_path / "in.zarr"
        make_data().to_zarr(src, mode="w", consolidated=True)

        @weather_skill(name="inspect", version="0.1.0", output=False)
        @weather_skill.argument("-i", "--input", type=Dataset("observations"), required=True)
        def inspect(input, **kwargs):
            return {"n": int(input.sizes["time"])}

        assert inspect(["-i", str(src)]) == {"n": 2}

    def test_rejects_manual_output_flag(self):
        with pytest.raises(ValueError, match="do not declare -o/--output"):

            @weather_skill(name="s", version="0.1.0")
            @weather_skill.argument("-o", "--output", type=Path, required=True)
            def skill(output, **kwargs):
                return make_data()

    def test_multi_output_writes_both(self, tmp_path):
        src = tmp_path / "in.zarr"
        a = tmp_path / "a.zarr"
        b = tmp_path / "b.zarr"
        make_data().to_zarr(src, mode="w", consolidated=True)

        @weather_skill(name="split", version="0.1.0")
        @weather_skill.argument("-i", "--input", type=Dataset("observations"), required=True)
        def split(input, output, **kwargs):
            assert output == [a, b]
            return input, input

        split(["-i", str(src), "-o", str(a), "-o", str(b)])
        assert a.exists() and b.exists()

    def test_output_count_mismatch_exits(self, tmp_path):
        src = tmp_path / "in.zarr"
        a = tmp_path / "a.zarr"
        b = tmp_path / "b.zarr"
        make_data().to_zarr(src, mode="w", consolidated=True)

        @weather_skill(name="copy", version="0.1.0")
        @weather_skill.argument("-i", "--input", type=Dataset("observations"), required=True)
        def copy(input, output, **kwargs):
            return input  # one Dataset, two -o paths

        with pytest.raises(SystemExit) as exc:
            copy(["-i", str(src), "-o", str(a), "-o", str(b)])
        assert exc.value.code == 1

    def test_any_accepts_shapes(self, tmp_path):
        src = tmp_path / "in.zarr"
        out = tmp_path / "out.zarr"
        make_data().to_zarr(src, mode="w", consolidated=True)

        @weather_skill(name="s", version="0.1.0")
        @weather_skill.argument("-i", "--input", type=Dataset("any"), required=True)
        def skill(input, output, **kwargs):
            return input

        skill(["-i", str(src), "-o", str(out)])
        assert out.exists()

    def test_variadic_inputs(self, tmp_path):
        paths = []
        for i in range(3):
            p = tmp_path / f"{i}.zarr"
            make_data(fill=float(i)).to_zarr(p, mode="w", consolidated=True)
            paths.append(p)
        out = tmp_path / "out.zarr"
        seen = {}

        @weather_skill(name="cat", version="0.1.0")
        @weather_skill.argument("-i", "--input", type=Dataset("any"), nargs="+", required=True)
        def cat(input, output, **kwargs):
            seen["n"] = len(input)
            return input[0]

        argv = ["-i", *[str(p) for p in paths], "-o", str(out)]
        cat(argv)
        assert seen["n"] == 3

    def test_negative_bbox_latitude(self, tmp_path):
        out = tmp_path / "out.zarr"
        seen = {}

        @weather_skill(name="s", version="0.1.0")
        @weather_skill.argument("--bbox", required=True)
        def skill(output, bbox, **kwargs):
            seen["bbox"] = bbox
            return make_data()

        skill(["-o", str(out), "--bbox", "-10/20/-20/30"])
        assert seen["bbox"] == (-10.0, 20.0, -20.0, 30.0)

    def test_history_args_json(self, tmp_path):
        out = tmp_path / "out.zarr"

        @weather_skill(name="s", version="0.1.0")
        @weather_skill.argument("--variable", "-v", action="append", required=True)
        def skill(output, variable, **kwargs):
            return make_data()

        skill(["-o", str(out), "-v", "precip", "-v", "temp"])
        entry = load_history(out)[-1]
        assert entry["args"]["variable"] == ["precip", "temp"]
        assert "output" not in entry["args"]
        json.dumps(entry)

    def test_attrs_merge_from_input(self, tmp_path):
        src = tmp_path / "in.zarr"
        out = tmp_path / "out.zarr"
        ds = make_data()
        ds.attrs["weather_skills_source"] = "test-src"
        ds.to_zarr(src, mode="w", consolidated=True)

        @weather_skill(name="copy", version="0.1.0")
        @weather_skill.argument("-i", "--input", type=Dataset("observations"), required=True)
        def copy(input, output, **kwargs):
            return input.assign(precip=input["precip"] * 2)

        copy(["-i", str(src), "-o", str(out)])
        written = xr.open_zarr(out, consolidated=True)
        assert written.attrs.get("weather_skills_source") == "test-src"
        assert HISTORY_ATTR in written.attrs

    def test_write_normalizes_step_and_fills_stripped_units(self, tmp_path):
        src = tmp_path / "in.zarr"
        out = tmp_path / "out.zarr"
        ds = make_forecast(n_number=0, n_step=3)
        ds = ds.assign_coords(step=ds["step"].values.astype("timedelta64[us]"))
        ds.to_zarr(src, mode="w", consolidated=True)

        @weather_skill(name="strip", version="0.1.0", allow_precip_totals=True)
        @weather_skill.argument("-i", "--input", type=Dataset("forecast"), required=True)
        def strip(input, output, **kwargs):
            out_ds = input.copy(deep=True)
            for name in out_ds.data_vars:
                out_ds[name].attrs.pop("units", None)
            return out_ds

        strip(["-i", str(src), "-o", str(out)])
        written = xr.open_zarr(out, consolidated=True)
        assert written["step"].dtype == np.dtype("timedelta64[ns]")
        assert "units" in written["tp"].attrs
        assert written["tp"].attrs["units"] in ("mm day-1", "millimeter / day")

    def test_write_stamps_amount_standard_name(self, tmp_path):
        src = tmp_path / "in.zarr"
        out = tmp_path / "out.zarr"
        ds = make_data(name="tp", units="kg m-2")
        ds["tp"].attrs["standard_name"] = "lwe_precipitation_rate"
        ds.to_zarr(src, mode="w", consolidated=True)

        @weather_skill(name="copy", version="0.1.0", allow_precip_totals=True)
        @weather_skill.argument("-i", "--input", type=Dataset("observations"), required=True)
        def copy(input, output, **kwargs):
            return input

        copy(["-i", str(src), "-o", str(out)])
        written = xr.open_zarr(out, consolidated=True)
        assert written["tp"].attrs["standard_name"] == (
            "lwe_thickness_of_precipitation_amount"
        )

    def test_none_return_skips_write(self, tmp_path):
        out = tmp_path / "out.eml"

        @weather_skill(name="compose", version="0.1.0")
        def compose(output, **kwargs):
            output.write_text("ok")

        compose(["-o", str(out)])
        assert out.read_text() == "ok"
