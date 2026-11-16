import json
from pathlib import Path

import pytest
from tests.support import REQUEST_ID, MemoryStore, admitted

from github_ai_automation import controller, stages
from github_ai_automation.contracts import (
    Applicability,
    Candidate,
    CIResult,
    GateAssessment,
    Review,
    Scenario,
    Usefulness,
)


@pytest.fixture
def harness(tmp_path, monkeypatch):
    candidate = Candidate(
        repository="owner/repo",
        repository_id=1,
        head_repository="owner/repo",
        head_repository_id=1,
        head_ref="feature",
        pr_id=100,
        pr_number=2,
        head_sha="a" * 40,
        base_sha="b" * 40,
        tree_sha="c" * 40,
    )
    root = Path(__file__).resolve().parents[3]
    store = MemoryStore()
    admitted(store, candidate)
    calls = []
    choices = {"decision": "meaningful", "checks_fail": False, "ci_ready": False}
    env = {
        "AI_AUTOMATION_PR_REVIEW": "merge",
        "GITHUB_SHA": "d" * 40,
        "GITHUB_RUN_ID": "10",
        "GITHUB_RUN_ATTEMPT": "1",
        "AI_AUTOMATION_AGENT_IMAGE": "sha256:" + "f" * 64,
        "TF_MODEL_REVIEW": "test/model",
        "TF_MODEL_FIX": "test/model",
    }
    monkeypatch.setenv("AI_AUTOMATION_PR_REVIEW", "merge")
    monkeypatch.setattr(controller, "trusted_runtime", lambda *_: {})
    monkeypatch.setattr(controller, "package_skills", lambda *_: "e" * 64)
    monkeypatch.setattr(
        "github_ai_automation.source.baseline_context",
        lambda *_: {"README.md": "Accepted behavior"},
    )
    monkeypatch.setattr("github_ai_automation.closure.proof", lambda *_: None)
    monkeypatch.setattr(controller, "missing_fragment_patch", lambda *_: [])

    def snapshot(_, dest):
        dest.mkdir(parents=True)
        source = dest / "source"
        source.mkdir()
        (source / "product.py").write_text("value = 2\n")
        (dest / "diff.txt").write_text("-value = 1\n+value = 2")
        return source, {}

    monkeypatch.setattr(controller, "snapshot", snapshot)

    class API:
        repository = "owner/repo"
        def candidate(self, number):
            return candidate, {"state": "open", "draft": False, "mergeable": True}

        def paths(self, _):
            return ["product.py"]

        def graphql(self, *args):
            return {
                "repository": {
                    "pullRequest": {
                        "closingIssuesReferences": {"nodes": [], "pageInfo": {"hasNextPage": False}}
                    }
                }
            }

        def pages(self, path):
            return (
                [{"filename": "product.py", "status": "modified"}]
                if path.endswith("/files")
                else []
            )

        def request(self, _):
            return {"status": "ahead"}

    def model(**kw):
        kind = kw["output_type"]
        calls.append((kind.__name__, kw["prompt"]))
        if kind is Applicability:
            return Applicability(
                decision="applicable",
                reviewed_paths=["product.py"],
                reason="Exercises cloud runtime",
            )
        if kind is Usefulness:
            return Usefulness(
                decision=choices["decision"],
                problem="Incorrect output",
                benefit="Correct output",
                reviewed_paths=["product.py"],
                evidence=["product.py:1"],
                counterevidence="Small size is not disqualifying",
                explanation="specific concrete benefit",
            )
        if kind is Review:
            return Review(
                verdict="pass",
                findings=[],
                reviewed_paths=["product.py"],
                summary="Reviewed",
                compatibility="compatible",
                spec_assessment="Matches accepted behavior",
                release_fragments=[],
            )
        if kind is GateAssessment:
            return GateAssessment(
                verdict="pass",
                findings=[],
                reviewed_paths=["product.py"],
                summary="Aligned",
                required_checks=["structure"],
            )
        assert kind is Scenario
        return Scenario(
            schema_version=1,
            capability="fixture",
            objective="Check output",
            assertions=[
                {
                    "name": "output",
                    "path": "product.py",
                    "product_action": "Run",
                    "observation": "Read",
                    "expected": "Correct output",
                }
            ],
            workload="run",
            observer="read",
            negative_control="bad output",
            fixture_requirements="isolated",
        )

    def checks(**kw):
        calls.append(("checks", ""))
        if choices["checks_fail"]:
            from github_ai_automation.contracts import Blocked

            raise Blocked("VALIDATION_FAILED_OR_UNAVAILABLE")
        return [{"id": "structure", "exit_code": 0}]

    def ci(*args, **kwargs):
        calls.append(("ci", ""))
        return (
            [
                CIResult(
                    request_id=REQUEST_ID,
                    workflow="ci.yml",
                    run_id=20,
                    attempt=1,
                    workflow_sha="d" * 40,
                    tested_tree=candidate.tree_sha,
                    conclusion="success",
                )
            ]
            if choices["ci_ready"]
            else []
        )

    monkeypatch.setattr(stages, "run_agent", model)
    monkeypatch.setattr(controller, "run_agent", model)
    monkeypatch.setattr(stages, "run_checks", checks)
    monkeypatch.setattr("github_ai_automation.events.reconcile_ci", ci)

    # Trusted fixture inventory scopes just this product.
    fixture = tmp_path / "root"
    (fixture / ".github").mkdir(parents=True)
    (fixture / ".github/merge-policy.json").write_text(
        json.dumps({"ci_workflows": [{"file": "ci.yml", "paths": ["product.py"]}]})
    )
    config = fixture / "github-ai-automation/config"
    config.mkdir(parents=True)
    for name in ("policy.json", "validation.json", "release-projects.json"):
        (config / name).write_text((root / "github-ai-automation/config" / name).read_text())

    def run():
        number = int(env["GITHUB_RUN_ID"])
        value = controller.process(
            API(), store, fixture, tmp_path / f"scratch-{number}", 2, request_id=REQUEST_ID, env=env
        )
        env["GITHUB_RUN_ID"] = str(number + 1)
        return value

    return run, calls, choices, store, candidate


