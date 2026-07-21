import json

import pytest
import xarray as xr
from conftest import make_gridded

from weather_skills_core import provenance


def write_store(path, history=None, *, legacy=False, raw=None, fill=1.0):
    """Write a tiny gridded store, optionally stamped with a history chain."""
    ds = make_gridded(fill=fill)
    if raw is not None:
        ds.attrs["weather_skills_history"] = raw
    elif history is not None:
        key = "rhiza_history" if legacy else "weather_skills_history"
        ds.attrs[key] = json.dumps(history, sort_keys=True)
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


class TestHashZarr:
    def test_deterministic(self, tmp_path):
        store = write_store(tmp_path / "a.zarr")
        assert provenance.hash_zarr(store) == provenance.hash_zarr(store)

    def test_content_change_changes_hash(self, tmp_path):
        a = write_store(tmp_path / "a.zarr", fill=1.0)
        before = provenance.hash_zarr(a)
        write_store(tmp_path / "a.zarr", fill=2.0)
        assert provenance.hash_zarr(a) != before

    def test_identical_content_same_hash(self, tmp_path):
        a = write_store(tmp_path / "a.zarr")
        b = write_store(tmp_path / "b.zarr")
        assert provenance.hash_zarr(a) == provenance.hash_zarr(b)


class TestLoadHistory:
    def test_missing_store_is_empty(self, tmp_path):
        assert provenance.load_history(tmp_path / "nope.zarr") == []

    def test_store_without_history_is_empty(self, tmp_path):
        assert provenance.load_history(write_store(tmp_path / "a.zarr")) == []

    def test_valid_history(self, tmp_path):
        chain = [entry()]
        store = write_store(tmp_path / "a.zarr", chain)
        assert provenance.load_history(store) == chain

    def test_legacy_rhiza_history_fallback(self, tmp_path):
        chain = [entry()]
        store = write_store(tmp_path / "a.zarr", chain, legacy=True)
        assert provenance.load_history(store) == chain

    def test_json_object_is_malformed(self, tmp_path, capsys):
        store = write_store(tmp_path / "a.zarr", raw=json.dumps({"skill": "x"}))
        assert provenance.load_history(store) == []
        assert "provenance --check" in capsys.readouterr().err

    def test_non_json_is_malformed(self, tmp_path, capsys):
        store = write_store(tmp_path / "a.zarr", raw="not json at all")
        assert provenance.load_history(store) == []
        assert "provenance --check" in capsys.readouterr().err

    def test_imperfect_entries_pass_through(self, tmp_path):
        # Coercion is array-level only: entries missing keys are not touched.
        chain = [{"unexpected": True}]
        store = write_store(tmp_path / "a.zarr", chain)
        assert provenance.load_history(store) == chain


class TestEntryConstruction:
    def test_input_ref_with_hash(self, tmp_path):
        store = write_store(tmp_path / "a.zarr")
        ref = provenance.input_ref(store)
        assert ref["basename"] == "a.zarr"
        assert ref["hash"] == provenance.hash_zarr(store)

    def test_input_ref_without_hash(self, tmp_path):
        ref = provenance.input_ref(write_store(tmp_path / "a.zarr"), include_hash=False)
        assert ref == {"basename": "a.zarr"}

    def test_multi_input_ref(self, tmp_path):
        a = write_store(tmp_path / "a.zarr")
        b = write_store(tmp_path / "b.zarr", [entry()])
        refs = provenance.multi_input_ref([a, b], [[], [entry()]])
        assert [r["basename"] for r in refs] == ["a.zarr", "b.zarr"]
        assert refs[0]["history"] == []
        assert refs[1]["history"] == [entry()]
        assert all("hash" in r for r in refs)

    def test_build_entry_fetcher(self):
        e = provenance.build_entry("chirps-fetch", "0.1.0", {"start": "2026-01-01"}, None)
        assert e["input"] is None
        assert "reference_inputs" not in e

    def test_build_entry_reference_inputs_sibling(self, tmp_path):
        refs = provenance.reference_ref([write_store(tmp_path / "grid.zarr")])
        e = provenance.build_entry("downscale", "0.1.0", {}, {"basename": "a.zarr"}, refs)
        assert e["reference_inputs"][0]["basename"] == "grid.zarr"
        assert "hash" in e["reference_inputs"][0]


