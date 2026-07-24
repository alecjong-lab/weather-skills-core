"""The six lint rules and the score rubric, on the committed fixture trees."""

from pathlib import Path

from weather_skills_core.lint.run import run_lint

FIXTURES = Path(__file__).parent / "fixtures" / "lint"


def findings_for(report, skill):
    return [f for f in report.findings if f.skill == skill]


def rules_for(report, skill):
    return sorted({f.rule for f in findings_for(report, skill)})


def score_of(report, skill):
    return next(s["score"] for s in report.skills if s["name"] == skill)


class TestCleanSkill:
    def test_no_findings_and_maximal_score(self):
        report = run_lint(FIXTURES / "clean_tree", [])
        assert report.findings == []
        assert report.aggregate == 100
        assert score_of(report, "clean-skill") == 100

    def test_out_of_tree_single_skill_skips_cross_rules(self):
        report = run_lint(FIXTURES / "clean_tree" / "skills" / "clean-skill", [])
        assert report.findings == []
        skipped = {s["rule"] for s in report.skipped_rules}
        assert skipped == {"WSK201", "WSK202"}
        assert all("no corpus beyond the target" in s["reason"] for s in report.skipped_rules)


class TestShadowRule:
    def test_each_shadowing_extra_arg_fires_wsk101(self):
        report = run_lint(FIXTURES / "shadow_tree", [])
        shadow_findings = [f for f in report.findings if f.rule == "WSK101"]
        assert {f.flag for f in shadow_findings} == {"--date", "--dims", "--title"}
        assert all(f.severity == "warning" for f in shadow_findings)

    def test_remediation_names_the_standard_toggle(self):
        report = run_lint(FIXTURES / "shadow_tree", [])
        by_flag = {f.flag: f.message for f in report.findings if f.rule == "WSK101"}
        assert "standard date parameter" in by_flag["--date"]
        assert "date=" in by_flag["--date"]
        assert "dims=True" in by_flag["--dims"]

    def test_non_shadowing_extra_arg_does_not_fire(self):
        report = run_lint(FIXTURES / "shadow_tree", [])
        assert not [f for f in report.findings if f.flag == "--period"]

    def _single_skill(self, tmp_path, *, name, decorator_head, extra_args_src):
        skill = tmp_path / name
        scripts_dir = skill / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "run.py").write_text(
            _PEP723
            + "from weather_skills_core import weather_skill\n"
            + '_SKILL_VERSION = "0.1.0"\n'
            + f"@weather_skill({name!r}, _SKILL_VERSION, {decorator_head}"
            + f"extra_args={extra_args_src})\n"
            + "def run():\n    pass\n"
        )
        (skill / "SKILL.md").write_text(_manifest([]))
        return skill

    def test_no_artifact_skill_may_declare_input_output_without_wsk101(self, tmp_path):
        # output_type is absent (no-artifact): the skill cannot declare
        # input_type and owns no decorator --output, so declaring --input and
        # --output through extra_args is the only option, not a shadow.
        skill = self._single_skill(
            tmp_path,
            name="no-artifact",
            decorator_head="",
            extra_args_src="{'input': {'help': 'x'}, 'output': {'help': 'y'}}",
        )
        report = run_lint(skill, [])
        shadow = [f for f in report.findings if f.rule == "WSK101"]
        assert shadow == []

    def test_artifact_skill_declaring_input_still_fires_wsk101(self, tmp_path):
        # output_type is set (artifact-writing): --input belongs to input_type,
        # so declaring it as an extra_arg is still a shadow.
        skill = self._single_skill(
            tmp_path,
            name="artifact",
            decorator_head="output_type='gridded', ",
            extra_args_src="{'input': {'help': 'x'}}",
        )
        report = run_lint(skill, [])
        shadow = [f for f in report.findings if f.rule == "WSK101"]
        assert {f.flag for f in shadow} == {"--input"}

    def test_no_artifact_skill_still_fires_wsk101_for_non_io_shadow(self, tmp_path):
        # The exemption is input/output-only: a no-artifact skill shadowing a
        # toggle parameter (variable) is still flagged.
        skill = self._single_skill(
            tmp_path,
            name="no-artifact-variable",
            decorator_head="",
            extra_args_src="{'variable': {'help': 'x'}}",
        )
        report = run_lint(skill, [])
        shadow = [f for f in report.findings if f.rule == "WSK101"]
        assert {f.flag for f in shadow} == {"--variable"}


