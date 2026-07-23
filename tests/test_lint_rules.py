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