class TestLegacyMigration:
    def test_legacy_attrs_migrate(self):
        attrs = {"rhiza_history": "[]", "rhiza_source": "chirps", "rhiza_forecast_init": "x"}
        provenance.migrate_legacy_attrs(attrs)
        assert attrs == {
            "weather_skills_history": "[]",
            "weather_skills_source": "chirps",
            "weather_skills_forecast_init": "x",
        }

    def test_existing_new_name_wins(self):
        attrs = {"rhiza_history": "old", "weather_skills_history": "new"}
        provenance.migrate_legacy_attrs(attrs)
        assert attrs == {"weather_skills_history": "new"}


class TestStampZarr:
    def test_stamp_sets_sorted_json_and_clears_encoding(self, tmp_path):
        store = write_store(tmp_path / "a.zarr")
        ds = xr.open_zarr(store, consolidated=False)
        ds["precip"].encoding["chunks"] = (1, 1, 1)
        chain = [entry()]
        provenance.stamp_zarr(ds, chain)
        assert ds.attrs["weather_skills_history"] == json.dumps(chain, sort_keys=True)
        assert all(ds[v].encoding == {} for v in ds.variables)

    def test_stamp_sets_source(self):
        ds = make_gridded()
        provenance.stamp_zarr(ds, [], source="oisst")
        assert ds.attrs["weather_skills_source"] == "oisst"

    def test_stamp_migrates_legacy(self):
        ds = make_gridded()
        ds.attrs["rhiza_source"] = "chirps"
        provenance.stamp_zarr(ds, [])
        assert "rhiza_source" not in ds.attrs
        assert ds.attrs["weather_skills_source"] == "chirps"


class TestPngMetadata:
    def test_single_unlabeled(self):
        chain = [entry()]
        md = provenance.png_metadata([(None, chain)])
        assert md["weather_skills_history"] == json.dumps(chain, sort_keys=True)
        assert md["Software"] == "forecasting-skills"

    def test_suffixed_labels(self):
        md = provenance.png_metadata([("a", []), ("b", [entry()])])
        assert set(md) == {"weather_skills_history_a", "weather_skills_history_b", "Software"}

    def test_semantic_labels(self):
        md = provenance.png_metadata([("forecast", []), ("mclimate", [])])
        assert "weather_skills_history_forecast" in md
        assert "weather_skills_history_mclimate" in md

    def test_custom_software(self):
        assert provenance.png_metadata([(None, [])], software="acme")["Software"] == "acme"


class TestCacheHitFetcher:
    def test_hit_on_matching_first_entry(self, tmp_path):
        e = entry(input=None)
        out = write_store(tmp_path / "out.zarr", [e])
        assert provenance.cache_hit(out, e, fetcher=True)

    def test_missing_store_is_miss(self, tmp_path):
        assert not provenance.cache_hit(tmp_path / "out.zarr", entry(input=None), fetcher=True)

    def test_no_history_is_miss(self, tmp_path):
        out = write_store(tmp_path / "out.zarr")
        assert not provenance.cache_hit(out, entry(input=None), fetcher=True)

    @pytest.mark.parametrize(
        "change",
        [{"version": "0.2.0"}, {"args": {"bbox": "9/9/9/9"}}, {"skill": "other"}],
    )
    def test_changed_field_is_miss(self, tmp_path, change):
        out = write_store(tmp_path / "out.zarr", [entry(input=None)])
        assert not provenance.cache_hit(out, entry(input=None, **change), fetcher=True)

    def test_first_entry_position(self, tmp_path):
        # A fetcher hit keys on history[0] even when later entries exist.
        e = entry(input=None)
        out = write_store(tmp_path / "out.zarr", [e, entry(skill="clip-region")])
        assert provenance.cache_hit(out, e, fetcher=True)

    def test_completeness_probe_rejects_hit(self, tmp_path, capsys):
        e = entry(input=None)
        out = write_store(tmp_path / "out.zarr", [e])
        assert not provenance.cache_hit(out, e, fetcher=True, completeness_probe=lambda p: False)
        assert "incomplete" in capsys.readouterr().err

    def test_completeness_probe_accepts_hit(self, tmp_path):
        e = entry(input=None)
        out = write_store(tmp_path / "out.zarr", [e])
        probed = []
        assert provenance.cache_hit(
            out, e, fetcher=True, completeness_probe=lambda p: probed.append(p) or True
        )
        assert probed == [out]

    def test_probe_not_called_when_entry_mismatches(self, tmp_path):
        out = write_store(tmp_path / "out.zarr", [entry(input=None)])
        probed = []
        provenance.cache_hit(
            out,
            entry(input=None, version="9.9.9"),
            fetcher=True,
            completeness_probe=lambda p: probed.append(p) or True,
        )
        assert probed == []


