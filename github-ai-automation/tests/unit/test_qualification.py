import json
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from github_ai_automation.contracts import Blocked, Review
from github_ai_automation.evaluation import assess
from github_ai_automation.qualification import LocalEvidence, ReadOnlyGitHub


@pytest.mark.parametrize("method", ["POST", "PATCH", "PUT", "DELETE", "HEAD", "get"])
def test_shadow_rejects_mutations_before_transport(method):
    def transport(request):
        raise AssertionError("Rejected operation reached GitHub")

    api = ReadOnlyGitHub(
        "owner/repo",
        "test",
        client=httpx.Client(
            base_url="https://api.github.com", transport=httpx.MockTransport(transport)
        ),
    )
    with pytest.raises(Blocked, match="QUALIFICATION_GITHUB_WRITE_DENIED"):
        api.request("releases", method=method)


def test_shadow_allows_actual_read_response():
    api = ReadOnlyGitHub(
        "owner/repo",
        "test",
        client=httpx.Client(
            base_url="https://api.github.com",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"id": 1})),
        ),
    )
    assert api.request("") == {"id": 1}


def test_local_evidence_is_immutable_and_deadline_bound():
    store = LocalEvidence(time.time() + 10)
    store.put("stage", {"result": "pass"}, None)
    value, _ = store.read("stage")
    value["result"] = "forged"
    assert store.read("stage")[0]["result"] == "pass"
    with pytest.raises(Blocked, match="LOCAL_EVIDENCE_ALREADY_RECORDED"):
        store.put("stage", value, None)
    store.deadline = time.time() - 1
    with pytest.raises(Blocked, match="OPERATION_EXPIRED"):
        store.put("other", value, None)


@pytest.mark.parametrize("meaningful", [True, False])
def test_shared_evaluator_orders_meaningfulness_before_review(meaningful, tmp_path):
    calls = []

    class Stages:
        def usefulness(self, *_):
            calls.append("value")
            return SimpleNamespace(
                decision="meaningful" if meaningful else "uncertain",
                model_dump=lambda: {"decision": "test"},
            )

        def agent(self, *_):
            calls.append("review")
            return Review(
                verdict="pass",
                findings=[],
                reviewed_paths=[],
                summary="Pass",
                compatibility="compatible",
                spec_assessment="No change",
                release_fragments=[],
            )

        def alignment(self, *_):
            calls.append("alignment")
            return {"checks": [{"id": "structure", "exit_code": 0}]}

    result = assess(Stages(), "context", [], tmp_path, tmp_path)
    assert calls == (["value", "review", "alignment"] if meaningful else ["value"])
    assert result["code"] == ("REVIEW_COMPLETE" if meaningful else "CONTRIBUTION_UNCONFIRMED")


def test_shadow_workflow_has_no_production_authority_or_subscriber():
    root = Path(__file__).resolve().parents[3]
    workflow = (root / "github-ai-automation/deployment/workflows/github-ai-qualification.yml.template").read_text()
    import yaml

    shadow = json.dumps(yaml.safe_load(workflow)["jobs"]["shadow"])
    for forbidden in [
        "contents: write",
        "pull-requests: write",
        "STATE_SECRET",
        "NEBIUS_CREDENTIALS",
        "automation-result-",
        "process-pr",
    ]:
        assert forbidden not in shadow
    continuation = (root / "github-ai-automation/deployment/workflows/github-ai-continuation.yml.template").read_text()
    assert '"GitHub AI qualification"' not in continuation
    assert "qualification-shadow-" in workflow


def test_repository_read_uses_canonical_endpoint_without_trailing_slash():
    seen = []
    api = ReadOnlyGitHub(
        "owner/repo",
        "test",
        client=httpx.Client(
            base_url="https://api.github.com",
            transport=httpx.MockTransport(
                lambda request: (
                    seen.append(request.url.path),
                    httpx.Response(200, json={"id": 1}),
                )[1]
            ),
        ),
    )
    assert api.request("")["id"] == 1
    assert seen == ["/repos/owner/repo"]
