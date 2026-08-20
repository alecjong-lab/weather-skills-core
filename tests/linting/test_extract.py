"""AST extraction of skill declarations for the stacked argument decorator API."""

import textwrap
from pathlib import Path

from weather_skills_core.linting.extract import extract_script, normalize_requirement_name

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "linting"


def write_script(tmp_path, body):
    skill_dir = tmp_path / "some-skill"
    scripts = skill_dir / "scripts"
    scripts.mkdir(parents=True)
    script = scripts / "some_skill.py"
    script.write_text(textwrap.dedent(body))
    return (script, skill_dir)


def test_declaration_extraction_clean_fixture():
    skill_dir = FIXTURES / "clean_tree" / "skills" / "clean-skill"
    decl = extract_script(skill_dir / "scripts" / "clean_skill.py", skill_dir)
    assert decl.error is None
    assert decl.name == "clean-skill"
    assert decl.version_constant and decl.version_passed
    assert decl.has_input and decl.has_output
    assert "bbox" in decl.arguments
    shape = decl.arguments["smoothing"]
    assert shape.flags == ("--smoothing",)
    assert shape.type_name == "int"
    assert "weather-skills-core" in {normalize_requirement_name(d) for d in decl.pep723_deps}


def test_declaration_extraction_stacked_arguments(tmp_path):
    script, skill_dir = write_script(
        tmp_path,
        '\n            """Doc."""\n            from weather_skills_core import weather_skill\n\n            _SKILL_VERSION = "0.1.0"\n\n\n            @weather_skill(name="some-skill", version=_SKILL_VERSION)\n            @weather_skill.argument("--from", required=True, dest="sender")\n            @weather_skill.argument("--item", "-x", action="append")\n            @weather_skill.argument("code")\n            @weather_skill.argument("--verbose", action="store_true")\n            def some_skill(sender, item, code, verbose, **kwargs):\n                """Doc."""\n            ',
    )
    decl = extract_script(script, skill_dir)
    assert decl.arguments["sender"].flags == ("--from",)
    assert decl.arguments["sender"].required is True
    assert decl.arguments["item"].flags == ("--item", "-x")
    assert decl.arguments["item"].arity == "append"
    assert decl.arguments["code"].positional
    assert decl.arguments["verbose"].arity == "store_true"


def test_declaration_extraction_dataset_input_arity(tmp_path):
    script, skill_dir = write_script(
        tmp_path,
        '\n            from weather_skills_core import Dataset, weather_skill\n            _SKILL_VERSION = "0.1.0"\n\n            @weather_skill(\n                name="some-skill",\n                version=_SKILL_VERSION,\n            )\n            @weather_skill.argument("-i", "--input", type=Dataset("any"), nargs=2, required=True)\n            @weather_skill.argument("--start-time", required=True)\n            @weather_skill.argument("--end-time", required=True)\n            @weather_skill.argument("--variable", "-v", action="append")\n            def some_skill(input, output, start_time, end_time, variable, **kwargs):\n                pass\n            ',
    )
    decl = extract_script(script, skill_dir)
    assert decl.has_input and decl.input_arity == "append"
    assert decl.has_output
    assert "start_time" in decl.arguments
    assert "variable" in decl.arguments
    assert decl.arguments["input"].type_name == "Dataset"


def test_declaration_extraction_kwargs_spread_on_argument_is_dynamic(tmp_path):
    script, skill_dir = write_script(
        tmp_path,
        '\n            from weather_skills_core import weather_skill\n            _SKILL_VERSION = "0.1.0"\n            EXTRA = {"type": int}\n\n            @weather_skill(name="some-skill", version=_SKILL_VERSION)\n            @weather_skill.argument("--foo", **EXTRA)\n            def some_skill(**kwargs):\n                pass\n            ',
    )
    decl = extract_script(script, skill_dir)
    assert decl.arguments_dynamic
    assert any("argument(**kwargs) is dynamic" in note for note in decl.notes)
    assert any("reverse check is suppressed" in note for note in decl.notes)


def test_declaration_extraction_arguments_keyword_is_not_supported(tmp_path):
    script, skill_dir = write_script(
        tmp_path,
        '\n            from weather_skills_core import weather_skill\n            _SKILL_VERSION = "0.1.0"\n            SHARED = []\n\n            @weather_skill(name="some-skill", version=_SKILL_VERSION, arguments=SHARED)\n            def some_skill(**kwargs):\n                pass\n            ',
    )
    decl = extract_script(script, skill_dir)
    assert decl.arguments_dynamic
    assert any("arguments= is not a @weather_skill keyword" in note for note in decl.notes)
