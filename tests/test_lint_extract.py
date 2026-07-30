"""AST extraction of skill declarations for the stacked argument decorator API."""

import textwrap
from pathlib import Path

from weather_skills_core.lint.extract import (
    extract_script,
    normalize_requirement_name,
)

FIXTURES = Path(__file__).parent / "fixtures" / "lint"


def write_script(tmp_path, body):
    skill_dir = tmp_path / "some-skill"
    scripts = skill_dir / "scripts"
    scripts.mkdir(parents=True)
    script = scripts / "some_skill.py"
    script.write_text(textwrap.dedent(body))
    return script, skill_dir


class TestDeclarationExtraction:
    def test_clean_fixture(self):
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

    def test_stacked_arguments(self, tmp_path):
        script, skill_dir = write_script(
            tmp_path,
            '''
            """Doc."""
            from weather_skills_core import weather_skill

            _SKILL_VERSION = "0.1.0"


            @weather_skill("some-skill", _SKILL_VERSION)
            @weather_skill.argument("--from", required=True, dest="sender")
            @weather_skill.argument("--item", "-x", action="append")
            @weather_skill.argument("code")
            @weather_skill.argument("--verbose", action="store_true")
            def some_skill(sender, item, code, verbose, **kwargs):
                """Doc."""
            ''',
        )
        decl = extract_script(script, skill_dir)
        assert decl.arguments["sender"].flags == ("--from",)
        assert decl.arguments["sender"].required is True
        assert decl.arguments["item"].flags == ("--item", "-x")
        assert decl.arguments["item"].arity == "append"
        assert decl.arguments["code"].positional
        assert decl.arguments["verbose"].arity == "store_true"

    def test_inputs_outputs_arity(self, tmp_path):
        script, skill_dir = write_script(
            tmp_path,
            """
            from weather_skills_core import weather_skill
            _SKILL_VERSION = "0.1.0"

            @weather_skill(
                "some-skill",
                _SKILL_VERSION,
                inputs=["observations", "forecast"],
                outputs=["visualization"],
            )
            @weather_skill.argument("--start-time", required=True)
            @weather_skill.argument("--end-time", required=True)
            @weather_skill.argument("--variable", "-v", action="append")
            def some_skill(a, b, start_time, end_time, variable, **kwargs):
                pass
            """,
        )
        decl = extract_script(script, skill_dir)
        assert decl.has_input and decl.input_arity == "append"
        assert decl.has_output
        assert "start_time" in decl.arguments
        assert "variable" in decl.arguments

    def test_legacy_dynamic_arguments_list_is_dynamic(self, tmp_path):
        script, skill_dir = write_script(
            tmp_path,
            """
            from weather_skills_core import weather_skill
            _SKILL_VERSION = "0.1.0"
            SHARED = []

            @weather_skill("some-skill", _SKILL_VERSION, arguments=SHARED)
            def some_skill(**kwargs):
                pass
            """,
        )
        decl = extract_script(script, skill_dir)
        assert decl.arguments_dynamic
        assert any("deprecated" in note for note in decl.notes)
