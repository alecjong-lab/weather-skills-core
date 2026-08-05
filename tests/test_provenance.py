"""Provenance helpers: history load/stamp and figure metadata."""

from conftest import make_data
from PIL import Image

from weather_skills_core import provenance


def write_store(path, history=None, fill=1.0):
    ds = make_data(fill=fill)
    if history is not None:
        provenance.stamp_zarr(ds, history)
    ds.to_zarr(path, mode="w", consolidated=True)
    return path


def entry(**overrides):
    base = {
        "skill": "demo",
        "version": "0.1.0",
        "args": {"x": 1},
        "input": None,
    }
    base.update(overrides)
    return base


class TestStampAndLoad:
    def test_roundtrip(self, tmp_path):
        out = write_store(tmp_path / "a.zarr", history=[entry()])
        assert provenance.load_history(out) == [entry()]

    def test_restamp(self, tmp_path):
        out = write_store(tmp_path / "a.zarr", history=[entry()])
        new = [entry(), entry(skill="next", version="0.2.0")]
        provenance.restamp_zarr(out, new)
        assert provenance.load_history(out) == new

    def test_hash_stable(self, tmp_path):
        a = write_store(tmp_path / "a.zarr")
        b = write_store(tmp_path / "b.zarr")
        assert provenance.hash_zarr(a) == provenance.hash_zarr(b)

    def test_build_entry(self):
        e = provenance.build_entry("s", "1.0.0", {"k": "v"}, None)
        assert e == {"skill": "s", "version": "1.0.0", "args": {"k": "v"}, "input": None}


class TestChainValidation:
    def test_ok(self):
        violations, _notes = provenance.validate_chain([entry()], "h")
        assert violations == []

    def test_missing_skill(self):
        bad = entry()
        del bad["skill"]
        violations, _ = provenance.validate_chain([bad], "h")
        assert any("skill" in v for v in violations)

    def test_chain_is_intact(self):
        assert provenance.chain_is_intact([entry()])
        assert not provenance.chain_is_intact([])
        assert not provenance.chain_is_intact(None)
        bad = entry()
        del bad["skill"]
        assert not provenance.chain_is_intact([bad])


class TestVisualization:
    def test_png(self, tmp_path):
        path = tmp_path / "x.png"
        Image.new("RGB", (4, 4)).save(path)
        chain = [entry()]
        provenance.stamp_figure(path, chain)
        assert provenance.load_figure_history(path) == chain

    def test_html(self, tmp_path):
        path = tmp_path / "x.html"
        path.write_text("<html><head></head><body></body></html>", encoding="utf-8")
        chain = [entry()]
        provenance.stamp_figure(path, chain)
        assert provenance.load_figure_history(path) == chain
        assert "weather_skills_history" in path.read_text(encoding="utf-8")

    def test_jpeg(self, tmp_path):
        path = tmp_path / "x.jpg"
        Image.new("RGB", (4, 4)).save(path, quality=90)
        chain = [entry()]
        provenance.stamp_figure(path, chain)
        assert provenance.load_figure_history(path) == chain

    def test_png_official_mark_when_intact(self, tmp_path):
        path = tmp_path / "marked.png"
        Image.new("RGB", (400, 300), color=(220, 220, 220)).save(path)
        before = list(Image.open(path).crop((320, 250, 400, 300)).get_flattened_data())
        chain = [entry()]
        provenance.stamp_figure(path, chain)
        assert provenance.load_figure_history(path) == chain
        after = list(Image.open(path).crop((320, 250, 400, 300)).get_flattened_data())
        # Bottom-right corner should differ from the unmarked baseline.
        assert before != after

    def test_png_no_mark_when_empty_or_invalid(self, tmp_path):
        for name, history in (
            ("empty.png", []),
            ("invalid.png", [{"skill": "", "version": "0", "args": {}, "input": None}]),
        ):
            path = tmp_path / name
            Image.new("RGB", (400, 300), color=(220, 220, 220)).save(path)
            before = list(Image.open(path).crop((320, 250, 400, 300)).get_flattened_data())
            provenance.stamp_figure(path, history)
            after = list(Image.open(path).crop((320, 250, 400, 300)).get_flattened_data())
            assert before == after

    def test_jpeg_official_mark_when_intact(self, tmp_path):
        path = tmp_path / "marked.jpg"
        Image.new("RGB", (400, 300), color=(220, 220, 220)).save(path, quality=95)
        before = list(Image.open(path).crop((320, 250, 400, 300)).get_flattened_data())
        chain = [entry()]
        provenance.stamp_figure(path, chain)
        assert provenance.load_figure_history(path) == chain
        after = list(Image.open(path).crop((320, 250, 400, 300)).get_flattened_data())
        assert before != after
