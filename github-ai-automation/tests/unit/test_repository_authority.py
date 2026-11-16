import httpx
import pytest
from tests.support import MemoryStore

from github_ai_automation import baseline, controller, rollout
from github_ai_automation.contracts import Blocked, digest
from github_ai_automation.github import GitHub


@pytest.mark.parametrize("operation", ["rollout", "baseline"])
@pytest.mark.parametrize(
    "permission,role,allowed",
    [
        ("write", "maintain", True),
        ("admin", "admin", True),
        ("write", "write", False),
        ("write", "custom-maintainer", False),
        ("read", "maintain", False),
        ("none", "maintain", False),
        ("write", None, False),
    ],
)
def test_maintainer_authority_uses_real_github_permission_response(
    operation, permission, role, allowed, monkeypatch, tmp_path
):
    def response(request):
        assert request.method == "GET"
        assert request.url.path == "/repos/owner/repo/collaborators/operator/permission"
        return httpx.Response(200, json={"permission": permission, "role_name": role})

    monkeypatch.setattr(controller, "trusted_runtime", lambda *_: {"id": 1})
    monkeypatch.setattr(baseline, "trusted_runtime", lambda *_: {"id": 1})
    inspection = {"provenance": "maintainer_adoption"}
    monkeypatch.setattr(baseline, "inspect", lambda *_: inspection)
    event = {"sender": {"type": "User", "id": 7, "login": "operator"}}
    env = {"GITHUB_ACTOR_ID": "7", "GITHUB_RUN_ID": "11"}
    store = MemoryStore()
    with httpx.Client(
        base_url="https://api.github.com", transport=httpx.MockTransport(response)
    ) as client:
        api = GitHub("owner/repo", "fixture", client)

        def invoke():
            if operation == "rollout":
                return rollout.configure(
                    api,
                    store,
                    {
                        "schema_version": 1,
                        "repository_id": 1,
                        "generation": 1,
                        "phase": "qualification",
                        "prs": [],
                        "releases": [],
                    },
                    0,
                    event,
                    env,
                )
            return baseline.adopt(
                api,
                store,
                tmp_path,
                "nebius-cxcli",
                "nebius-cxcli-v0.1.9",
                10,
                digest(inspection),
                0,
                event,
                env,
            )

        if allowed:
            invoke()
        else:
            with pytest.raises(Blocked, match=operation.upper() + "_ACTOR_UNAUTHORIZED"):
                invoke()
            assert store.scan("repositories") == []
