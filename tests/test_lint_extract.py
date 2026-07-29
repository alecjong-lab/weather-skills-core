"""AST extraction of skill declarations for the simplified decorator API."""

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
        assert decl.toggles["region"] == "optional"
        shape = decl.extra_args["smoothing"]
        assert shape.flags == ("--smoothing",)
        assert shape.type_name == "int"
        assert "weather-skills-core" in {normalize_requirement_name(d) for d in decl.pep723_deps}

    def test_tuple_list_extra_args(self, tmp_path):
        script, skill_dir = write_script(
            tmp_path,
            '''
            """Doc."""
            from weather_skills_core import weather_skill

            _SKILL_VERSION = "0.1.0"


            @weather_skill(
                "some-skill",
                _SKILL_VERSION,
                extra_args=[
                    (("--from",), {"required": True, "dest": "sender"}),
                    (("--item", "-x"), {"action": "append"}),
                    (("code",), {}),
                    (("--verbose",), {"action": "store_true"}),
                ],
            )
            def some_skill(sender, item, code, verbose):
                """Doc."""
            ''',
        )
        decl = extract_script(script, skill_dir)
        assert decl.extra_args["sender"].flags == ("--from",)
        assert decl.extra_args["sender"].required is True
        assert decl.extra_args["item"].flags == ("--item", "-x")
        assert decl.extra_args["item"].arity == "append"
        assert decl.extra_args["code"].positional
        assert decl.extra_args["verbose"].arity == "store_true"

    def test_inputs_outputs_arity(self, tmp_path):
        script, skill_dir = write_script(
            tmp_path,
            """
            from weather_skills_core import weather_skill
            _SKILL_VERSION = "0.1.0"

            @weather_skill(
                "some-skill",
                _SKILL_VERSION,
                inputs=["data", "forecast"],
                outputs=["visualization"],
                dates="range",
                variable="multiple_optional",
            )
            def some_skill(a, b, start_time, end_time, variable):
                pass
            """,
        )
        decl = extract_script(script, skill_dir)
        assert decl.has_input and decl.input_arity == "append"
        assert decl.has_output
        assert decl.toggles["dates"] == "range"
        assert decl.toggles["variable"] == "multiple_optional"

    def test_non_list_extra_args_is_dynamic(self, tmp_path):
        script, skill_dir = write_script(
            tmp_path,
            """
            from weather_skills_core import weather_skill
            _SKILL_VERSION = "0.1.0"
            EXTRAS = []

            @weather_skill("some-skill", _SKILL_VERSION, extra_args=EXTRAS)
            def some_skill():
                pass
            """,
        )
        decl = extract_script(script, skill_dir)
        assert decl.extra_args_dynamic
        assert any("not a literal list" in note for note in decl.notes)
