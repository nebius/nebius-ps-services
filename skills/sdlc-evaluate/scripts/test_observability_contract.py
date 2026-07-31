#!/usr/bin/env python3
"""Contract tests for observability-backed SDLC evaluation."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
EVALUATE = ROOT / "sdlc-evaluate"


class ObservabilityEvaluationContractTest(unittest.TestCase):
    def read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def test_observability_is_limited_to_predefined_operational_criteria(self) -> None:
        skill = self.read(EVALUATE / "SKILL.md")
        normalized = " ".join(skill.split())

        self.assertIn("predefined runtime operational criterion", normalized)
        self.assertIn("release, canary, performance, reliability", normalized)
        self.assertIn("exact measurement and unit", normalized)
        self.assertIn("comparator and threshold", normalized)
        self.assertIn("candidate version or workload identity", normalized)
        self.assertIn("candidate and baseline/control attribution", normalized)
        self.assertIn("required coverage", normalized)
        self.assertIn(
            "Before any Grafana readiness or data call, create one query-admission "
            "record for one criterion",
            normalized,
        )
        self.assertIn("explicit pass and fail conditions", normalized)
        self.assertIn("explicit inconclusive conditions", normalized)
        self.assertIn("how the result can change the criterion grade", normalized)
        self.assertIn("non-Grafana provenance", normalized)
        self.assertIn(
            "readiness call is not valid signal discovery",
            normalized,
        )
        self.assertIn("Skip Grafana with zero calls", normalized)
        self.assertIn("`inconclusive` with `SPEC_GAP`", normalized)
        self.assertIn("structured `criterion_fit`", normalized)
        self.assertIn("at most one data query per provider invocation", normalized)
        self.assertIn("Remaining query budget is a ceiling, not a target", normalized)
        self.assertIn("never batch independent criteria or signal families", normalized)
        self.assertIn("does not replace functional or semantic checks", normalized)
        self.assertIn(
            "at least two named attribution, coverage, or dependency interpretations",
            normalized,
        )
        self.assertIn("Do not use deep mode to discover a missing gate", normalized)

    def test_production_read_and_experiment_boundaries_are_distinct(self) -> None:
        skill = self.read(EVALUATE / "SKILL.md")
        normalized = " ".join(skill.split())

        self.assertIn("Passive read-only production telemetry", normalized)
        self.assertIn("must not execute a production workload", normalized)
        self.assertIn(
            "confirmed non-production or disposable Live Experiment Environment",
            normalized,
        )
        self.assertIn("POLICY_BLOCK", skill)

    def test_inconclusive_is_a_first_class_criterion_result(self) -> None:
        skill = self.read(EVALUATE / "SKILL.md")
        template = self.read(EVALUATE / "assets" / "templates" / "evaluate.md.template")
        normalized = " ".join(skill.split())

        self.assertIn("`pass`, `fail`, or `inconclusive`", normalized)
        self.assertIn("<pass, fail, inconclusive>", template)
        self.assertIn("ENVIRONMENT_DEFECT", skill)
        self.assertIn("HUMAN_INPUT_REQUIRED", skill)
        self.assertIn("SPEC_GAP", skill)
        self.assertIn("A provider `rejected` result", skill)
        self.assertIn("is never `ENVIRONMENT_DEFECT`", skill)
        self.assertIn("`missing_authority` maps to `HUMAN_INPUT_REQUIRED`", skill)
        self.assertIn(
            "`unresolved_selector`, `invalid_window`, and `irrelevant_evidence` map to",
            skill,
        )
        self.assertIn("`invalid_budget` maps to `POLICY_BLOCK`", skill)
        self.assertIn(
            "State moves to `evaluated` only when every required criterion passes",
            normalized,
        )

    def test_template_records_bounded_observability_provenance(self) -> None:
        template = self.read(EVALUATE / "assets" / "templates" / "evaluate.md.template")

        for term in (
            "## Observability Evidence",
            "Decision",
            "Evidence Source",
            "Authority And Scope Provenance",
            "Admitted Criterion ID",
            "Signal Family And Candidate",
            "Signal Provenance",
            "Measurement",
            "Candidate Selector Attribution",
            "Baseline Or Control Identity",
            "Baseline Or Control Selector Attribution",
            "Candidate Window",
            "Baseline Or Control Window",
            "Required Coverage",
            "Pass Condition",
            "Fail Condition",
            "Inconclusive Conditions",
            "Grade-Change Relation",
            "Observed Comparison",
            "Criterion Interpretation",
            "Connectivity State",
            "Rejection Reason",
            "Connectivity Checks",
            "Provider Stages Used",
            "Fast Data Queries",
            "Deep Data Queries",
            "Total Data Queries",
            "Remaining Query Budget",
            "Remaining Fast Query Budget",
            "Remaining Deep Query Budget",
            "Coverage And Data Gaps",
        ):
            self.assertIn(term, template)

    def test_eval_cases_require_zero_call_admission_and_sequential_queries(
        self,
    ) -> None:
        cases = self.read(EVALUATE / "evals" / "trigger-prompts.md")
        normalized = " ".join(cases.split())

        self.assertIn(
            "Do not invoke readiness or query data",
            normalized,
        )
        self.assertIn(
            "Readiness and datasource discovery must not be used to find a useful "
            "signal",
            normalized,
        )
        self.assertIn("make zero Grafana calls", normalized)
        self.assertIn("one `signal_fit`, one `criterion_fit`", normalized)
        self.assertIn("at most one pre-admitted query", normalized)
        self.assertIn("Never batch criteria or signal families", normalized)


if __name__ == "__main__":
    unittest.main()
