"""Decorator behavior for the simplified @weather_skill API."""

import json

import pytest
import xarray as xr
from conftest import make_data
from PIL import Image

from weather_skills_core.decorator import rewrite_bbox_argv, weather_skill
from weather_skills_core.provenance import HISTORY_ATTR, load_history, load_visualization_history


class TestRewriteBbox:
    def test_equals_form(self):
        assert rewrite_bbox_argv(["--bbox", "-10/20/-20/30"]) == ["--bbox=-10/20/-20/30"]


class TestParser:
    def test_basic_flags(self):
        @weather_skill("s", "1.0.0", inputs=["data"], outputs=["data"], region="optional")
        def skill(ds, bbox):
            return ds

        dests = {a.dest for a in skill.parser._actions if a.dest != "help"}
        assert dests == {"input", "output", "bbox"}

    def test_dates_range(self):
        @weather_skill("s", "1.0.0", outputs=["data"], dates="range")
        def skill(start_time, end_time):
            return make_data()

        dests = {a.dest for a in skill.parser._actions if a.dest != "help"}
        assert "start" in dests and "end" in dests

    def test_variable_modes(self):
        @weather_skill("s", "1.0.0", outputs=["data"], variable="multiple_required")
        def skill(variable):
            return make_data()

        action = next(a for a in skill.parser._actions if a.dest == "variable")
        assert action.required is True
        assert action.option_strings == ["--variable", "-v"]

    def test_extra_args_tuple(self):
        @weather_skill(
            "s",
            "1.0.0",
            outputs=["data"],
            extra_args=[(("--smoothing", "-s"), {"type": int, "default": 1})],
        )
        def skill(smoothing):
            return make_data()

        dests = {a.dest for a in skill.parser._actions if a.dest != "help"}
        assert "smoothing" in dests


class TestTransform:
    def test_dataset_write_and_stamp(self, data_store, tmp_path):
        out = tmp_path / "out.zarr"

        @weather_skill("double", "0.1.0", inputs=["data"], outputs=["data"])
        def double(ds):
            return ds * 2

        double(["-i", str(data_store), "-o", str(out)])
        with xr.open_zarr(out, consolidated=True) as ds:
            assert float(ds["precip"].values.mean()) == 2.0
            history = json.loads(ds.attrs[HISTORY_ATTR])
        assert history[-1]["skill"] == "double"
        assert history[-1]["version"] == "0.1.0"

    def test_path_return_restamps(self, data_store, tmp_path):
        out = tmp_path / "out.zarr"

        @weather_skill("copy", "0.1.0", inputs=["data"], outputs=["data"])
        def copy(ds):
            ds.to_zarr(out, mode="w", consolidated=True)
            return out

        copy(["-i", str(data_store), "-o", str(out)])
        history = load_history(out)
        assert history[-1]["skill"] == "copy"

    def test_bbox_and_dates(self, data_store, tmp_path):
        out = tmp_path / "out.zarr"
        seen = {}

        @weather_skill(
            "clip",
            "0.1.0",
            inputs=["data"],
            outputs=["data"],
            dates="range",
            region="required",
        )
        def clip(ds, bbox, start_time, end_time):
            seen["bbox"] = bbox
            seen["start"] = start_time.isoformat()
            seen["end"] = end_time.isoformat()
            return ds

        clip(
            [
                "-i",
                str(data_store),
                "-o",
                str(out),
                "--start",
                "2026-01-01",
                "--end",
                "2026-01-10",
                "--bbox",
                "3/10/1/13",
            ]
        )
        assert seen["bbox"] == (3.0, 10.0, 1.0, 13.0)
        assert seen["start"] == "2026-01-01"
        assert load_history(out)[-1]["args"]["start"] == "2026-01-01"

    def test_wrong_input_arity_exits(self, data_store, tmp_path):
        @weather_skill("s", "1.0.0", inputs=["data", "data"], outputs=["data"])
        def skill(a, b):
            return a

        with pytest.raises(SystemExit) as exc:
            skill(["-i", str(data_store), "-o", str(tmp_path / "o.zarr")])
        assert exc.value.code == 2


class TestFetcher:
    def test_no_input(self, tmp_path):
        out = tmp_path / "out.zarr"

        @weather_skill("fetch", "0.1.0", outputs=["data"], dates="single")
        def fetch(date):
            return make_data(start=date.isoformat())

        fetch(["-o", str(out), "--date", "2026-02-01"])
        history = load_history(out)
        assert history[-1]["input"] is None
        assert history[-1]["args"]["date"] == "2026-02-01"


class TestUnstructured:
    def test_passes_path(self, tmp_path):
        src = tmp_path / "blob.bin"
        src.write_bytes(b"hello")
        out = tmp_path / "out.zarr"
        seen = {}

        @weather_skill("wrap", "0.1.0", inputs=["unstructured"], outputs=["data"])
        def wrap(path):
            seen["path"] = path
            return make_data()

        wrap(["-i", str(src), "-o", str(out)])
        assert seen["path"] == src


class TestVisualization:
    def test_png_stamp(self, data_store, tmp_path):
        out = tmp_path / "plot.png"

        @weather_skill("plot", "0.1.0", inputs=["data"], outputs=["visualization"])
        def plot(ds):
            Image.new("RGB", (8, 8), color=(255, 0, 0)).save(out)
            return out

        plot(["-i", str(data_store), "-o", str(out)])
        history = load_visualization_history(out)
        assert history[-1]["skill"] == "plot"

    def test_html_stamp(self, tmp_path):
        out = tmp_path / "fig.html"

        @weather_skill("plot", "0.1.0", outputs=["visualization"])
        def plot():
            out.write_text("<html><head></head><body>hi</body></html>", encoding="utf-8")
            return out

        plot(["-o", str(out)])
        history = load_visualization_history(out)
        assert history[-1]["skill"] == "plot"
        assert 'name="weather_skills_history"' in out.read_text(encoding="utf-8")

    def test_jpeg_stamp(self, tmp_path):
        out = tmp_path / "fig.jpg"

        @weather_skill("plot", "0.1.0", outputs=["visualization"])
        def plot():
            Image.new("RGB", (8, 8), color=(0, 255, 0)).save(out, quality=90)
            return out

        plot(["-o", str(out)])
        history = load_visualization_history(out)
        assert history[-1]["skill"] == "plot"


class TestNoArtifact:
    def test_ignores_return(self, data_store):
        @weather_skill("inspect", "0.1.0", inputs=["data"])
        def inspect(ds):
            return "ignored"

        assert inspect(["-i", str(data_store)]) == "ignored"


class TestMultiVariable:
    def test_multiple_required(self, tmp_path):
        out = tmp_path / "out.zarr"
        seen = {}

        @weather_skill("s", "1.0.0", outputs=["data"], variable="multiple_required")
        def skill(variable):
            seen["variable"] = variable
            return make_data()

        skill(["-o", str(out), "-v", "a", "-v", "b"])
        assert seen["variable"] == ["a", "b"]
