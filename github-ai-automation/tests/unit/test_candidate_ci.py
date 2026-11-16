import importlib.util
import json
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "skills/merge-pr/scripts"


@pytest.fixture
def ci(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "tested_candidate_ci", SCRIPTS / "candidate_ci.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "trusted_runtime", lambda *_: None)
    for name, value in {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_SHA": "d" * 40,
        "GITHUB_RUN_ID": "20",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_WORKFLOW_REF": "owner/repo/.github/workflows/ci.yml@refs/heads/main",
    }.items():
        monkeypatch.setenv(name, value)
    return module


class API:
    repo = "owner/repo"

    def __init__(self):
        self.candidate = {
            "repository": self.repo,
            "repository_id": 1,
            "head_repository": "fork/repo",
            "head_repository_id": 9,
            "head_ref": "main",
            "pr_id": 100,
            "pr_number": 2,
            "head_sha": "a" * 40,
            "base_sha": "b" * 40,
            "tree_sha": "c" * 40,
        }
        self.inputs = {
            "candidate_pr": "2",
            "candidate_head": "a" * 40,
            "candidate_base": "b" * 40,
            "candidate_request_id": "9" * 64,
            "candidate_source": "10:1",
        }
        self.admission = {
            "code": "CI_REQUIRED",
            "operation": "process",
            "request_id": "9" * 64,
            "candidate": dict(self.candidate),
            "expected_ci": ["ci.yml"],
        }

    def repo_info(self):
        return {"id": 1, "default_branch": "main"}

    def api(self, path):
        if path.startswith("pulls/"):
            return {
                "state": "open",
                "draft": False,
                "id": 100,
                "head": {
                    "sha": "a" * 40,
                    "ref": "main",
                    "repo": {"id": 9, "full_name": "fork/repo"},
                },
                "base": {"sha": "b" * 40, "repo": {"id": 1}},
            }
        if path.startswith("git/commits/"):
            return {"tree": {"sha": "c" * 40}}
        return {
            "repository": {"id": 1},
            "path": ".github/workflows/github-ai-automation.yml",
            "head_branch": "main",
            "head_sha": "d" * 40,
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "success",
            "run_attempt": 1,
        }

    def pages(self, *args):
        return [
            {
                "id": 77,
                "name": "automation-result-10-1",
                "expired": False,
                "workflow_run": {"id": 10},
            }
        ]

    def artifact(self, *args):
        return self.admission


def test_fork_ci_receipt_preserves_both_repo_identities(ci):
    api = API()
    result = ci.identity(api, api.inputs)
    assert result["head_repository_id"] == 9 and result["repository_id"] == 1
    assert result["head_ref"] == "main" and result["request_id"] == "9" * 64
    assert result["schema"] == "automation-candidate-ci/v2"


@pytest.mark.parametrize(
    "field,value",
    [
        ("head_repository_id", 1),
        ("head_repository", "other/repo"),
        ("head_ref", "feature"),
        ("tree_sha", "f" * 40),
    ],
)
def test_ci_rejects_changed_source_identity(ci, field, value):
    api = API()
    api.admission["candidate"][field] = value
    with pytest.raises(Exception, match="Candidate admission identity changed"):
        ci.identity(api, api.inputs)


def test_ci_rejects_another_request_and_partial_input(ci):
    api = API()
    api.admission["request_id"] = "8" * 64
    with pytest.raises(Exception, match="not admitted"):
        ci.identity(api, api.inputs)
    del api.inputs["candidate_source"]
    with pytest.raises(Exception, match="Missing request authority"):
        ci.identity(api, api.inputs)


def test_enrolled_ci_workflows_bind_all_five_candidate_inputs():
    import yaml

    root = SCRIPTS.parents[2]
    policy = json.loads((root / "github-ai-automation/deployment/merge-policy.json.template").read_text())
    for entry in policy["ci_workflows"]:
        text = (root / ".github/workflows" / entry["file"]).read_text()
        data = yaml.load(text, Loader=yaml.BaseLoader)
        inputs = data["on"]["workflow_dispatch"]["inputs"]
        assert len(inputs) <= 10
        assert {
            "candidate_pr",
            "candidate_head",
            "candidate_base",
            "candidate_request_id",
            "candidate_source",
        } <= set(inputs)
        assert "inputs.candidate_request_id)" in data["run-name"]
        assert "inputs.candidate_source != ''" in text