class TestCacheHitChained:
    def upstream(self):
        return [entry(skill="chirps-fetch", input=None)]

    def test_hit(self, tmp_path):
        e = entry()
        out = write_store(tmp_path / "out.zarr", self.upstream() + [e])
        assert provenance.cache_hit(out, e, self.upstream())

    def test_upstream_mismatch_is_miss(self, tmp_path):
        e = entry()
        out = write_store(tmp_path / "out.zarr", self.upstream() + [e])
        other = [entry(skill="imerg-fetch", input=None)]
        assert not provenance.cache_hit(out, e, other)

    def test_chain_length_mismatch_is_miss(self, tmp_path):
        e = entry()
        out = write_store(tmp_path / "out.zarr", [e])
        assert not provenance.cache_hit(out, e, self.upstream())

    def test_hash_change_is_miss(self, tmp_path):
        e = entry()
        out = write_store(tmp_path / "out.zarr", self.upstream() + [e])
        changed = entry(input={"basename": "in.zarr", "hash": "different"})
        assert not provenance.cache_hit(out, changed, self.upstream())

    def test_hash_ignored_when_compare_disabled(self, tmp_path):
        e = entry()
        out = write_store(tmp_path / "out.zarr", self.upstream() + [e])
        changed = entry(input={"basename": "in.zarr"})
        assert provenance.cache_hit(out, changed, self.upstream(), compare_hash=False)

    def test_basename_change_is_miss_even_without_hash(self, tmp_path):
        e = entry()
        out = write_store(tmp_path / "out.zarr", self.upstream() + [e])
        changed = entry(input={"basename": "renamed.zarr"})
        assert not provenance.cache_hit(out, changed, self.upstream(), compare_hash=False)

    def test_multi_input_hit(self, tmp_path):
        inputs = [
            {"basename": "a.zarr", "hash": "ha", "history": []},
            {"basename": "b.zarr", "hash": "hb", "history": self.upstream()},
        ]
        e = entry(input=inputs)
        out = write_store(tmp_path / "out.zarr", [e])
        assert provenance.cache_hit(out, e, [])

    def test_multi_input_hash_change_is_miss(self, tmp_path):
        inputs = [{"basename": "a.zarr", "hash": "ha", "history": []}]
        out = write_store(tmp_path / "out.zarr", [entry(input=inputs)])
        changed = entry(input=[{"basename": "a.zarr", "hash": "other", "history": []}])
        assert not provenance.cache_hit(out, changed, [])

    def test_multi_input_branch_history_change_is_miss(self, tmp_path):
        inputs = [{"basename": "a.zarr", "hash": "ha", "history": []}]
        out = write_store(tmp_path / "out.zarr", [entry(input=inputs)])
        changed = entry(input=[{"basename": "a.zarr", "hash": "ha", "history": self.upstream()}])
        assert not provenance.cache_hit(out, changed, [])

    def test_multi_input_count_change_is_miss(self, tmp_path):
        inputs = [{"basename": "a.zarr", "hash": "ha", "history": []}]
        out = write_store(tmp_path / "out.zarr", [entry(input=inputs)])
        changed = entry(input=inputs + [{"basename": "b.zarr", "hash": "hb", "history": []}])
        assert not provenance.cache_hit(out, changed, [])

    def test_reference_inputs_change_is_miss(self, tmp_path):
        refs = [{"basename": "grid.zarr", "hash": "g1"}]
        e = dict(entry(), reference_inputs=refs)
        out = write_store(tmp_path / "out.zarr", [e])
        changed = dict(entry(), reference_inputs=[{"basename": "grid.zarr", "hash": "g2"}])
        assert provenance.cache_hit(out, e, [])
        assert not provenance.cache_hit(out, changed, [])

    def test_reference_inputs_absent_on_both_is_hit(self, tmp_path):
        e = entry()
        out = write_store(tmp_path / "out.zarr", [e])
        assert provenance.cache_hit(out, e, [])
