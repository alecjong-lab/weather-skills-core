"""Hashing, history load, and cache-hit matching used by @weather_skill."""

import json

import pytest
from conftest import make_gridded

from weather_skills_core import provenance


def write_store(path, history=None, *, fill=1.0):
    ds = make_gridded(fill=fill)
    if history is not None:
        ds.attrs["weather_skills_history"] = json.dumps(history, sort_keys=True)
    ds.to_zarr(path, mode="w", consolidated=True)
    return path


def entry(**overrides):
    base = {
        "skill": "clip-region",
        "version": "0.1.0",
        "args": {"bbox": "1/2/3/4"},
        "input": {"basename": "in.zarr", "hash": "abc"},
    }
    base.update(overrides)
    return base


def test_hash_zarr(tmp_path):
    a = write_store(tmp_path / "a.zarr", fill=1.0)
    b = write_store(tmp_path / "b.zarr", fill=1.0)
    assert provenance.hash_zarr(a) == provenance.hash_zarr(b)
    write_store(tmp_path / "a.zarr", fill=2.0)
    assert provenance.hash_zarr(a) != provenance.hash_zarr(b)


def test_load_history(tmp_path):
    assert provenance.load_history(tmp_path / "missing.zarr") == []
    bare = write_store(tmp_path / "bare.zarr")
    assert provenance.load_history(bare) == []
    chain = [entry(input=None)]
    stamped = write_store(tmp_path / "stamped.zarr", chain)
    assert provenance.load_history(stamped) == chain


def test_cache_hit_fetcher(tmp_path):
    e = entry(input=None)
    out = write_store(tmp_path / "out.zarr", [e])
    assert provenance.cache_hit(out, e, fetcher=True)
    assert not provenance.cache_hit(out, entry(input=None, version="0.2.0"), fetcher=True)
    assert not provenance.cache_hit(tmp_path / "gone.zarr", e, fetcher=True)


def test_cache_hit_chained(tmp_path):
    upstream = [entry(skill="chirps-fetch", input=None)]
    e = entry()
    out = write_store(tmp_path / "out.zarr", upstream + [e])
    assert provenance.cache_hit(out, e, upstream)
    assert not provenance.cache_hit(
        out, entry(input={"basename": "in.zarr", "hash": "different"}), upstream
    )
    other = [entry(skill="imerg-fetch", input=None)]
    assert not provenance.cache_hit(out, e, other)


def test_stamp_zarr_sets_history():
    ds = make_gridded()
    chain = [entry(input=None)]
    provenance.stamp_zarr(ds, chain, source="test")
    assert json.loads(ds.attrs["weather_skills_history"]) == chain
    assert ds.attrs["weather_skills_source"] == "test"


def test_read_and_validate_chain():
    assert provenance.read_chain("[]") == []
    assert provenance.read_chain("{}", label="x") is None
    assert provenance.read_chain("[]", strict=True) == []
    with pytest.raises(ValueError, match="not a JSON array"):
        provenance.read_chain("{}", strict=True)

    good = [entry(input=None)]
    violations, notes = provenance.validate_chain(good, "history")
    assert violations == []
    assert notes == []

    bad = [{"skill": "x"}]
    violations, _ = provenance.validate_chain(bad, "history")
    assert any("version" in v or "args" in v or "input" in v for v in violations)


def test_cache_hit_multi_input(tmp_path):
    upstream_a = [entry(skill="a-fetch", input=None)]
    upstream_b = [entry(skill="b-fetch", input=None)]
    multi = [
        {"basename": "a.zarr", "hash": "ha", "history": upstream_a},
        {"basename": "b.zarr", "hash": "hb", "history": upstream_b},
    ]
    e = entry(input=multi)
    out = write_store(tmp_path / "out.zarr", [e])
    assert provenance.cache_hit(out, e, [])
    changed = entry(
        input=[
            {"basename": "a.zarr", "hash": "ha", "history": upstream_a},
            {"basename": "b.zarr", "hash": "CHANGED", "history": upstream_b},
        ]
    )
    assert not provenance.cache_hit(out, changed, [])
