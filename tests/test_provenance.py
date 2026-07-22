import json

import pytest
import xarray as xr
from conftest import make_gridded

from weather_skills_core import provenance


def write_store(path, history=None, *, raw=None, fill=1.0):
    """Write a tiny gridded store, optionally stamped with a history chain."""
    ds = make_gridded(fill=fill)
    if raw is not None:
        ds.attrs["weather_skills_history"] = raw
    elif history is not None:
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

    def test_rhiza_history_attr_is_not_read(self, tmp_path):
        # A store carrying only the old rhiza_history attr has no history.
        ds = make_gridded()
        ds.attrs["rhiza_history"] = json.dumps([entry()], sort_keys=True)
        path = tmp_path / "a.zarr"
        ds.to_zarr(path, mode="w", consolidated=True)
        assert provenance.load_history(path) == []

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


class TestParseChain:
    def test_valid_array(self):
        chain = [entry()]
        assert provenance.parse_chain(json.dumps(chain)) == chain

    def test_empty_array(self):
        assert provenance.parse_chain("[]") == []

    def test_non_json_raises(self):
        with pytest.raises(ValueError, match="^value is not valid JSON$"):
            provenance.parse_chain("not json at all")

    def test_none_raises_not_valid_json(self):
        with pytest.raises(ValueError, match="^value is not valid JSON$"):
            provenance.parse_chain(None)

    def test_json_object_raises_not_array(self):
        with pytest.raises(ValueError, match="^value is not a JSON array$"):
            provenance.parse_chain(json.dumps({"skill": "x"}))

    def test_json_scalar_raises_not_array(self):
        with pytest.raises(ValueError, match="^value is not a JSON array$"):
            provenance.parse_chain("42")


class TestCoerceChain:
    def test_valid_array_passes_through(self, capsys):
        chain = [entry()]
        assert provenance.coerce_chain(json.dumps(chain), "a.zarr") == chain
        assert capsys.readouterr().err == ""

    def test_empty_array_passes_through(self, capsys):
        assert provenance.coerce_chain("[]", "a.zarr") == []
        assert capsys.readouterr().err == ""

    def test_imperfect_entries_pass_through(self):
        # Coercion is array-level only: entries missing keys are not touched.
        chain = [{"unexpected": True}]
        assert provenance.coerce_chain(json.dumps(chain), "a.zarr") == chain

    def test_non_json_warns_and_returns_none(self, capsys):
        label = "plot.png (weather_skills_history_a)"
        assert provenance.coerce_chain("not json at all", label) is None
        assert capsys.readouterr().err == (
            "ignoring malformed weather_skills_history on plot.png "
            "(weather_skills_history_a); run `provenance --check` for details\n"
        )

    def test_json_object_warns_and_returns_none(self, capsys):
        assert provenance.coerce_chain(json.dumps({"skill": "x"}), "a.zarr") is None
        err = capsys.readouterr().err
        assert err == (
            "ignoring malformed weather_skills_history on a.zarr; "
            "run `provenance --check` for details\n"
        )

    def test_json_scalar_warns_and_returns_none(self, capsys):
        assert provenance.coerce_chain("42", "a.zarr") is None
        assert "provenance --check" in capsys.readouterr().err


