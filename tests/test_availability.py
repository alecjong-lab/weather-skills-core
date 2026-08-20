"""Correctness tests for weather_skills_core.availability."""

from datetime import date

import pytest

from weather_skills_core.availability import (
    Availability,
    available_through,
    ecmwf_s2s_available_through,
    ecmwf_s2s_valid_init,
    load_products,
    pentad_available_through,
)
from weather_skills_core.errors import UsageError

AS_OF = date(2026, 8, 20)  # Thursday


def test_pentad_as_of_aug_20_is_aug_15():
    assert pentad_available_through(AS_OF) == date(2026, 8, 15)


def test_pentad_best_case_two_day_lag():
    assert pentad_available_through(date(2026, 8, 22)) == date(2026, 8, 20)


def test_pentad_month_end():
    assert pentad_available_through(date(2026, 9, 2)) == date(2026, 8, 31)


def test_ecmwf_s2s_two_day_embargo():
    assert ecmwf_s2s_available_through(AS_OF) == date(2026, 8, 18)


def test_ecmwf_s2s_pre_daily_snaps_to_thursday():
    # as_of Tue 2023-06-20 → embargo clock Sun 18 → snap to Thu 15.
    assert ecmwf_s2s_available_through(date(2023, 6, 20)) == date(2023, 6, 15)


def test_ecmwf_s2s_valid_init():
    assert ecmwf_s2s_valid_init(date(2026, 8, 18)) is True
    assert ecmwf_s2s_valid_init(date(2023, 6, 20)) is False  # Tuesday before daily
    assert ecmwf_s2s_valid_init(date(2023, 6, 19)) is True  # Monday


def test_fixed_lag():
    spec = Availability.from_dict(
        {"shape": "range", "policy": "lag", "lag_days": 4, "note": "IMERG late"}
    )
    assert available_through(spec, AS_OF) == date(2026, 8, 16)


def test_zero_lag_is_as_of():
    spec = Availability.from_dict(
        {"shape": "date", "policy": "none", "lag_days": 0, "note": "archive"}
    )
    assert available_through(spec, AS_OF) == AS_OF


def test_policy_none_without_lag_has_no_cap():
    spec = Availability.from_dict(
        {
            "shape": "range",
            "policy": "none",
            "earliest": "1850-01-01",
            "note": "projections",
        }
    )
    assert available_through(spec, AS_OF) is None
    assert spec.earliest == date(1850, 1, 1)


def test_pentad_schedule_via_spec():
    spec = Availability.from_dict(
        {
            "shape": "range",
            "policy": "lag",
            "schedule": "pentad",
            "earliest": "1998-01-01",
            "note": "pentad",
        }
    )
    assert available_through(spec, AS_OF) == date(2026, 8, 15)


def test_ecmwf_s2s_schedule_via_spec():
    spec = Availability.from_dict(
        {
            "shape": "date",
            "policy": "embargo",
            "schedule": "ecmwf-s2s",
            "earliest": "2015-01-01",
            "note": "S2S",
        }
    )
    assert available_through(spec, AS_OF) == date(2026, 8, 18)


def test_from_dict_accepts_date_earliest():
    spec = Availability.from_dict(
        {
            "shape": "range",
            "policy": "lag",
            "lag_days": 1,
            "earliest": date(1981, 9, 1),
            "note": "yaml date",
        }
    )
    assert spec.earliest == date(1981, 9, 1)
    assert spec.to_dict()["earliest"] == "1981-09-01"


def test_roundtrip_dict():
    src = {
        "shape": "range",
        "policy": "lag",
        "lag_days": 4,
        "earliest": "2000-06-01",
        "note": "late",
    }
    spec = Availability.from_dict(src)
    assert spec.to_dict() == src


def test_from_dict_rejects_unknown_key():
    with pytest.raises(UsageError, match="unknown keys"):
        Availability.from_dict({"shape": "range", "policy": "lag", "lag_days": 1, "foo": 1})


def test_from_dict_requires_lag_or_schedule_when_capped():
    with pytest.raises(UsageError, match="lag_days or schedule"):
        Availability.from_dict({"shape": "range", "policy": "lag", "note": "missing lag"})


def test_from_dict_rejects_bad_shape():
    with pytest.raises(UsageError, match="shape"):
        Availability.from_dict({"shape": "grid", "policy": "lag", "lag_days": 1})


def _write_skill(skills_dir, name, body):
    folder = skills_dir / name
    folder.mkdir()
    (folder / "SKILL.md").write_text(body)


def test_load_products_keeps_bare_name_when_variant_shapes_match(tmp_path):
    _write_skill(
        tmp_path,
        "imerg-fetch",
        """---
name: imerg-fetch
description: test
metadata:
  catalog-group: fetchers
  availability:
    shape: range
    policy: lag
    lag_days: 4
    note: late
    variants:
      final:
        lag_days: 110
        note: final
  variables:
    - precip
---
""",
    )
    products = load_products(tmp_path)
    assert set(products) == {"imerg-fetch", "imerg-fetch:final"}
    assert products["imerg-fetch"].lag_days == 4
    assert products["imerg-fetch:final"].lag_days == 110


def test_load_products_omits_bare_name_when_variant_shapes_differ(tmp_path):
    _write_skill(
        tmp_path,
        "dynamical-fetch",
        """---
name: dynamical-fetch
description: test
metadata:
  catalog-group: fetchers
  availability:
    shape: date
    policy: lag
    lag_days: 1
    note: forecast
    variants:
      noaa-gfs-forecast: {}
      noaa-gfs-analysis:
        shape: range
        note: analysis
  variables:
    - precipitation_surface
---
""",
    )
    products = load_products(tmp_path)
    assert "dynamical-fetch" not in products
    assert products["dynamical-fetch:noaa-gfs-forecast"].shape == "date"
    assert products["dynamical-fetch:noaa-gfs-analysis"].shape == "range"


def test_load_products_requires_fetcher_variables(tmp_path):
    _write_skill(
        tmp_path,
        "chirps-fetch",
        """---
name: chirps-fetch
description: test
metadata:
  catalog-group: fetchers
  availability:
    shape: range
    policy: lag
    lag_days: 1
    note: test
---
""",
    )
    with pytest.raises(UsageError, match="metadata.variables"):
        load_products(tmp_path)
