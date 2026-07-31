"""Decorator behavior for the stacked @weather_skill.argument API."""

import json

import pytest
import xarray as xr
from conftest import make_data
from PIL import Image

from weather_skills_core.decorator import weather_skill
from weather_skills_core.standard_args import rewrite_bbox_argv
from weather_skills_core.provenance import HISTORY_ATTR, load_history, load_figure_history


class TestRewriteBbox:
    def test_equals_form(self):
        assert rewrite_bbox_argv(["--bbox", "-10/20/-20/30"]) == ["--bbox=-10/20/-20/30"]


class TestParser:
    def test_basic_flags(self):
        @weather_skill(name="s", version="1.0.0", inputs=["data"], outputs=["data"])
        @weather_skill.argument("--bbox")
        def skill(ds, bbox, **kwargs):
            return ds

        dests = {a.dest for a in skill.parser._actions if a.dest != "help"}
        assert dests == {"input", "output", "bbox"}

    def test_dates_range(self):
        @weather_skill(name="s", version="1.0.0", outputs=["data"])
        @weather_skill.argument("--start-time", required=True)
        @weather_skill.argument("--end-time", required=True)
        def skill(start_time, end_time, **kwargs):
            return make_data()

        dests = {a.dest for a in skill.parser._actions if a.dest != "help"}
        assert "start_time" in dests and "end_time" in dests

    def test_variable_append(self):
        @weather_skill(name="s", version="1.0.0", outputs=["data"])
        @weather_skill.argument("--variable", "-v", action="append", required=True)
        def skill(variable, **kwargs):
            return make_data()

        action = next(a for a in skill.parser._actions if a.dest == "variable")
        assert action.required is True
        assert action.option_strings == ["--variable", "-v"]

    def test_extra_argument(self):
        @weather_skill(name="s", version="1.0.0", outputs=["data"])
        @weather_skill.argument("--smoothing", "-s", type=int, default=1)
        def skill(smoothing, **kwargs):
            return make_data()

        action = next(a for a in skill.parser._actions if a.dest == "smoothing")
        assert action.default == 1

    def test_requires_kwargs(self):
        with pytest.raises(TypeError, match=r"\*\*kwargs"):

            @weather_skill(name="s", version="1.0.0", outputs=["data"])
            def skill(ds):
                return ds

    def test_argument_order_matches_source(self):
        @weather_skill(name="s", version="1.0.0", outputs=["data"])
        @weather_skill.argument("--date", required=True)
        @weather_skill.argument("--bbox", help="Study area.")
        def skill(date, bbox, **kwargs):
            return make_data()

        dests = [a.dest for a in skill.parser._actions if a.dest in ("date", "bbox")]
        assert dests == ["date", "bbox"]

    def test_canonical_bbox_help_appended(self):
        @weather_skill(name="s", version="1.0.0", outputs=["data"])
        @weather_skill.argument("--bbox", help="Study area.")
        def skill(bbox, **kwargs):
            return make_data()

        action = next(a for a in skill.parser._actions if a.dest == "bbox")
        assert "Study area." in action.help
        assert "N/W/S/E" in action.help

    def test_name_and_version_are_keyword_only(self):
        with pytest.raises(TypeError):
            weather_skill("s", "1.0.0", outputs=["data"])  # type: ignore[misc]


