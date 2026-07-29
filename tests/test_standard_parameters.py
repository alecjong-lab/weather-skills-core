"""Catalog and Types surface."""

from weather_skills_core import Types, standard_args


def test_standard_args_catalog():
    assert set(standard_args()) == {"time", "start_time", "end_time", "bbox", "variable"}


def test_types_constants():
    assert Types.GRIDDED == "gridded"
    assert Types.PNG == "png"