class TestCrossSkillRules:
    def test_same_shape_duplicate_fires_wsk201_on_every_holder(self):
        report = run_lint(FIXTURES / "multi_tree", [])
        dupes = [f for f in report.findings if f.rule == "WSK201" and f.flag == "--method"]
        assert {f.skill for f in dupes} == {"alpha", "beta", "gamma"}
        alpha = next(f for f in dupes if f.skill == "alpha")
        assert "beta (target)" in alpha.message and "gamma (target)" in alpha.message
        assert "propose promoting" in alpha.message

    def test_divergent_shape_fires_wsk202_naming_the_difference(self):
        report = run_lint(FIXTURES / "multi_tree", [])
        divergences = [f for f in report.findings if f.rule == "WSK202"]
        gamma_method = next(f for f in divergences if f.skill == "gamma" and f.flag == "--method")
        assert "choices" in gamma_method.message
        # alpha and beta share --method at the same shape: no divergence
        # between them, only against gamma.
        alpha_method = next(f for f in divergences if f.skill == "alpha" and f.flag == "--method")
        assert "gamma (target)" in alpha_method.message
        assert "beta" not in alpha_method.message

    def test_type_divergence_detected(self):
        report = run_lint(FIXTURES / "multi_tree", [])
        window = [f for f in report.findings if f.rule == "WSK202" and f.flag == "--window"]
        assert {f.skill for f in window} == {"alpha", "gamma"}
        assert "type int vs float" in next(f for f in window if f.skill == "alpha").message

    def test_upward_discovery_reports_findings_only_for_the_target(self):
        report = run_lint(FIXTURES / "multi_tree" / "skills" / "alpha", [])
        assert {f.skill for f in report.findings} == {"alpha"}
        assert [s["name"] for s in report.skills] == ["alpha"]
        assert not report.skipped_rules  # siblings provide the corpus
        dupe = next(f for f in report.findings if f.rule == "WSK201")
        tree = str((FIXTURES / "multi_tree" / "skills").resolve())
        assert f"beta ({tree})" in dupe.message


class TestSkillMdRule:
    def test_drift_fires_in_both_directions(self):
        report = run_lint(FIXTURES / "drift_tree", [])
        drift = [f for f in findings_for(report, "drift-skill") if f.rule == "WSK301"]
        messages = {f.flag: f.message for f in drift}
        assert "--window" in messages and "not mentioned in SKILL.md" in messages["--window"]
        assert "--nonexistent" in messages
        assert "does not declare it" in messages["--nonexistent"]

    def test_missing_manifest_is_its_own_finding(self):
        report = run_lint(FIXTURES / "drift_tree", [])
        missing = [f for f in findings_for(report, "no-manifest") if f.rule == "WSK301"]
        assert len(missing) == 1
        assert "SKILL.md is missing" in missing[0].message

    def test_documented_skill_produces_no_drift(self):
        report = run_lint(FIXTURES / "clean_tree", [])
        assert not [f for f in report.findings if f.rule == "WSK301"]


class TestVersionRule:
    def test_missing_constant_and_unpassed_constant_fire(self):
        report = run_lint(FIXTURES / "version_tree", [])
        assert rules_for(report, "no-constant") == ["WSK401"]
        assert "no module-level _SKILL_VERSION" in findings_for(report, "no-constant")[0].message
        assert rules_for(report, "literal-version") == ["WSK401"]
        assert "not passed" in findings_for(report, "literal-version")[0].message

    def test_conformant_version_does_not_fire(self):
        report = run_lint(FIXTURES / "clean_tree", [])
        assert not [f for f in report.findings if f.rule == "WSK401"]


