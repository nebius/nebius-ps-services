import base64
import json

import pytest
from tests.support import REQUEST_ID, MemoryStore, admitted

from github_ai_automation import controller, events, publication
from github_ai_automation.contracts import Blocked, Candidate
from github_ai_automation.github import GitHub


@pytest.fixture
def candidate():
    return Candidate(
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


def test_rename_out_of_control_plane_remains_report_only(candidate, monkeypatch, tmp_path):
    class API(GitHub):
        def __init__(self):
            pass

        def pages(self, path):
            return [
                {
                    "filename": "docs/copied-policy.py",
                    "previous_filename": "github-ai-automation/src/github_ai_automation/policy.py",
                    "status": "renamed",
                }
            ]

        def candidate(self, number):
            return candidate, {"changed_files": 1, "state": "open", "draft": False}

        def request(self, path):
            raise AssertionError("Protected rename reached effect planning")

    monkeypatch.setattr(controller, "trusted_runtime", lambda *_: {})
    monkeypatch.setattr(controller, "read_policy", lambda *_: {})
    store = MemoryStore()
    admitted(store, candidate)
    result = controller.process(
        API(),
        store,
        tmp_path,
        tmp_path,
        2,
        request_id=REQUEST_ID,
        env={"AI_AUTOMATION_PR_REVIEW": "merge"},
    )
    assert result["code"] == "CONTROL_PLANE_REPORT_ONLY"


def test_renamed_release_fragment_cannot_escape_immutability(monkeypatch):
    class API:
        def request(self, path):
            if path.startswith("git/blobs/"):
                return {
                    "size": 100,
                    "encoding": "base64",
                    "content": base64.b64encode(
                        json.dumps(
                            {"project": "vpngw", "bump": "patch", "summary": "New change"}
                        ).encode()
                    ).decode(),
                }
            return {
                "status": "ahead",
                "files": [
                    {
                        "filename": "docs/old-release.json",
                        "previous_filename": "release-notes/pr-1.json",
                        "status": "renamed",
                    },
                    {"filename": "release-notes/pr-2.json", "status": "added", "sha": "c" * 40},
                ],
            }

    with pytest.raises(Blocked, match="RELEASE_FRAGMENT_MUST_BE_IMMUTABLE"):
        publication.read_fragments(API(), "a" * 40, "b" * 40, "vpngw")


def test_report_mode_cannot_publish_an_earlier_repair(candidate, tmp_path, monkeypatch):
    writes = []
    monkeypatch.setenv("AI_AUTOMATION_PR_REVIEW", "report")
    monkeypatch.setattr(
        events,
        "completed_run",
        lambda *_a, **_kw: {
            "path": ".github/workflows/github-ai-automation.yml",
            "conclusion": "success",
            "run_attempt": 1,
        },
    )

    class API:
        def artifact(self, *args):
            return {
                "pr_number": 2,
                "candidate": candidate.model_dump(),
                "code": "PATCH_PREPARED",
                "record": candidate.key + "/requests/" + REQUEST_ID + "/runs/10_1",
                "request_id": REQUEST_ID,
                "operation": "process",
            }

        def candidate(self, _):
            return candidate, {"state": "open", "draft": False}

        def comment(self, *args, **kwargs):
            pass

        def dispatch(self, *args):
            writes.append("dispatch")

    store = MemoryStore()
    admitted(store, candidate)
    with pytest.raises(Blocked, match="REQUEST_CAPABILITY_DENIED"):
        events.continue_run(API(), store, tmp_path, 10)
    assert writes == []


@pytest.mark.parametrize(
    "override", [{"closure": []}, {"stage_timeout_seconds": 0}, {"max_fix_attempts": True}]
)
def test_invalid_runtime_policy_fails_before_recovery(tmp_path, monkeypatch, override):
    from github_ai_automation.recovery import recover

    config = tmp_path / "github-ai-automation/config"
    config.mkdir(parents=True)
    value = {
        "schema_version": 1,
        "max_fix_attempts": 2,
        "stage_timeout_seconds": 1800,
        "live_timeout_seconds": 3600,
        "live_capabilities": {},
    }
    (config / "policy.json").write_text(json.dumps({**value, **override}))
    for key, value in {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "schedule",
        "GITHUB_REF": "refs/heads/main",
        "AI_AUTOMATION_REPOSITORY_ID": "1",
        "AI_AUTOMATION_PR_REVIEW": "disabled",
        "AI_AUTOMATION_RELEASE_PUBLISH": "false",
    }.items():
        monkeypatch.setenv(key, value)

    class API:
        def request(self, _):
            return {"id": 1, "default_branch": "main"}

    with pytest.raises(Blocked, match="POLICY_INVALID"):
        recover(API(), MemoryStore(), tmp_path)


@pytest.mark.parametrize("incorporated", [True, False])
def test_invalid_policy_blocks_early_pr_effects(candidate, tmp_path, monkeypatch, incorporated):
    from github_ai_automation import closure

    config = tmp_path / "github-ai-automation/config"
    config.mkdir(parents=True)
    (config / "policy.json").write_text("{}")
    monkeypatch.setattr(controller, "trusted_runtime", lambda *_: {})
    monkeypatch.setattr(
        closure, "proof", lambda *_: {"proof": "incorporated"} if incorporated else None
    )

    class API:
        def candidate(self, _):
            return candidate, {"state": "open", "draft": False, "mergeable": True}

        def paths(self, _):
            return ["src/example.py"]

        def pages(self, _):
            return [{"filename": "src/example.py", "status": "modified"}]

        def request(self, _):
            return {"status": "behind"}

    with pytest.raises(Blocked, match="POLICY_INVALID"):
        controller.process(
            API(), MemoryStore(), tmp_path, tmp_path, 2, env={"AI_AUTOMATION_PR_REVIEW": "merge"}
        )
