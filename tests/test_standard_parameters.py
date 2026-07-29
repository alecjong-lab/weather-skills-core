"""Cross-check standard_parameters() against the decorator's parser."""

from weather_skills_core.decorator import standard_parameters, weather_skill


@weather_skill(
    "full",
    "0.1.0",
    inputs=["data"],
    outputs=["data"],
    dates="range",
    region="optional",
    variable="single_optional",
)
def _full(ds, bbox, start_time, end_time, variable):
    return ds


@weather_skill("date-only", "0.1.0", outputs=["data"], dates="single")
def _date_only(date):
    return date


def _parser_dests(fn):
    return {a.dest for a in fn.parser._actions if a.dest != "help"}


class TestStandardParameters:
    def test_io_and_toggles_present(self):
        dests = _parser_dests(_full)
        assert {"input", "output", "start", "end", "bbox", "variable"} <= dests

    def test_single_date(self):
        assert "date" in _parser_dests(_date_only)
        assert "start" not in _parser_dests(_date_only)

    def test_catalog_covers_flags(self):
        flags = {f for p in standard_parameters() for f in p.flags}
        assert {"--input", "-i", "--output", "-o", "--start", "--end", "--date", "--bbox"} <= flags