def test_usefulness_precedes_checks_and_no_benefit_requires_blind_confirmation(harness):
    run, calls, choices, *_ = harness
    choices["decision"] = "no_benefit"
    assert run()["code"] == "CLOSURE_PREPARED"
    assert [c[0] for c in calls] == ["Usefulness", "Usefulness"]
    assert calls[0][1] == calls[1][1]  # Independent invocation, no first conclusion leaked.
    assert "specific concrete benefit" not in calls[1][1]


def test_uncertain_contribution_never_reaches_repairs_ci_or_live(harness):
    run, calls, choices, *_ = harness
    choices["decision"] = "uncertain"
    assert run()["code"] == "CONTRIBUTION_UNCONFIRMED"
    assert [c[0] for c in calls] == ["Usefulness"]


def test_ci_callback_reuses_analysis_and_proposes_live_to_separate_owner(harness):
    run, calls, choices, *_ = harness
    assert run()["code"] == "CI_REQUIRED"
    assert [c[0] for c in calls] == ["Usefulness", "Review", "GateAssessment", "checks", "ci"]
    choices["ci_ready"] = True
    assert run()["code"] == "ASSESSMENT_READY"
    assert run()["code"] == "ASSESSMENT_READY"
    names = [c[0] for c in calls]
    for name in ("Usefulness", "Review", "GateAssessment", "checks", "Scenario"):
        assert names.count(name) == 1
    assert "live" not in names
    assert names.index("ci") < names.index("Scenario")


def test_model_pass_does_not_replace_observed_validation(harness):
    run, calls, choices, *_ = harness
    choices["checks_fail"] = True
    assert run()["code"] == "VALIDATION_FAILED_OR_UNAVAILABLE"
    assert not any(name in {"ci", "live"} for name, _ in calls)


def test_missing_focused_profile_blocks_repair(tmp_path):
    from github_ai_automation.contracts import Blocked

    with pytest.raises(Blocked, match="FOCUSED_VALIDATION_UNAVAILABLE"):
        stages.profiles(Path(__file__).resolve().parents[3], ["product.py"], focused=True)


def test_contributor_rationale_is_included_in_usefulness_context(harness):
    run, calls, *_ = harness
    run()
    assert "rationale_and_discussion" in calls[0][1]
