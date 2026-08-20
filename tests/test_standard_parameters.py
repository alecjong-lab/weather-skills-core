"""Cross-check standard_parameters() against the decorator's parser."""

from weather_skills_core import Dataset
from weather_skills_core.decorator import weather_skill
from weather_skills_core.standard_args import standard_parameters


@weather_skill(name="full", version="0.1.0")
@weather_skill.argument("-i", "--input", type=Dataset("observations"), required=True)
@weather_skill.argument("--bbox", required=True)
@weather_skill.argument("--start-time", required=True)
@weather_skill.argument("--end-time", required=True)
@weather_skill.argument("--variable", "-v")
def _full(ds, output, bbox, start_time, end_time, variable, **kwargs):
    return ds


@weather_skill(name="date-only", version="0.1.0")
@weather_skill.argument("--date", required=True)
def _date_only(output, date, **kwargs):
    return date


def _parser_dests(fn):
    return {a.dest for a in fn.parser._actions if a.dest != "help"}


def test_standard_parameters_io_and_canonicals_present():
    dests = _parser_dests(_full)
    assert {"input", "output", "start_time", "end_time", "bbox", "variable"} <= dests


def test_standard_parameters_single_date():
    assert "date" in _parser_dests(_date_only)
    assert "start_time" not in _parser_dests(_date_only)


def test_standard_parameters_catalog_covers_canonical_flags():
    flags = {f for p in standard_parameters() for f in p.flags}
    assert {"--start-time", "--end-time", "--date", "--bbox", "--variable", "-v"} <= flags
    assert "--input" not in flags
    assert "--output" not in flags
