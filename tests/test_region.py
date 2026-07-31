"""Tests for region resolution and --region standard-arg injection."""

from types import SimpleNamespace

import pytest

from weather_skills_core.decorator import Argument
from weather_skills_core.errors import DataError, UsageError
from weather_skills_core.region import resolve_region
from weather_skills_core.standard_args import convert_standard_args


def test_resolve_kenya_iso3():
    bbox, gdf = resolve_region("KEN")
    n, w, s, e = bbox
    assert n > s and w < e
    assert 4.0 < n < 6.0
    assert 33.0 < w < 35.0
    assert -5.5 < s < -4.0
    assert 41.0 < e < 43.0
    assert list(gdf["iso3"]) == ["KEN"]
    assert list(gdf["name"]) == ["Kenya"]


def test_resolve_by_name_case_insensitive():
    bbox_code, _ = resolve_region("KEN")
    bbox_name, gdf = resolve_region("Kenya")
    assert bbox_code == bbox_name
    assert list(gdf["iso3"]) == ["KEN"]


def test_resolve_unknown():
    with pytest.raises(DataError, match="not a known"):
        resolve_region("ZZZ")


def test_convert_region_fills_bbox_and_gdf():
    args = SimpleNamespace(region="KEN", bbox=None)
    arguments = [Argument("--region"), Argument("--bbox")]
    params = convert_standard_args(args, arguments)
    assert params["bbox"] == resolve_region("KEN")[0]
    assert list(params["region"]["iso3"]) == ["KEN"]


def test_convert_region_and_bbox_conflict():
    args = SimpleNamespace(region="KEN", bbox="5/34/-5/42")
    arguments = [Argument("--region"), Argument("--bbox")]
    with pytest.raises(UsageError, match="not both"):
        convert_standard_args(args, arguments)


def test_convert_region_injects_bbox_without_bbox_flag():
    args = SimpleNamespace(region="kenya")
    arguments = [Argument("--region")]
    params = convert_standard_args(args, arguments)
    assert params["bbox"][0] > params["bbox"][2]
