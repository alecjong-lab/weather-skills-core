"""Tests for region resolution and --region standard-arg injection."""

from types import SimpleNamespace

import pytest

from weather_skills_core import region as region_mod
from weather_skills_core.decorator import Argument
from weather_skills_core.errors import DataError, UsageError
from weather_skills_core.region import (
    bbox_from_geometry,
    clean_region_name,
    lookup_region,
    resolve_region,
)
from weather_skills_core.standard_args import convert_standard_args

_NAIROBI = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {
                "shapeName": "Nairobi",
                "shapeGroup": "KEN",
                "shapeType": "ADM1",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [36.6, -1.4],
                        [37.0, -1.4],
                        [37.0, -1.1],
                        [36.6, -1.1],
                        [36.6, -1.4],
                    ]
                ],
            },
        },
        {
            "type": "Feature",
            "properties": {
                "shapeName": "Elgeyo-Marakwet",
                "shapeGroup": "KEN",
                "shapeType": "ADM1",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[35.0, 0.5], [35.5, 0.5], [35.5, 1.0], [35.0, 1.0], [35.0, 0.5]]],
            },
        },
    ],
}

_WESTLANDS = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {
                "shapeName": "Westlands",
                "shapeGroup": "KEN",
                "shapeType": "ADM2",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [36.7, -1.3],
                        [36.9, -1.3],
                        [36.9, -1.2],
                        [36.7, -1.2],
                        [36.7, -1.3],
                    ]
                ],
            },
        }
    ],
}


@pytest.fixture(autouse=True)
def _clear_admin_cache():
    region_mod._admin_collection.cache_clear()
    yield
    region_mod._admin_collection.cache_clear()


def _fake_admin(iso3, level):
    if iso3 != "KEN":
        raise AssertionError(f"unexpected fetch {iso3} admin-{level}")
    if level == 1:
        return _NAIROBI
    if level == 2:
        return _WESTLANDS
    raise AssertionError(f"unexpected admin level {level}")


def test_clean_region_name():
    assert clean_region_name("United States") == "united_states_of_america"
    assert clean_region_name("South Korea") == "south_korea"
    assert clean_region_name("São Tomé") == "sao_tome"
    assert clean_region_name("Côte d'Ivoire") == "ivory_coast"


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


def test_lookup_empty_is_usage_error():
    with pytest.raises(UsageError, match="non-empty"):
        lookup_region("  ")


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


def test_bbox_wraps_antimeridian():
    geometry = {
        "type": "Polygon",
        "coordinates": [[[170.0, 0.0], [175.0, 0.0], [-170.0, 0.0], [170.0, 0.0]]],
    }
    _n, w, _s, e = bbox_from_geometry(geometry)
    assert w > e
    assert w == 170.0
    assert e == -170.0


def test_lookup_admin1_name(monkeypatch):
    monkeypatch.setattr("weather_skills_core.region._load_admin_geojson", _fake_admin)

    feature = lookup_region("kenya-nairobi")
    assert feature["properties"]["level"] == "admin_1"
    assert feature["properties"]["name"] == "Nairobi"
    assert feature["properties"]["iso3"] == "KEN"
    assert feature["properties"]["region_name"] == "kenya-nairobi"

    n, w, s, e = bbox_from_geometry(feature["geometry"])
    assert n == pytest.approx(-1.1)
    assert s == pytest.approx(-1.4)
    assert w == pytest.approx(36.6)
    assert e == pytest.approx(37.0)


def test_lookup_admin1_iso3_prefix(monkeypatch):
    monkeypatch.setattr("weather_skills_core.region._load_admin_geojson", _fake_admin)

    feature = lookup_region("KEN-nairobi")
    assert feature["properties"]["region_name"] == "kenya-nairobi"
    assert feature["properties"]["level"] == "admin_1"


def test_lookup_admin2(monkeypatch):
    monkeypatch.setattr("weather_skills_core.region._load_admin_geojson", _fake_admin)

    feature = lookup_region("kenya-nairobi-westlands")
    assert feature["properties"]["level"] == "admin_2"
    assert feature["properties"]["name"] == "Westlands"
    assert feature["properties"]["region_name"] == "kenya-nairobi-westlands"


def test_lookup_admin2_without_parent(monkeypatch):
    monkeypatch.setattr("weather_skills_core.region._load_admin_geojson", _fake_admin)

    feature = lookup_region("kenya-westlands")
    assert feature["properties"]["level"] == "admin_2"
    assert feature["properties"]["name"] == "Westlands"


def test_lookup_hyphenated_admin1(monkeypatch):
    monkeypatch.setattr("weather_skills_core.region._load_admin_geojson", _fake_admin)

    feature = lookup_region("kenya-elgeyo-marakwet")
    assert feature["properties"]["level"] == "admin_1"
    assert feature["properties"]["name"] == "Elgeyo-Marakwet"


def test_lookup_unknown_admin_unit(monkeypatch):
    monkeypatch.setattr("weather_skills_core.region._load_admin_geojson", _fake_admin)

    with pytest.raises(DataError, match="admin-1 or admin-2"):
        lookup_region("kenya-not-a-county")


def test_hyphenated_country_is_not_split_into_admin():
    feature = lookup_region("Guinea-Bissau")
    assert feature["properties"]["level"] == "country"
    assert feature["properties"]["iso3"] == "GNB"