class TestCoreDepRule:
    def test_missing_block_and_missing_dependency_fire(self):
        report = run_lint(FIXTURES / "dep_tree", [])
        assert rules_for(report, "no-block") == ["WSK402"]
        assert "no PEP 723" in findings_for(report, "no-block")[0].message
        assert rules_for(report, "missing-core") == ["WSK402"]
        assert "do not declare weather-skills-core" in (
            findings_for(report, "missing-core")[0].message
        )

    def test_declared_core_dep_does_not_fire(self):
        report = run_lint(FIXTURES / "clean_tree", [])
        assert not [f for f in report.findings if f.rule == "WSK402"]


class TestAnalysisFailures:
    def test_unanalyzable_scripts_fire_wsk001_and_others_still_lint(self):
        report = run_lint(FIXTURES / "errors_tree", [])
        assert rules_for(report, "broken-syntax") == ["WSK001"]
        assert rules_for(report, "no-decorator") == ["WSK001"]
        assert findings_for(report, "good-skill") == []
        assert score_of(report, "broken-syntax") == 0
        assert score_of(report, "no-decorator") == 0
        assert score_of(report, "good-skill") == 100


_PEP723 = '# /// script\n# dependencies = ["weather-skills-core"]\n# ///\n'


def _script(skill_name, func_name, extra_args_src):
    return (
        _PEP723
        + "from weather_skills_core import weather_skill\n"
        + '_SKILL_VERSION = "0.1.0"\n'
        + f"@weather_skill({skill_name!r}, _SKILL_VERSION, input_type='any', "
        + f"output_type='same', extra_args={extra_args_src})\n"
        + f"def {func_name}(ds):\n    return ds\n"
    )


def _manifest(flags):
    lines = ["# multi-skill", "", "## Usage", "", "### Arguments"]
    lines += [f"- `{flag}` — description." for flag in flags]
    lines += ["- `--input`, `-i` — input Zarr.", "- `--output`, `-o` — output Zarr."]
    return "\n".join(lines) + "\n"


def make_multi_script_skill(tmp_path, *, scripts, manifest_flags):
    """A single skill directory holding several decorated scripts and a manifest."""
    skill = tmp_path / "multi-skill"
    scripts_dir = skill / "scripts"
    scripts_dir.mkdir(parents=True)
    for filename, source in scripts.items():
        (scripts_dir / filename).write_text(source)
    (skill / "SKILL.md").write_text(_manifest(manifest_flags))
    return skill


