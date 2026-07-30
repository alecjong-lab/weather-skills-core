"""Cross-check standard_parameters() against the decorator's parser."""

from weather_skills_core.decorator import standard_parameters, weather_skill

@weather_skill(
    "full",
    "0.1.0",
    inputs=["observations"],
    outputs=["observations"]
)
@weather_skill.argument("--bbox", required=True)
@weather_skill.argument("--start-time", required=True)
@weather_skill.argument("--end-time", required=True)
@weather_skill.argument("--variable", "-v")
def _full(ds, bbox, start_time, end_time, variable, **kwargs):
    return ds

@weather_skill(
    "date-only",
    "0.1.0",
    outputs=["observations"]
)
@weather_skill.argument("--date", required=True)
def _date_only(date, **kwargs):
    return date

def _parser_dests(fn):
    return {a.dest for a in fn.parser._actions if a.dest != "help"}

class TestStandardParameters:
    def test_io_and_canonicals_present(self):
        dests = _parser_dests(_full)
        assert {"input", "output", "start_time", "end_time", "bbox", "variable"} <= dests

    def test_single_date(self):
        assert "date" in _parser_dests(_date_only)
        assert "start_time" not in _parser_dests(_date_only)

    def test_catalog_covers_flags(self):
        flags = {f for p in standard_parameters() for f in p.flags}
        assert {
            "--input",
            "-i",
            "--output",
            "-o",
            "--start-time",
            "--end-time",
            "--date",
            "--bbox",
        } <= flags