class TestValidateChain:
    def test_valid_chain(self):
        violations, notes = provenance.validate_chain([entry(input=None), entry()], "h")
        assert violations == []
        assert notes == []

    def test_non_list_chain(self):
        violations, notes = provenance.validate_chain({"skill": "x"}, "h")
        assert violations == ["h: value is not a JSON array"]
        assert notes == []

    def test_non_dict_entry(self):
        violations, _ = provenance.validate_chain(["nope"], "h")
        assert violations == ["h[0]: entry is not an object"]

    def test_missing_required_keys(self):
        violations, _ = provenance.validate_chain([{}], "h")
        assert violations == [
            "h[0]: missing required key 'skill'",
            "h[0]: missing required key 'version'",
            "h[0]: missing required key 'args'",
            "h[0]: missing required key 'input'",
        ]

    def test_mistyped_fields(self):
        bad = {"skill": 1, "version": 2, "args": [], "input": 3}
        violations, _ = provenance.validate_chain([bad], "h")
        assert violations == [
            "h[0].skill: must be a string",
            "h[0].version: must be a string",
            "h[0].args: must be an object",
            "h[0].input: must be null, an object, or an array of objects",
        ]

    def test_empty_skill_string(self):
        violations, _ = provenance.validate_chain([entry(skill="")], "h")
        assert violations == ["h[0].skill: must be a non-empty string"]

    def test_unknown_entry_key_is_note(self):
        violations, notes = provenance.validate_chain([dict(entry(), extra=1)], "h")
        assert violations == []
        assert notes == ["h[0]: unknown key 'extra'"]

    def test_violation_location_uses_entry_index(self):
        violations, _ = provenance.validate_chain([entry(), entry(version=1)], "h")
        assert violations == ["h[1].version: must be a string"]

    def test_input_dict_missing_keys(self):
        violations, _ = provenance.validate_chain([entry(input={})], "h")
        assert violations == [
            "h[0].input: missing required key 'basename'",
            "h[0].input: missing required key 'hash'",
        ]

    def test_input_dict_mistyped_values(self):
        violations, _ = provenance.validate_chain([entry(input={"basename": 1, "hash": 2})], "h")
        assert violations == [
            "h[0].input.basename: must be a string",
            "h[0].input.hash: must be a string",
        ]

    def test_input_list_items_located(self):
        e = entry(input=[{"basename": "a.zarr", "hash": "x"}, "bad"])
        violations, _ = provenance.validate_chain([e], "h")
        assert violations == ["h[0].input[1]: input entry is not an object"]

    def test_input_item_unknown_key_is_note(self):
        e = entry(input=[{"basename": "a.zarr", "hash": "x", "note": 1}])
        violations, notes = provenance.validate_chain([e], "h")
        assert violations == []
        assert notes == ["h[0].input[0]: unknown key 'note'"]

    def test_multi_input_with_histories_is_valid(self):
        e = entry(
            input=[
                {"basename": "a.zarr", "hash": "ha", "history": []},
                {"basename": "b.zarr", "hash": "hb", "history": [entry(input=None)]},
            ]
        )
        violations, notes = provenance.validate_chain([e], "h")
        assert violations == []
        assert notes == []

    def test_nested_history_recursion(self):
        nested = [{"skill": "", "version": "0.1.0", "args": {}, "input": None}]
        e = entry(input=[{"basename": "a.zarr", "hash": "x", "history": nested}])
        violations, _ = provenance.validate_chain([e], "h")
        assert violations == ["h[0].input[0].history[0].skill: must be a non-empty string"]

    def test_nested_history_non_array(self):
        e = entry(input=[{"basename": "a.zarr", "hash": "x", "history": "bad"}])
        violations, _ = provenance.validate_chain([e], "h")
        assert violations == ["h[0].input[0].history: value is not a JSON array"]


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

    def test_stamp_leaves_unrelated_attrs_untouched(self):
        # rhiza_* attrs are ordinary opaque attrs: no migration, no removal.
        ds = make_gridded()
        ds.attrs["rhiza_source"] = "chirps"
        provenance.stamp_zarr(ds, [])
        assert ds.attrs["rhiza_source"] == "chirps"
        assert "weather_skills_source" not in ds.attrs


class TestRestampZarr:
    def test_history_rewritten_for_both_readers(self, tmp_path):
        store = write_store(tmp_path / "a.zarr", [entry(args={"end": "2026-01-31"})])
        new_chain = [entry(args={"end": "2026-01-02"})]
        provenance.restamp_zarr(store, new_chain)
        assert provenance.load_history(store) == new_chain
        consolidated = xr.open_zarr(store, consolidated=True)
        assert json.loads(consolidated.attrs["weather_skills_history"]) == new_chain

    def test_data_and_other_attrs_untouched(self, tmp_path):
        ds = make_gridded(fill=3.0)
        ds.attrs["weather_skills_source"] = "toy"
        path = tmp_path / "a.zarr"
        ds.to_zarr(path, mode="w", consolidated=True)
        provenance.restamp_zarr(path, [entry()])
        after = xr.open_zarr(path, consolidated=True)
        assert after.attrs["weather_skills_source"] == "toy"
        assert float(after["precip"].values.max()) == 3.0


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

    def test_completeness_probe_rejects_hit(self, tmp_path, capsys):
        e = entry()
        out = write_store(tmp_path / "out.zarr", self.upstream() + [e])
        assert not provenance.cache_hit(
            out, e, self.upstream(), completeness_probe=lambda p: False
        )
        err = capsys.readouterr().err
        assert "incomplete" in err
        assert "recomputing" in err

    def test_completeness_probe_accepts_hit(self, tmp_path):
        e = entry()
        out = write_store(tmp_path / "out.zarr", self.upstream() + [e])
        probed = []
        assert provenance.cache_hit(
            out, e, self.upstream(), completeness_probe=lambda p: probed.append(p) or True
        )
        assert probed == [out]

    def test_probe_not_called_when_entry_mismatches(self, tmp_path):
        out = write_store(tmp_path / "out.zarr", self.upstream() + [entry()])
        probed = []
        provenance.cache_hit(
            out,
            entry(version="9.9.9"),
            self.upstream(),
            completeness_probe=lambda p: probed.append(p) or True,
        )
        assert probed == []
