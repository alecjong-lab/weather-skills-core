import json
from datetime import date
from pathlib import Path

import numpy as np
import pytest
import xarray as xr
from conftest import make_gridded

from weather_skills_core import DataError, EntryOverride, UsageError, weather_skill
from weather_skills_core.decorator import rewrite_bbox_argv


class FakeFigure:
    """Stand-in for a matplotlib Figure; captures the savefig call."""

    def __init__(self):
        self.saved = None

    def savefig(self, path, metadata=None, **kwargs):
        self.saved = {"path": path, "metadata": metadata, "kwargs": kwargs}
        Path(path).write_bytes(b"\x89PNG fake")


def history_of(store):
    ds = xr.open_zarr(store, consolidated=True)
    return json.loads(ds.attrs["weather_skills_history"])


def make_identity_skill(calls, **declaration):
    declaration.setdefault("input_type", "gridded")
    declaration.setdefault("output_type", "gridded")

    @weather_skill("identity", "0.1.0", **declaration)
    def identity(ds, **params):
        """Copy the input envelope unchanged."""
        calls.append(params)
        return ds.copy()

    return identity


class TestParserConstruction:
    def test_help_contains_version_epilog_and_description(self, capsys):
        skill = make_identity_skill([])
        with pytest.raises(SystemExit) as exc:
            skill(["--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "skill version: 0.1.0" in out
        assert "Copy the input envelope unchanged." in out

    def test_standard_flags(self):
        skill = make_identity_skill(
            [], variable="single", title=True, dims=True, time_dim="time", bbox="optional"
        )
        args = skill.parser.parse_args(
            ["-i", "a", "-o", "b", "-v", "precip", "--title", "T", "--dims", "y,x"]
        )
        assert args.variable == "precip"
        assert args.title == "T"
        assert args.dims == "y,x"
        assert args.time_dim == "time"  # declared default
        assert args.bbox is None

    def test_repeatable_variable(self):
        skill = make_identity_skill([], variable="repeat")
        args = skill.parser.parse_args(["-i", "a", "-o", "b", "-v", "x", "-v", "y"])
        assert args.variable == ["x", "y"]

    def test_extra_args_bare_type(self):
        skill = make_identity_skill([], extra_args={"factor": int})
        args = skill.parser.parse_args(["-i", "a", "-o", "b", "--factor", "3"])
        assert args.factor == 3

    def test_extra_args_constraint_set_choices(self):
        skill = make_identity_skill([], extra_args={"interpolation_factor": {int, range(0, 2)}})
        args = skill.parser.parse_args(["-i", "a", "-o", "b", "--interpolation-factor", "1"])
        assert args.interpolation_factor == 1
        with pytest.raises(SystemExit) as exc:
            skill.parser.parse_args(["-i", "a", "-o", "b", "--interpolation-factor", "5"])
        assert exc.value.code == 2

    def test_extra_args_bool_flag(self):
        skill = make_identity_skill([], extra_args={"align_day_of_year": bool})
        assert skill.parser.parse_args(["-i", "a", "-o", "b"]).align_day_of_year is False
        args = skill.parser.parse_args(["-i", "a", "-o", "b", "--align-day-of-year"])
        assert args.align_day_of_year is True

    def test_extra_args_dict_spec(self):
        skill = make_identity_skill(
            [],
            extra_args={
                "method": {"choices": ["mean", "std"], "required": True},
                "dim": {"repeat": True},
            },
        )
        args = skill.parser.parse_args(
            ["-i", "a", "-o", "b", "--method", "std", "--dim", "x", "--dim", "y"]
        )
        assert args.method == "std"
        assert args.dim == ["x", "y"]

    def test_extra_args_positional(self):
        @weather_skill(
            "resolve-region", "0.1.0", extra_args={"code": {"positional": True, "metavar": "CODE"}}
        )
        def resolve_region(code):
            """Resolve a country code."""

        args = resolve_region.parser.parse_args(["KEN"])
        assert args.code == "KEN"

    def test_missing_required_input_exits_2(self):
        skill = make_identity_skill([])
        with pytest.raises(SystemExit) as exc:
            skill(["-o", "b"])
        assert exc.value.code == 2

    def test_declaration_errors(self):
        with pytest.raises(ValueError, match="output_type"):
            weather_skill("x", "0.1.0", output_type="zar")(lambda: None)
        with pytest.raises(ValueError, match="together"):
            weather_skill("x", "0.1.0", output_type="gridded", start_time=True)(lambda: None)
        with pytest.raises(ValueError, match="history label"):
            weather_skill("x", "0.1.0", input_type=["any", "any"], output_type="png")(
                lambda a, b: None
            )


class TestMutexGroups:
    def make_select_skill(self, calls, required=True):
        return make_identity_skill(
            calls,
            extra_args={"index": int, "value": str},
            mutex_groups={"selector": {"args": ("index", "value"), "required": required}},
        )

    def test_one_member_parses_and_reaches_function(self, tmp_path, gridded_store):
        calls = []
        skill = self.make_select_skill(calls)
        skill(["-i", str(gridded_store), "-o", str(tmp_path / "o.zarr"), "--index", "3"])
        assert calls == [{"index": 3, "value": None}]

    def test_two_members_exit_2(self, tmp_path, gridded_store, capsys):
        skill = self.make_select_skill([])
        with pytest.raises(SystemExit) as exc:
            skill(
                [
                    "-i",
                    str(gridded_store),
                    "-o",
                    str(tmp_path / "o.zarr"),
                    "--index",
                    "3",
                    "--value",
                    "x",
                ]
            )
        assert exc.value.code == 2
        assert "not allowed with" in capsys.readouterr().err

    def test_required_group_with_no_member_exits_2(self, tmp_path, gridded_store, capsys):
        skill = self.make_select_skill([])
        with pytest.raises(SystemExit) as exc:
            skill(["-i", str(gridded_store), "-o", str(tmp_path / "o.zarr")])
        assert exc.value.code == 2
        assert "is required" in capsys.readouterr().err

    def test_optional_group_allows_no_member(self, tmp_path, gridded_store):
        calls = []
        skill = self.make_select_skill(calls, required=False)
        skill(["-i", str(gridded_store), "-o", str(tmp_path / "o.zarr")])
        assert calls == [{"index": None, "value": None}]

    def test_sequence_shorthand_is_optional_group(self, tmp_path, gridded_store):
        skill = make_identity_skill(
            [],
            extra_args={"index": int, "value": str},
            mutex_groups={"selector": ("index", "value")},
        )
        with pytest.raises(SystemExit) as exc:
            skill.parser.parse_args(["-i", "a", "-o", "b", "--index", "1", "--value", "x"])
        assert exc.value.code == 2
        args = skill.parser.parse_args(["-i", "a", "-o", "b"])
        assert args.index is None

    def test_usage_renders_group_brackets(self):
        skill = self.make_select_skill([])
        assert "(--index INDEX | --value VALUE)" in skill.parser.format_usage()

    def test_aliases_work_inside_group(self, tmp_path, gridded_store):
        calls = []
        skill = make_identity_skill(
            calls,
            extra_args={
                "factor": {"type": float, "aliases": ["-f"]},
                "target_resolution": {"type": float},
                "reference_grid": {},
            },
            mutex_groups={
                "target": {
                    "args": ("factor", "target_resolution", "reference_grid"),
                    "required": True,
                }
            },
        )
        skill(["-i", str(gridded_store), "-o", str(tmp_path / "o.zarr"), "-f", "2.0"])
        assert calls[0]["factor"] == 2.0
        with pytest.raises(SystemExit):
            skill.parser.parse_args(["-i", "a", "-o", "b", "-f", "2", "--reference-grid", "g"])

    def test_declaration_errors(self):
        def declare(**kwargs):
            return weather_skill("x", "0.1.0", **kwargs)(lambda: None)

        with pytest.raises(ValueError, match="not an extra_args dest"):
            declare(extra_args={"a": int}, mutex_groups={"g": ("a", "b")})
        with pytest.raises(ValueError, match="at least two"):
            declare(extra_args={"a": int}, mutex_groups={"g": ("a",)})
        with pytest.raises(ValueError, match="both mutex groups"):
            declare(
                extra_args={"a": int, "b": int, "c": int},
                mutex_groups={"g1": ("a", "b"), "g2": ("a", "c")},
            )
        with pytest.raises(ValueError, match="positional"):
            declare(
                extra_args={"a": {"positional": True}, "b": int},
                mutex_groups={"g": ("a", "b")},
            )
        with pytest.raises(ValueError, match="on the group"):
            declare(
                extra_args={"a": {"required": True}, "b": int},
                mutex_groups={"g": ("a", "b")},
            )
        with pytest.raises(ValueError, match="unknown keys"):
            declare(
                extra_args={"a": int, "b": int},
                mutex_groups={"g": {"args": ("a", "b"), "exclusive": True}},
            )
        with pytest.raises(ValueError, match="under 'args'"):
            declare(extra_args={"a": int, "b": int}, mutex_groups={"g": {"required": True}})


class TestBboxArgv:
    def test_rewrite(self):
        assert rewrite_bbox_argv(["--bbox", "-1/32/-5/42", "-o", "x"]) == [
            "--bbox=-1/32/-5/42",
            "-o",
            "x",
        ]

    def test_negative_north_parses_end_to_end(self, tmp_path, gridded_store):
        seen = {}

        @weather_skill("clip", "0.1.0", input_type="any", output_type="gridded", bbox="required")
        def clip(ds, bbox):
            """Clip."""
            seen["bbox"] = bbox
            return ds.copy()

        clip(["-i", str(gridded_store), "-o", str(tmp_path / "o.zarr"), "--bbox", "-1/32/-5/42"])
        assert seen["bbox"] == (-1.0, 32.0, -5.0, 42.0)


class TestCacheShortCircuit:
    def test_second_run_skips_function_and_store(self, tmp_path, gridded_store, capsys):
        calls = []
        skill = make_identity_skill(calls)
        out = tmp_path / "out.zarr"
        skill(["-i", str(gridded_store), "-o", str(out)])
        assert len(calls) == 1
        first_history = history_of(out)
        skill(["-i", str(gridded_store), "-o", str(out)])
        assert len(calls) == 1
        assert "Cache hit" in capsys.readouterr().err
        assert history_of(out) == first_history

    def test_changed_extra_arg_reruns(self, tmp_path, gridded_store):
        calls = []
        skill = make_identity_skill(calls, extra_args={"factor": int})
        out = tmp_path / "out.zarr"
        skill(["-i", str(gridded_store), "-o", str(out), "--factor", "1"])
        skill(["-i", str(gridded_store), "-o", str(out), "--factor", "2"])
        assert len(calls) == 2

    def test_modified_input_reruns_with_hash(self, tmp_path, gridded_store):
        calls = []
        skill = make_identity_skill(calls)
        out = tmp_path / "out.zarr"
        skill(["-i", str(gridded_store), "-o", str(out)])
        make_gridded(fill=5.0).to_zarr(gridded_store, mode="w", consolidated=True)
        skill(["-i", str(gridded_store), "-o", str(out)])
        assert len(calls) == 2

    def test_modified_input_still_hits_without_hash_compare(self, tmp_path, gridded_store):
        calls = []
        skill = make_identity_skill(calls, hash_input=False)
        out = tmp_path / "out.zarr"
        skill(["-i", str(gridded_store), "-o", str(out)])
        # The stamped entry still carries a hash even though the check skips it.
        assert "hash" in history_of(out)[-1]["input"]
        make_gridded(fill=5.0).to_zarr(gridded_store, mode="w", consolidated=True)
        skill(["-i", str(gridded_store), "-o", str(out)])
        assert len(calls) == 1

    def test_workers_excluded_from_cache_key(self, tmp_path, gridded_store):
        calls = []
        skill = make_identity_skill(calls, workers=4)
        out = tmp_path / "out.zarr"
        skill(["-i", str(gridded_store), "-o", str(out), "--workers", "2"])
        skill(["-i", str(gridded_store), "-o", str(out), "--workers", "8"])
        assert len(calls) == 1
        assert "workers" not in history_of(out)[-1]["args"]

    def test_normalize_args_canonicalizes_cache_key(self, tmp_path, gridded_store):
        calls = []

        def normalize(args):
            if args.get("variable"):
                args["variable"] = sorted(set(args["variable"]))
            return args

        skill = make_identity_skill(calls, variable="repeat", normalize_args=normalize)
        out = tmp_path / "out.zarr"
        skill(["-i", str(gridded_store), "-o", str(out), "-v", "b", "-v", "a"])
        skill(["-i", str(gridded_store), "-o", str(out), "-v", "a", "-v", "b"])
        assert len(calls) == 1
        assert history_of(out)[-1]["args"]["variable"] == ["a", "b"]

    def test_exclude_args(self, tmp_path, gridded_store):
        calls = []
        skill = make_identity_skill(calls, extra_args={"verbose": bool}, exclude_args=("verbose",))
        out = tmp_path / "out.zarr"
        skill(["-i", str(gridded_store), "-o", str(out)])
        skill(["-i", str(gridded_store), "-o", str(out), "--verbose"])
        assert len(calls) == 1

    def test_chain_appends_on_upstream(self, tmp_path, gridded_store):
        first = make_identity_skill([])
        second = make_identity_skill([])
        mid = tmp_path / "mid.zarr"
        out = tmp_path / "out.zarr"
        first(["-i", str(gridded_store), "-o", str(mid)])
        second(["-i", str(mid), "-o", str(out)])
        chain = history_of(out)
        assert len(chain) == 2
        assert chain[0] == history_of(mid)[0]
        assert chain[-1]["input"]["basename"] == "mid.zarr"


class TestValidationOverrides:
    @pytest.fixture
    def renamed_store(self, tmp_path):
        path = tmp_path / "renamed.zarr"
        ds = make_gridded().rename({"latitude": "yy", "longitude": "xx"})
        ds.to_zarr(path, mode="w", consolidated=True)
        return path

    def make_clip_skill(self, calls):
        @weather_skill(
            "clip-region",
            "0.1.0",
            input_type="gridded",
            output_type="gridded",
            bbox="required",
            dims=True,
        )
        def clip_region(ds, bbox, dims):
            """Clip."""
            from weather_skills_core.envelope import bbox_subset, detect_spatial_dims

            calls.append(dims)
            lat_dim, lon_dim = detect_spatial_dims(ds, dims)
            return bbox_subset(ds, bbox, lat_dim=lat_dim, lon_dim=lon_dim)

        return clip_region

    def test_undetectable_dims_rejected_without_override(self, tmp_path, renamed_store, capsys):
        skill = self.make_clip_skill([])
        with pytest.raises(SystemExit) as exc:
            skill(["-i", str(renamed_store), "-o", str(tmp_path / "o.zarr"), "--bbox", "3/10/1/13"])
        assert exc.value.code == 2
        assert "Pass --dims" in capsys.readouterr().err

    def test_dims_override_validates_typed_gridded_input(self, tmp_path, renamed_store):
        calls = []
        skill = self.make_clip_skill(calls)
        out = tmp_path / "o.zarr"
        skill(
            [
                "-i",
                str(renamed_store),
                "-o",
                str(out),
                "--bbox",
                "2.5/10.5/0.5/12.5",
                "--dims",
                "yy,xx",
            ]
        )
        assert calls == ["yy,xx"]
        written = xr.open_zarr(out, consolidated=True)
        assert written.sizes["yy"] == 2

    def test_dims_override_naming_absent_dims_exits_2(self, tmp_path, renamed_store, capsys):
        skill = self.make_clip_skill([])
        with pytest.raises(SystemExit) as exc:
            skill(
                [
                    "-i",
                    str(renamed_store),
                    "-o",
                    str(tmp_path / "o.zarr"),
                    "--bbox",
                    "3/10/1/13",
                    "--dims",
                    "a,b",
                ]
            )
        assert exc.value.code == 2
        assert "not in dataset dims" in capsys.readouterr().err

    def test_time_dim_override_validated(self, tmp_path, gridded_store, capsys):
        calls = []
        skill = make_identity_skill(calls, time_dim=True)
        with pytest.raises(SystemExit) as exc:
            skill(["-i", str(gridded_store), "-o", str(tmp_path / "o.zarr"), "--time-dim", "t"])
        assert exc.value.code == 2
        assert "not in dataset dims" in capsys.readouterr().err
        skill(["-i", str(gridded_store), "-o", str(tmp_path / "o.zarr"), "--time-dim", "time"])
        assert calls == [{"time_dim": "time"}]


class TestOutputTypeSame:
    def test_shape_preserving_transform_end_to_end(self, tmp_path, gridded_store):
        calls = []
        skill = make_identity_skill(calls, input_type="any", output_type="same")
        out = tmp_path / "out.zarr"
        skill(["-i", str(gridded_store), "-o", str(out)])
        skill(["-i", str(gridded_store), "-o", str(out)])
        assert len(calls) == 1  # cache applies as for any zarr output
        assert history_of(out)[-1]["skill"] == "identity"

    def test_overlap_guard_applies(self, gridded_store):
        skill = make_identity_skill([], output_type="same")
        with pytest.raises(SystemExit) as exc:
            skill(["-i", str(gridded_store), "-o", str(gridded_store)])
        assert exc.value.code == 2

    def test_requires_a_declared_input(self):
        with pytest.raises(ValueError, match="declared zarr input"):
            weather_skill("x", "0.1.0", output_type="same")(lambda: None)


class TestCacheDisabled:
    def test_always_recomputes_and_stamps(self, tmp_path, gridded_store, capsys):
        calls = []
        skill = make_identity_skill(calls, cache=False)
        out = tmp_path / "out.zarr"
        skill(["-i", str(gridded_store), "-o", str(out)])
        skill(["-i", str(gridded_store), "-o", str(out)])
        assert len(calls) == 2
        assert "Cache hit" not in capsys.readouterr().err
        chain = history_of(out)
        assert chain[-1]["skill"] == "identity"
        assert "hash" in chain[-1]["input"]

    def test_fetcher_always_recomputes(self, tmp_path):
        calls = []

        @weather_skill("toy-fetch", "0.1.0", output_type="gridded", source="toy", cache=False)
        def fetch(**params):
            """Fetch a toy dataset."""
            calls.append(params)
            return make_gridded()

        out = tmp_path / "out.zarr"
        fetch(["-o", str(out)])
        fetch(["-o", str(out)])
        assert len(calls) == 2
        assert history_of(out)[0]["input"] is None

    def test_deferred_hash_still_stamped(self, tmp_path, gridded_store):
        skill = make_identity_skill([], cache=False, hash_input=False)
        out = tmp_path / "out.zarr"
        skill(["-i", str(gridded_store), "-o", str(out)])
        assert "hash" in history_of(out)[-1]["input"]

    def test_requires_zarr_output(self):
        with pytest.raises(ValueError, match="cache=False"):
            weather_skill("x", "0.1.0", input_type="any", output_type="png", cache=False)(
                lambda ds: None
            )
        with pytest.raises(ValueError, match="cache=False"):
            weather_skill("x", "0.1.0", cache=False)(lambda: None)


class TestFetcherMode:
    def make_fetcher(self, calls, **declaration):
        @weather_skill("toy-fetch", "0.1.0", output_type="gridded", source="toy", **declaration)
        def fetch(**params):
            """Fetch a toy dataset."""
            calls.append(params)
            return make_gridded()

        return fetch

    def test_entry_input_none_and_source_stamped(self, tmp_path):
        fetch = self.make_fetcher([])
        out = tmp_path / "out.zarr"
        fetch(["-o", str(out)])
        chain = history_of(out)
        assert chain == [{"skill": "toy-fetch", "version": "0.1.0", "args": {}, "input": None}]
        assert xr.open_zarr(out, consolidated=True).attrs["weather_skills_source"] == "toy"

    def test_cache_hit_on_rerun(self, tmp_path):
        calls = []
        fetch = self.make_fetcher(calls)
        out = tmp_path / "out.zarr"
        fetch(["-o", str(out)])
        fetch(["-o", str(out)])
        assert len(calls) == 1

    def test_completeness_probe_failure_refetches(self, tmp_path):
        calls = []
        fetch = self.make_fetcher(calls, completeness_probe=lambda p: False)
        out = tmp_path / "out.zarr"
        fetch(["-o", str(out)])
        fetch(["-o", str(out)])
        assert len(calls) == 2

    def test_resolved_dates_recorded_and_passed(self, tmp_path):
        calls = []
        resolver_calls = []

        def resolver(args):
            resolver_calls.append(1)
            return date(2026, 6, 30)

        fetch = self.make_fetcher(calls, start_time=True, end_time=True, latest_resolver=resolver)
        out = tmp_path / "out.zarr"
        fetch(["--start", "latest-3w", "--end", "latest", "-o", str(out)])
        assert resolver_calls == [1]
        assert calls[0]["start_time"] == date(2026, 6, 10)
        assert calls[0]["end_time"] == date(2026, 6, 30)
        args = history_of(out)[0]["args"]
        assert args["start"] == "2026-06-10"
        assert args["end"] == "2026-06-30"

    def test_resolver_not_called_for_absolute_dates(self, tmp_path):
        resolver_calls = []
        fetch = self.make_fetcher(
            [],
            start_time=True,
            end_time=True,
            latest_resolver=lambda args: resolver_calls.append(1) or date(2026, 6, 30),
        )
        fetch(["--start", "2026-01-01", "--end", "2026-01-05", "-o", str(tmp_path / "o.zarr")])
        assert resolver_calls == []

    def test_relative_resolution_logged(self, tmp_path, capsys):
        fetch = self.make_fetcher([], start_time=True, end_time=True)
        fetch(["--start", "now-1w", "--end", "now", "-o", str(tmp_path / "o.zarr")])
        err = capsys.readouterr().err
        assert '"now-1w".."now"' in err
        assert "7 days" in err

    def test_singular_date(self, tmp_path):
        calls = []

        @weather_skill("init-fetch", "0.1.0", output_type="gridded", date=True)
        def fetch(**params):
            """Fetch one init."""
            calls.append(params)
            return make_gridded()

        fetch(["--date", "2026-02-03", "-o", str(tmp_path / "o.zarr")])
        assert calls[0]["date"] == date(2026, 2, 3)
        assert history_of(tmp_path / "o.zarr")[0]["args"]["date"] == "2026-02-03"


class TestExitCodes:
    def test_bad_date_token_exits_2(self, tmp_path, capsys):
        fetch = TestFetcherMode().make_fetcher([], start_time=True, end_time=True)
        with pytest.raises(SystemExit) as exc:
            fetch(["--start", "now+3d", "--end", "now", "-o", str(tmp_path / "o.zarr")])
        assert exc.value.code == 2
        assert "Error: invalid date value" in capsys.readouterr().err

    def test_missing_input_exits_2(self, tmp_path, capsys):
        skill = make_identity_skill([])
        with pytest.raises(SystemExit) as exc:
            skill(["-i", str(tmp_path / "nope.zarr"), "-o", str(tmp_path / "o.zarr")])
        assert exc.value.code == 2
        assert "not found" in capsys.readouterr().err

    def test_overlap_guard_exits_2(self, gridded_store, capsys):
        skill = make_identity_skill([])
        with pytest.raises(SystemExit) as exc:
            skill(["-i", str(gridded_store), "-o", str(gridded_store)])
        assert exc.value.code == 2
        assert "overlaps" in capsys.readouterr().err

    def test_nested_output_exits_2(self, gridded_store):
        skill = make_identity_skill([])
        with pytest.raises(SystemExit) as exc:
            skill(["-i", str(gridded_store), "-o", str(gridded_store / "sub.zarr")])
        assert exc.value.code == 2

    def test_data_error_from_function_exits_1(self, tmp_path, gridded_store):
        @weather_skill("boom", "0.1.0", input_type="any", output_type="gridded")
        def boom(ds):
            """Fail with a data error."""
            raise DataError("no data in the requested window")

        with pytest.raises(SystemExit) as exc:
            boom(["-i", str(gridded_store), "-o", str(tmp_path / "o.zarr")])
        assert exc.value.code == 1

    def test_usage_error_from_function_exits_2(self, tmp_path, gridded_store):
        @weather_skill("boom", "0.1.0", input_type="any", output_type="gridded")
        def boom(ds):
            """Fail with a usage error."""
            raise UsageError("bad arguments")

        with pytest.raises(SystemExit) as exc:
            boom(["-i", str(gridded_store), "-o", str(tmp_path / "o.zarr")])
        assert exc.value.code == 2

    def test_envelope_mismatch_exits_2(self, tmp_path, gridded_store, capsys):
        skill = make_identity_skill([], input_type="station")
        with pytest.raises(SystemExit) as exc:
            skill(["-i", str(gridded_store), "-o", str(tmp_path / "o.zarr")])
        assert exc.value.code == 2
        assert "station_id" in capsys.readouterr().err

    def test_unreadable_zarr_exits_2(self, tmp_path):
        bad = tmp_path / "plain"
        bad.mkdir()
        (bad / "junk.txt").write_text("hello")
        skill = make_identity_skill([])
        with pytest.raises(SystemExit) as exc:
            skill(["-i", str(bad), "-o", str(tmp_path / "o.zarr")])
        assert exc.value.code == 2

    def test_validate_args_hook_runs_before_cache(self, tmp_path, gridded_store, capsys):
        def validate(args):
            if not args.to_name.strip():
                raise UsageError("--to-name must be a non-empty variable name.")

        skill = make_identity_skill([], extra_args={"to_name": str}, validate_args=validate)
        with pytest.raises(SystemExit) as exc:
            skill(["-i", str(gridded_store), "-o", str(tmp_path / "o.zarr"), "--to-name", " "])
        assert exc.value.code == 2
        assert "non-empty" in capsys.readouterr().err


class TestMultiInput:
    def test_fixed_two_inputs(self, tmp_path):
        a = tmp_path / "a.zarr"
        b = tmp_path / "b.zarr"
        make_gridded(fill=1.0).to_zarr(a, mode="w", consolidated=True)
        make_gridded(fill=2.0).to_zarr(b, mode="w", consolidated=True)
        received = []

        @weather_skill("difference", "0.1.0", input_type=["any", "any"], output_type="gridded")
        def difference(ds_a, ds_b):
            """A - B."""
            received.extend([ds_a, ds_b])
            out = ds_a.copy()
            out["precip"] = ds_a["precip"] - ds_b["precip"]
            return out

        out = tmp_path / "out.zarr"
        difference(["-i", str(a), "-i", str(b), "-o", str(out)])
        assert len(received) == 2
        chain = history_of(out)
        assert len(chain) == 1
        inputs = chain[-1]["input"]
        assert [i["basename"] for i in inputs] == ["a.zarr", "b.zarr"]
        assert all({"basename", "hash", "history"} <= set(i) for i in inputs)

    def test_wrong_input_count_exits_2(self, tmp_path, gridded_store):
        @weather_skill("difference", "0.1.0", input_type=["any", "any"], output_type="gridded")
        def difference(ds_a, ds_b):
            """A - B."""

        with pytest.raises(SystemExit) as exc:
            difference(["-i", str(gridded_store), "-o", str(tmp_path / "o.zarr")])
        assert exc.value.code == 2

    def test_trunk_is_first_inputs_chain(self, tmp_path, gridded_store):
        upstreamed = tmp_path / "up.zarr"
        make_identity_skill([])(["-i", str(gridded_store), "-o", str(upstreamed)])

        @weather_skill(
            "concat", "0.1.0", input_type="any", output_type="gridded", variadic_input=True
        )
        def concat(datasets):
            """Concatenate."""
            return datasets[0].copy()

        out = tmp_path / "out.zarr"
        concat(["-i", str(upstreamed), "-i", str(gridded_store), "-o", str(out)])
        chain = history_of(out)
        # Trunk = first input's chain + the merge entry.
        assert chain[:-1] == history_of(upstreamed)
        assert chain[-1]["skill"] == "concat"

    def test_variadic_requires_two_inputs(self, tmp_path, gridded_store):
        @weather_skill(
            "concat", "0.1.0", input_type="any", output_type="gridded", variadic_input=True
        )
        def concat(datasets):
            """Concatenate."""

        with pytest.raises(SystemExit) as exc:
            concat(["-i", str(gridded_store), "-o", str(tmp_path / "o.zarr")])
        assert exc.value.code == 2

    def test_multi_input_cache_hit_and_branch_miss(self, tmp_path):
        a = tmp_path / "a.zarr"
        b = tmp_path / "b.zarr"
        make_gridded(fill=1.0).to_zarr(a, mode="w", consolidated=True)
        make_gridded(fill=2.0).to_zarr(b, mode="w", consolidated=True)
        calls = []

        @weather_skill("difference", "0.1.0", input_type=["any", "any"], output_type="gridded")
        def difference(ds_a, ds_b):
            """A - B."""
            calls.append(1)
            return ds_a.copy()

        out = tmp_path / "out.zarr"
        argv = ["-i", str(a), "-i", str(b), "-o", str(out)]
        difference(argv)
        difference(argv)
        assert len(calls) == 1
        # Modifying one branch in place forces a recompute (per-input hash).
        make_gridded(fill=9.0).to_zarr(b, mode="w", consolidated=True)
        difference(argv)
        assert len(calls) == 2


class TestReferenceInputs:
    def test_reference_change_forces_miss(self, tmp_path, gridded_store):
        ref = tmp_path / "grid.zarr"
        make_gridded(fill=1.0).to_zarr(ref, mode="w", consolidated=True)
        calls = []
        skill = make_identity_skill(
            calls,
            input_type="any",
            hash_input=False,
            extra_args={"reference_grid": str},
            reference_args=("reference_grid",),
        )
        out = tmp_path / "out.zarr"
        argv = [
            "-i",
            str(gridded_store),
            "-o",
            str(out),
            "--reference-grid",
            str(ref),
        ]
        skill(argv)
        skill(argv)
        assert len(calls) == 1
        assert history_of(out)[-1]["reference_inputs"][0]["basename"] == "grid.zarr"
        make_gridded(fill=7.0).to_zarr(ref, mode="w", consolidated=True)
        skill(argv)
        assert len(calls) == 2

    def test_missing_reference_exits_2(self, tmp_path, gridded_store):
        skill = make_identity_skill(
            [],
            extra_args={"reference_grid": str},
            reference_args=("reference_grid",),
        )
        with pytest.raises(SystemExit) as exc:
            skill(
                [
                    "-i",
                    str(gridded_store),
                    "-o",
                    str(tmp_path / "o.zarr"),
                    "--reference-grid",
                    str(tmp_path / "nope.zarr"),
                ]
            )
        assert exc.value.code == 2


class TestStreaming:
    def make_streamer(self, pieces, **declaration):
        @weather_skill(
            "stream-fetch",
            "0.1.0",
            output_type="gridded",
            streaming=True,
            source="toy",
            **declaration,
        )
        def fetch(**params):
            """Stream a toy dataset per period."""
            yield from pieces()

        return fetch

    def test_appends_and_restamps(self, tmp_path):
        def pieces():
            yield make_gridded(n_time=1, start="2026-01-01")
            yield make_gridded(n_time=1, start="2026-01-02")

        fetch = self.make_streamer(pieces)
        out = tmp_path / "out.zarr"
        fetch(["-o", str(out)])
        ds = xr.open_zarr(out, consolidated=True)
        assert ds.sizes["time"] == 2
        assert history_of(out)[0]["skill"] == "stream-fetch"
        assert ds.attrs["weather_skills_source"] == "toy"

    def test_second_run_is_cache_hit(self, tmp_path):
        ran = []

        def pieces():
            ran.append(1)
            yield make_gridded(n_time=1)

        fetch = self.make_streamer(pieces)
        out = tmp_path / "out.zarr"
        fetch(["-o", str(out)])
        fetch(["-o", str(out)])
        assert len(ran) == 1

    def test_midstream_failure_rolls_back_partial_store(self, tmp_path, capsys):
        def pieces():
            yield make_gridded(n_time=1)
            raise RuntimeError("transfer failed")

        fetch = self.make_streamer(pieces)
        out = tmp_path / "out.zarr"
        with pytest.raises(RuntimeError, match="transfer failed"):
            fetch(["-o", str(out)])
        assert not out.exists()
        assert "Removed partial store" in capsys.readouterr().err

    def test_prior_complete_store_survives_pre_write_failure(self, tmp_path):
        def good():
            yield make_gridded(n_time=1)

        fetch = self.make_streamer(good)
        out = tmp_path / "out.zarr"
        fetch(["-o", str(out)])

        def bad():
            raise RuntimeError("nothing fetched")
            yield

        fetch_bad = self.make_streamer(bad, completeness_probe=lambda p: False)
        with pytest.raises(RuntimeError):
            fetch_bad(["-o", str(out)])
        # The failure happened before any write, so the prior store is intact.
        assert out.exists()

    def test_no_yield_exits_1(self, tmp_path, capsys):
        def pieces():
            return
            yield

        fetch = self.make_streamer(pieces)
        with pytest.raises(SystemExit) as exc:
            fetch(["-o", str(tmp_path / "o.zarr")])
        assert exc.value.code == 1
        assert "produced no data" in capsys.readouterr().err

    def test_entry_override_rewrites_effective_end(self, tmp_path):
        def pieces():
            yield EntryOverride({"end": "2026-01-05"})
            yield make_gridded(n_time=1)

        fetch = self.make_streamer(pieces, start_time=True, end_time=True)
        out = tmp_path / "out.zarr"
        fetch(["--start", "2026-01-01", "--end", "2026-01-31", "-o", str(out)])
        args = history_of(out)[0]["args"]
        assert args["start"] == "2026-01-01"
        assert args["end"] == "2026-01-05"


class TestEntryOverrideStandardMode:
    def test_tuple_return_rewrites_entry(self, tmp_path):
        @weather_skill("effective", "0.1.0", output_type="gridded", start_time=True, end_time=True)
        def fetch(start_time, end_time):
            """Fetch with an effective end."""
            return make_gridded(), EntryOverride({"end": "2026-01-02"})

        out = tmp_path / "out.zarr"
        fetch(["--start", "2026-01-01", "--end", "2026-01-31", "-o", str(out)])
        assert history_of(out)[0]["args"]["end"] == "2026-01-02"


class TestPngMode:
    def test_single_input_key_and_software(self, tmp_path, gridded_store):
        fig = FakeFigure()

        @weather_skill("plot", "0.1.0", input_type="any", output_type="png", title=True)
        def plot(ds, title):
            """Plot."""
            return fig

        out = tmp_path / "plot.png"
        plot(["-i", str(gridded_store), "-o", str(out), "--title", "T"])
        metadata = fig.saved["metadata"]
        assert set(metadata) == {"weather_skills_history", "Software"}
        chain = json.loads(metadata["weather_skills_history"])
        assert chain[-1]["skill"] == "plot"
        assert chain[-1]["args"]["title"] == "T"
        assert chain[-1]["input"]["basename"] == "in.zarr"
        assert "hash" in chain[-1]["input"]
        assert fig.saved["kwargs"]["dpi"] == 150
        assert out.exists()

    def test_two_inputs_suffixed_keys(self, tmp_path, gridded_store):
        other = tmp_path / "b.zarr"
        make_gridded(fill=3.0).to_zarr(other, mode="w", consolidated=True)
        fig = FakeFigure()

        @weather_skill(
            "plot-compare",
            "0.1.0",
            input_type=["any", "any"],
            output_type="png",
            history_labels=["a", "b"],
        )
        def plot_compare(ds_a, ds_b):
            """Compare."""
            return fig

        plot_compare(["-i", str(gridded_store), "-i", str(other), "-o", str(tmp_path / "p.png")])
        assert set(fig.saved["metadata"]) == {
            "weather_skills_history_a",
            "weather_skills_history_b",
            "Software",
        }

    def test_named_inputs_default_labels(self, tmp_path, gridded_store):
        other = tmp_path / "mc.zarr"
        make_gridded(fill=3.0).to_zarr(other, mode="w", consolidated=True)
        fig = FakeFigure()

        @weather_skill(
            "plot-mediogram",
            "0.1.0",
            input_type=["any", "any"],
            output_type="png",
            input_names=["forecast", "mclimate"],
        )
        def plot_mediogram(forecast_ds, mclimate_ds):
            """Mediogram."""
            return fig

        plot_mediogram(
            [
                "--forecast",
                str(gridded_store),
                "--mclimate",
                str(other),
                "-o",
                str(tmp_path / "p.png"),
            ]
        )
        metadata = fig.saved["metadata"]
        assert "weather_skills_history_forecast" in metadata
        assert "weather_skills_history_mclimate" in metadata
        # Path strings do not leak into the recorded args.
        chain = json.loads(metadata["weather_skills_history_forecast"])
        assert "forecast" not in chain[-1]["args"]
        assert "output" not in chain[-1]["args"]

    def test_savefig_kwargs_extend(self, tmp_path, gridded_store):
        fig = FakeFigure()

        @weather_skill(
            "plot",
            "0.1.0",
            input_type="any",
            output_type="png",
            savefig_kwargs={"bbox_inches": "tight"},
        )
        def plot(ds):
            """Plot."""
            return fig

        plot(["-i", str(gridded_store), "-o", str(tmp_path / "p.png")])
        assert fig.saved["kwargs"] == {"dpi": 150, "bbox_inches": "tight"}

    def test_no_cache_always_renders(self, tmp_path, gridded_store):
        calls = []

        @weather_skill("plot", "0.1.0", input_type="any", output_type="png")
        def plot(ds):
            """Plot."""
            calls.append(1)
            return FakeFigure()

        argv = ["-i", str(gridded_store), "-o", str(tmp_path / "p.png")]
        plot(argv)
        plot(argv)
        assert len(calls) == 2

    def test_upstream_chain_embedded(self, tmp_path, gridded_store):
        mid = tmp_path / "mid.zarr"
        make_identity_skill([])(["-i", str(gridded_store), "-o", str(mid)])
        fig = FakeFigure()

        @weather_skill("plot", "0.1.0", input_type="any", output_type="png")
        def plot(ds):
            """Plot."""
            return fig

        plot(["-i", str(mid), "-o", str(tmp_path / "p.png")])
        chain = json.loads(fig.saved["metadata"]["weather_skills_history"])
        assert [e["skill"] for e in chain] == ["identity", "plot"]


class TestNoArtifactMode:
    def test_cli_only(self, capsys):
        received = {}

        @weather_skill(
            "resolve-region",
            "0.1.0",
            extra_args={"code": {"positional": True}, "geojson": str},
        )
        def resolve_region(code, geojson):
            """Resolve a country code to a bbox."""
            received.update(code=code, geojson=geojson)
            print("1/2/3/4")

        resolve_region(["KEN"])
        assert received == {"code": "KEN", "geojson": None}
        assert capsys.readouterr().out == "1/2/3/4\n"

    def test_no_output_flag(self):
        @weather_skill("email-report", "0.1.0", extra_args={"to": str})
        def email_report(to):
            """Send a report."""

        with pytest.raises(SystemExit) as exc:
            email_report.parser.parse_args(["-o", "x"])
        assert exc.value.code == 2

    def test_help_epilog(self, capsys):
        @weather_skill("submit-feedback", "0.1.0")
        def submit_feedback():
            """Submit feedback."""

        with pytest.raises(SystemExit):
            submit_feedback(["--help"])
        assert "skill version: 0.1.0" in capsys.readouterr().out

    def test_exit_1_on_data_error(self):
        @weather_skill("submit-feedback", "0.1.0")
        def submit_feedback():
            """Submit feedback."""
            raise DataError("server rejected the submission; retry")

        with pytest.raises(SystemExit) as exc:
            submit_feedback([])
        assert exc.value.code == 1


class TestWriteTail:
    def test_encoding_cleared_and_hook_applies_after(self, tmp_path, gridded_store):
        applied = []

        def write_encoding(ds):
            applied.append(sorted(str(v) for v in ds.variables if ds[v].encoding))
            ds["time"].encoding["units"] = "days since 1970-01-01 00:00:00"

        skill = make_identity_skill([], write_encoding=write_encoding)
        skill(["-i", str(gridded_store), "-o", str(tmp_path / "o.zarr")])
        # At hook time every encoding had been cleared.
        assert applied == [[]]

    def test_input_attrs_carried(self, tmp_path):
        src = tmp_path / "in.zarr"
        ds = make_gridded()
        ds.attrs["Conventions"] = "CF-1.13"
        ds.to_zarr(src, mode="w", consolidated=True)

        @weather_skill("fresh", "0.1.0", input_type="any", output_type="gridded")
        def fresh(ds):
            """Return a dataset built from scratch (no attrs of its own)."""
            return xr.Dataset({"precip": ds["precip"]})

        out = tmp_path / "o.zarr"
        fresh(["-i", str(src), "-o", str(out)])
        assert xr.open_zarr(out, consolidated=True).attrs["Conventions"] == "CF-1.13"

    def test_legacy_attrs_migrated_on_write(self, tmp_path):
        src = tmp_path / "in.zarr"
        ds = make_gridded()
        ds.attrs["rhiza_source"] = "chirps"
        ds.attrs["rhiza_history"] = json.dumps(
            [{"skill": "chirps-fetch", "version": "0.1.0", "args": {}, "input": None}]
        )
        ds.to_zarr(src, mode="w", consolidated=True)
        skill = make_identity_skill([], input_type="any")
        out = tmp_path / "o.zarr"
        skill(["-i", str(src), "-o", str(out)])
        attrs = xr.open_zarr(out, consolidated=True).attrs
        assert "rhiza_source" not in attrs
        assert "rhiza_history" not in attrs
        assert attrs["weather_skills_source"] == "chirps"
        # The legacy chain was read as upstream and extended.
        assert [e["skill"] for e in history_of(out)] == ["chirps-fetch", "identity"]

    def test_existing_output_replaced(self, tmp_path, gridded_store):
        out = tmp_path / "o.zarr"
        make_gridded(fill=9.0, name="other").to_zarr(out, mode="w", consolidated=True)
        skill = make_identity_skill([])
        skill(["-i", str(gridded_store), "-o", str(out)])
        written = xr.open_zarr(out, consolidated=True)
        assert list(written.data_vars) == ["precip"]

    def test_wrote_message(self, tmp_path, gridded_store, capsys):
        skill = make_identity_skill([])
        skill(["-i", str(gridded_store), "-o", str(tmp_path / "o.zarr")])
        assert "Wrote:" in capsys.readouterr().err

    def test_opaque_input_warning(self, tmp_path, gridded_store, capsys):
        skill = make_identity_skill([])
        skill(["-i", str(gridded_store), "-o", str(tmp_path / "o.zarr")])
        assert "treating input as opaque" in capsys.readouterr().err


class TestFunctionParams:
    def test_bbox_and_np_data_roundtrip(self, tmp_path, gridded_store):
        received = {}

        @weather_skill(
            "clip",
            "0.1.0",
            input_type="any",
            output_type="gridded",
            bbox="required",
            dims=True,
        )
        def clip(ds, bbox, dims):
            """Clip to a bbox."""
            received.update(bbox=bbox, dims=dims)
            from weather_skills_core import envelope

            return envelope.bbox_subset(ds, bbox)

        out = tmp_path / "o.zarr"
        clip(["-i", str(gridded_store), "-o", str(out), "--bbox", "2.5/10.5/0.5/12.5"])
        assert received["bbox"] == (2.5, 10.5, 0.5, 12.5)
        assert received["dims"] is None
        written = xr.open_zarr(out, consolidated=True)
        assert written.sizes["latitude"] == 2
        assert written.sizes["longitude"] == 2
        # The recorded args keep the raw bbox string.
        assert history_of(out)[-1]["args"]["bbox"] == "2.5/10.5/0.5/12.5"

    def test_optional_bbox_defaults_to_none(self, tmp_path, gridded_store):
        received = {}
        skill_calls = []

        @weather_skill("subset", "0.1.0", input_type="any", output_type="gridded", bbox="optional")
        def subset(ds, bbox):
            """Subset."""
            received["bbox"] = bbox
            skill_calls.append(1)
            return ds.copy()

        subset(["-i", str(gridded_store), "-o", str(tmp_path / "o.zarr")])
        assert received["bbox"] is None

    def test_workers_passed_but_not_recorded(self, tmp_path, gridded_store):
        received = {}

        @weather_skill("w", "0.1.0", input_type="any", output_type="gridded", workers=4)
        def w(ds, workers):
            """Workers."""
            received["workers"] = workers
            return ds.copy()

        w(["-i", str(gridded_store), "-o", str(tmp_path / "o.zarr"), "--workers", "2"])
        assert received["workers"] == 2

    def test_workers_below_one_exits_2(self, tmp_path, gridded_store):
        skill = make_identity_skill([], workers=4)
        with pytest.raises(SystemExit) as exc:
            skill(["-i", str(gridded_store), "-o", str(tmp_path / "o.zarr"), "--workers", "0"])
        assert exc.value.code == 2

    def test_numpy_values_survive_roundtrip(self, tmp_path, gridded_store):
        @weather_skill(
            "scale", "0.1.0", input_type="any", output_type="gridded", extra_args={"factor": float}
        )
        def scale(ds, factor):
            """Scale."""
            out = ds.copy()
            out["precip"] = ds["precip"] * factor
            return out

        out = tmp_path / "o.zarr"
        scale(["-i", str(gridded_store), "-o", str(out), "--factor", "2.5"])
        written = xr.open_zarr(out, consolidated=True)
        assert float(written["precip"].values.max()) == pytest.approx(2.5)
        assert np.isfinite(written["precip"].values).all()