class TestRunLoop:
    def test_copy_dataset(self, tmp_path):
        src = tmp_path / "in.zarr"
        out = tmp_path / "out.zarr"
        make_data().to_zarr(src, mode="w", consolidated=True)

        @weather_skill(name="copy", version="0.1.0", inputs=["data"], outputs=["data"])
        def copy(ds, **kwargs):
            return ds

        copy(["-i", str(src), "-o", str(out)])
        assert out.exists()
        assert load_history(out)[-1]["skill"] == "copy"

    def test_bbox_passed_as_tuple(self, tmp_path):
        src = tmp_path / "in.zarr"
        out = tmp_path / "out.zarr"
        make_data().to_zarr(src, mode="w", consolidated=True)
        seen = {}

        @weather_skill(name="s", version="0.1.0", inputs=["data"], outputs=["data"])
        @weather_skill.argument("--bbox", required=True)
        @weather_skill.argument("--start-time", required=True)
        @weather_skill.argument("--end-time", required=True)
        def skill(ds, bbox, start_time, end_time, **kwargs):
            seen["bbox"] = bbox
            seen["start"] = start_time.isoformat()
            return ds

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
        assert seen["start"] == "2026-01-01"

    def test_start_after_end_exits(self, tmp_path):
        out = tmp_path / "out.zarr"

        @weather_skill(name="s", version="0.1.0", outputs=["data"])
        @weather_skill.argument("--start-time", required=True)
        @weather_skill.argument("--end-time", required=True)
        def skill(start_time, end_time, **kwargs):
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

        @weather_skill(name="s", version="0.1.0", outputs=["data"])
        @weather_skill.argument("--date", required=True)
        def skill(date, **kwargs):
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

        @weather_skill(name="s", version="1.0.0", inputs=["data", "data"], outputs=["data"])
        def skill(ds_a, ds_b, **kwargs):
            return ds_a

        skill(["-i", str(a), "-i", str(b), "-o", str(out)])
        assert out.exists()

    def test_unstructured_input(self, tmp_path):
        raw = tmp_path / "raw.bin"
        raw.write_bytes(b"abc")
        out = tmp_path / "out.zarr"

        @weather_skill(name="wrap", version="0.1.0", inputs=["unstructured"], outputs=["data"])
        def wrap(path, **kwargs):
            assert path == raw
            return make_data()

        wrap(["-i", str(raw), "-o", str(out)])
        assert out.exists()

    def test_figure_output(self, tmp_path):
        src = tmp_path / "in.zarr"
        out = tmp_path / "plot.png"
        make_data().to_zarr(src, mode="w", consolidated=True)

        @weather_skill(name="plot", version="0.1.0", inputs=["data"], outputs=["figure"])
        def plot(ds, output, **kwargs):
            Image.new("RGB", (8, 8), color=(1, 2, 3)).save(output)
            return output

        plot(["-i", str(src), "-o", str(out)])
        assert out.exists()
        assert load_figure_history(out)[-1]["skill"] == "plot"

    def test_figure_wrong_path_exits(self, tmp_path):
        out = tmp_path / "plot.png"
        wrong = tmp_path / "other.png"

        @weather_skill(name="plot", version="0.1.0", outputs=["figure"])
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

        @weather_skill(name="copy", version="0.1.0", inputs=["data"], outputs=["data"])
        def copy(ds, output, **kwargs):
            seen["output"] = output
            return ds

        copy(["-i", str(src), "-o", str(out)])
        assert seen["output"] == out

    def test_multi_output(self, tmp_path):
        src = tmp_path / "in.zarr"
        a = tmp_path / "a.zarr"
        b = tmp_path / "b.zarr"
        make_data().to_zarr(src, mode="w", consolidated=True)

        @weather_skill(
            name="split", version="0.1.0", inputs=["data"], outputs=["data", "data"]
        )
        def split(ds, output, **kwargs):
            assert output == [a, b]
            return ds, ds

        split(["-i", str(src), "-o", str(a), "-o", str(b)])
        assert a.exists() and b.exists()

    def test_no_artifact(self, tmp_path):
        src = tmp_path / "in.zarr"
        make_data().to_zarr(src, mode="w", consolidated=True)

        @weather_skill(name="inspect", version="0.1.0", inputs=["data"])
        def inspect(ds, **kwargs):
            return {"n": int(ds.sizes["time"])}

        assert inspect(["-i", str(src)]) == {"n": 2}

    def test_any_accepts_shapes(self, tmp_path):
        src = tmp_path / "in.zarr"
        out = tmp_path / "out.zarr"
        make_data().to_zarr(src, mode="w", consolidated=True)

        @weather_skill(name="s", version="0.1.0", inputs=["any"], outputs=["any"])
        def skill(ds, **kwargs):
            return ds

        skill(["-i", str(src), "-o", str(out)])
        assert out.exists()

    def test_variadic_rejects_mixed(self):
        with pytest.raises(ValueError, match="variadic"):

            @weather_skill(
                name="s", version="0.1.0", inputs=["data", "any+"], outputs=["any"]
            )
            def skill(items, **kwargs):
                return items[0]

    def test_variadic_inputs(self, tmp_path):
        paths = []
        for i in range(3):
            p = tmp_path / f"{i}.zarr"
            make_data(fill=float(i)).to_zarr(p, mode="w", consolidated=True)
            paths.append(p)
        out = tmp_path / "out.zarr"
        seen = {}

        @weather_skill(name="cat", version="0.1.0", inputs=["any+"], outputs=["any"])
        def cat(datasets, **kwargs):
            seen["n"] = len(datasets)
            return datasets[0]

        argv = []
        for p in paths:
            argv.extend(["-i", str(p)])
        argv.extend(["-o", str(out)])
        cat(argv)
        assert seen["n"] == 3

    def test_variadic_requires_at_least_one(self, tmp_path):
        out = tmp_path / "out.zarr"

        @weather_skill(name="cat", version="0.1.0", inputs=["any+"], outputs=["any"])
        def cat(datasets, **kwargs):
            return datasets[0]

        with pytest.raises(SystemExit) as exc:
            cat(["-o", str(out)])
        assert exc.value.code == 2

    def test_negative_bbox_latitude(self, tmp_path):
        out = tmp_path / "out.zarr"
        seen = {}

        @weather_skill(name="s", version="0.1.0", outputs=["data"])
        @weather_skill.argument("--bbox", required=True)
        def skill(bbox, **kwargs):
            seen["bbox"] = bbox
            return make_data()

        skill(["-o", str(out), "--bbox", "-10/20/-20/30"])
        assert seen["bbox"] == (-10.0, 20.0, -20.0, 30.0)

    def test_history_args_json(self, tmp_path):
        out = tmp_path / "out.zarr"

        @weather_skill(name="s", version="0.1.0", outputs=["data"])
        @weather_skill.argument("--variable", "-v", action="append", required=True)
        def skill(variable, **kwargs):
            return make_data()

        skill(["-o", str(out), "-v", "precip", "-v", "temp"])
        entry = load_history(out)[-1]
        assert entry["args"]["variable"] == ["precip", "temp"]
        json.dumps(entry)

    def test_attrs_merge_from_input(self, tmp_path):
        src = tmp_path / "in.zarr"
        out = tmp_path / "out.zarr"
        ds = make_data()
        ds.attrs["weather_skills_source"] = "test-src"
        ds.to_zarr(src, mode="w", consolidated=True)

        @weather_skill(name="copy", version="0.1.0", inputs=["data"], outputs=["data"])
        def copy(ds, **kwargs):
            return ds.assign(precip=ds["precip"] * 2)

        copy(["-i", str(src), "-o", str(out)])
        written = xr.open_zarr(out, consolidated=True)
        assert written.attrs.get("weather_skills_source") == "test-src"
        assert HISTORY_ATTR in written.attrs
