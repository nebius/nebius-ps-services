import json

import pytest
from tests.support import REQUEST_ID, MemoryStore, admitted

from github_ai_automation import closure, controller, events, state
from github_ai_automation.contracts import Blocked, Candidate


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


def test_identical_tree_on_other_target_is_not_duplicate(candidate):
    class API:
        def request(self, path):
            if path == "":
                return {"default_branch": "main"}
            if path.startswith("compare/"):
                return {"status": "ahead"}
            return {"tree": {"sha": candidate.tree_sha}}

        def pages(self, _):
            return [
                {
                    "id": 90,
                    "number": 1,
                    "base": {"sha": candidate.base_sha, "ref": "release", "repo": {"id": 1}},
                    "head": {"sha": "d" * 40, "repo": {"id": 1}},
                }
            ]

    assert closure.proof(API(), candidate) is None


def test_control_plane_excluded_before_closure(monkeypatch, candidate, tmp_path):
    monkeypatch.setattr(controller, "trusted_runtime", lambda *_: {})

    class API:
        def candidate(self, _):
            return candidate, {"state": "open", "draft": False}

        def paths(self, _):
            return [".github/workflows/ci.yml"]

        def request(self, _):
            raise AssertionError("Closure must not run for control-plane PRs")

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


def test_reconcile_ingests_all_ci_after_lost_continuation(candidate, tmp_path, monkeypatch):
    monkeypatch.setenv("AI_AUTOMATION_PR_REVIEW", "merge")
    root = tmp_path / ".github"
    root.mkdir()
    workflows = ["first.yml", "second.yml"]
    (root / "merge-policy.json").write_text(
        json.dumps({"ci_workflows": [{"file": w, "paths": ["src/*"]} for w in workflows]})
    )

    class API:
        repository = "owner/repo"

        def candidate(self, _):
            return candidate, {}

        def paths(self, _):
            return ["src/a.py"]

        def request(self, path):
            if path == "":
                return {"id": 1, "default_branch": "main"}
            if "/workflows/" in path:
                workflow = path.split("/workflows/")[1].split("/")[0]
                return {
                    "workflow_runs": [
                        {
                            "id": workflows.index(workflow) + 10,
                            "display_title": f"ai-candidate-2-{candidate.head_sha}-{candidate.base_sha}-{REQUEST_ID}",
                            "status": "completed",
                            "conclusion": "success",
                        }
                    ]
                }
            number = int(path.split("/")[-1])
            return {
                "id": number,
                "repository": {"id": 1},
                "event": "workflow_dispatch",
                "head_branch": "main",
                "path": ".github/workflows/" + workflows[number - 10],
                "status": "completed",
                "conclusion": "success",
                "head_sha": "d" * 40,
                "run_attempt": 1,
            }

        def pages(self, path, *, field=None):
            return self.request(path)[field]

        def artifact(self, run_id, name, filename):
            return {
                **candidate.model_dump(),
                "schema": "automation-candidate-ci/v2",
                "request_id": REQUEST_ID,
                "source": "5:1",
                "run_id": run_id,
                "attempt": 1,
                "workflow_sha": "d" * 40,
                "workflow": workflows[run_id - 10],
                "conclusion": "success",
                "jobs": ["verify"],
            }

    store = MemoryStore()
    lease = state.acquire(store, candidate.key + "/lock")
    admitted(store, candidate)
    for workflow in workflows:
        store.put(
            events.ci_key(candidate, REQUEST_ID, workflow),
            {"candidate": candidate.model_dump(), "source": "5:1"},
            None,
        )
    rows = events.reconcile_ci(API(), store, lease, tmp_path, candidate, REQUEST_ID)
    assert {v.workflow for v in rows} == set(workflows)
    assert all(v.request_id == REQUEST_ID for v in rows)


def test_proven_non_submission_allows_new_bounded_merge_attempt():
    store = MemoryStore()
    lease = state.acquire(store, "pr/lock")

    def rejected(_):
        raise state.NotSubmitted()

    with pytest.raises(Blocked, match="EFFECT_NOT_SUBMITTED"):
        state.effect(store, lease, "pr/merge", {"review": 1}, lambda _: None, rejected)
    assert state.effect(
        store, lease, "pr/merge", {"review": 2}, lambda _: None, lambda _: {"accepted": True}
    ) == {"accepted": True}
    assert store.read("pr/merge")[0]["previous_attempts"][0]["status"] == "not_submitted"


def test_conflict_export_preserves_both_parents_and_rejects_markers(tmp_path, monkeypatch):
    import subprocess

    from github_ai_automation import source
    from github_ai_automation.sandbox import command

    repo = tmp_path / "fixture"
    repo.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()

    git("init", "-q")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    (repo / "product.py").write_text("value = 1\n")
    git("add", ".")
    git("commit", "-qm", "base")
    common = git("rev-parse", "HEAD")
    (repo / "product.py").write_text("value = 2\n")
    git("commit", "-qam", "head")
    head, tree = git("rev-parse", "HEAD"), git("rev-parse", "HEAD^{tree}")
    git("update-ref", "refs/pull/2/head", head)
    git("checkout", "--detach", common)
    (repo / "product.py").write_text("value = 3\n")
    (repo / "base_only.py").write_text("base = True\n")
    git("add", ".")
    git("commit", "-qm", "new base")
    base = git("rev-parse", "HEAD")
    candidate = Candidate(
        repository="owner/repo",
        repository_id=1,
        head_repository="owner/repo",
        head_repository_id=1,
        head_ref="feature",
        pr_id=100,
        pr_number=2,
        head_sha=head,
        base_sha=base,
        tree_sha=tree,
    )

    def local_fetch(args, **kwargs):
        return command(
            [str(repo) if x == "https://github.com/owner/repo.git" else x for x in args], **kwargs
        )

    monkeypatch.setattr(source, "command", local_fetch)
    checkout, original = source.snapshot(candidate, tmp_path / "candidate")
    assert "base_only.py" not in (tmp_path / "candidate/diff.txt").read_text()
    assert "value = 2" in (tmp_path / "candidate/diff.txt").read_text()
    assert source.prepare_conflict_tree(candidate, tmp_path / "candidate", checkout)
    assert (checkout / "base_only.py").read_text() == "base = True\n"
    with pytest.raises(Blocked, match="UNRESOLVED_CONFLICT_MARKERS"):
        source.require_resolved_conflicts(source.changes(checkout, original))
    (checkout / "product.py").write_text("value = 4\n")
    patch = source.changes(checkout, original)
    source.require_resolved_conflicts(patch)
    assert {p["path"] for p in patch} == {"product.py", "base_only.py"}