class TestMultiScriptSkill:
    def test_a_skills_own_scripts_are_not_a_corpus(self, tmp_path):
        # Two scripts in one skill declaring the same one-off flag are not a
        # cross-skill collision; the cross-skill rules are skipped for a lone
        # skill however many scripts it holds.
        skill = make_multi_script_skill(
            tmp_path,
            scripts={
                "one.py": _script("one", "one", "{'shared': {'type': int, 'help': 'x'}}"),
                "two.py": _script("two", "two", "{'shared': {'type': int, 'help': 'x'}}"),
            },
            manifest_flags=["--shared"],
        )
        report = run_lint(skill, [])
        assert not [f for f in report.findings if f.rule in ("WSK201", "WSK202")]
        assert {s["rule"] for s in report.skipped_rules} == {"WSK201", "WSK202"}

    def test_wsk301_reverse_check_unions_every_script(self, tmp_path):
        # --foo is declared only by one.py, --bar only by two.py; documenting
        # both must not read as undeclared against the script that omits it.
        skill = make_multi_script_skill(
            tmp_path,
            scripts={
                "one.py": _script("one", "one", "{'foo': {'type': int, 'help': 'x'}}"),
                "two.py": _script("two", "two", "{'bar': {'type': int, 'help': 'x'}}"),
            },
            manifest_flags=["--foo", "--bar"],
        )
        report = run_lint(skill, [])
        assert [f for f in report.findings if f.rule == "WSK301"] == []

    def test_wsk301_reverse_check_fires_once_for_an_undeclared_documented_flag(self, tmp_path):
        # --ghost is documented but declared by no script: one reverse finding,
        # not one per script.
        skill = make_multi_script_skill(
            tmp_path,
            scripts={
                "one.py": _script("one", "one", "{'foo': {'type': int, 'help': 'x'}}"),
                "two.py": _script("two", "two", "{'bar': {'type': int, 'help': 'x'}}"),
            },
            manifest_flags=["--foo", "--bar", "--ghost"],
        )
        report = run_lint(skill, [])
        ghost = [f for f in report.findings if f.rule == "WSK301" and f.flag == "--ghost"]
        assert len(ghost) == 1
        assert "does not declare it in any script" in ghost[0].message

    def test_findings_and_scores_key_by_script_not_display_name(self, tmp_path):
        # Both scripts pick the display name "dup"; the collision-proof key is
        # the relative script path, and a finding attaches to one script only.
        skill = make_multi_script_skill(
            tmp_path,
            scripts={
                "one.py": _script("dup", "one", "{'date': {'type': str, 'help': 'x'}}"),
                "two.py": _script("dup", "two", "{'clean': {'type': int, 'help': 'x'}}"),
            },
            manifest_flags=["--date", "--clean"],
        )
        report = run_lint(skill, [])
        assert {s["key"] for s in report.skills} == {
            "multi-skill/scripts/one.py",
            "multi-skill/scripts/two.py",
        }
        assert {s["name"] for s in report.skills} == {"dup"}
        shadow = [f for f in report.findings if f.rule == "WSK101"]
        assert len(shadow) == 1 and shadow[0].file.endswith("one.py")

    def test_dynamic_extra_args_suppresses_the_wsk301_reverse_check(self, tmp_path):
        # extra_args is a name reference: the declared-flag set is unknown, so
        # documenting flags must not fire the reverse check, and the report
        # surfaces the suppression note.
        skill = tmp_path / "multi-skill"
        scripts_dir = skill / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "one.py").write_text(
            _PEP723
            + "from weather_skills_core import weather_skill\n"
            + '_SKILL_VERSION = "0.1.0"\n'
            + 'SHARED = {"foo": {"type": int}}\n'
            + "@weather_skill('one', _SKILL_VERSION, input_type='any', "
            + "output_type='same', extra_args=SHARED)\n"
            + "def one(ds):\n    return ds\n"
        )
        (skill / "SKILL.md").write_text(_manifest(["--foo", "--bar", "--baz"]))
        report = run_lint(skill, [])
        reverse = [
            f for f in report.findings if f.rule == "WSK301" and "does not declare it" in f.message
        ]
        assert reverse == []
        assert any("reverse check is suppressed" in note for note in report.notes)


class TestScoreRubric:
    def test_warning_only_rule_scores_half_of_that_rule(self):
        # shadow-skill: 4 applicable rules (no corpus), one rule at its
        # warning floor -> (0.5 + 3) / 4 = 87.5, rounded to 88.
        report = run_lint(FIXTURES / "shadow_tree", [])
        assert score_of(report, "shadow-skill") == 88

    def test_cross_rules_excluded_from_the_denominator_when_skipped(self):
        # Linted alone, clean-skill scores over the 4 per-skill rules only;
        # skipped rules never count for or against it.
        report = run_lint(FIXTURES / "clean_tree" / "skills" / "clean-skill", [])
        assert score_of(report, "clean-skill") == 100
        assert {s["rule"] for s in report.skipped_rules} == {"WSK201", "WSK202"}

    def test_aggregate_is_the_mean_of_per_skill_scores(self):
        report = run_lint(FIXTURES / "errors_tree", [])
        scores = [s["score"] for s in report.skills]
        assert report.aggregate == round(sum(scores) / len(scores))
