#!/usr/bin/env python3
"""Deterministic, fail-closed GitHub merge gate. No model or PR code execution."""

from __future__ import annotations

import argparse
import fnmatch
import io
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import time
from urllib.parse import quote
import zipfile

SCHEMA = "skills-review/v1"
ADMISSION_SCHEMA = "dependabot-admission/v1"
SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
BOT_ID = 49699333  # github.com Dependabot; never trust a display name alone.
ACTIONS_BOT_ID = 41898282  # github-actions[bot] on github.com.
CI_CONTEXT = "Required CI"
INTENT_SCHEMA = "merge-intent/v1"
LIMIT = 2 * 1024 * 1024


class Blocked(Exception):
    """Safe diagnostic, never raw subprocess output."""


class Pending(Blocked):
    """A later authoritative observation may allow continuation."""


def require(condition, message):
    if not condition:
        raise Blocked(message)


def gh(args, payload=None, binary=False):
    # Never log raw CLI errors or tokens, or buffer an unbounded API response.
    with subprocess.Popen(
        ["gh", *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ) as process:
        try:
            if payload is not None:
                encoded = json.dumps(payload).encode()
                require(
                    len(encoded) < 32768, "Request exceeds the bounded request limit"
                )
                process.stdin.write(encoded)
            process.stdin.close()
            chunks, size = [], 0
            deadline = time.monotonic() + 45
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    require(
                        remaining > 0,
                        "GitHub operation timed out; reconcile remote state",
                    )
                    if not selector.select(remaining):
                        raise Blocked(
                            "GitHub operation timed out; reconcile remote state"
                        )
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    size += len(chunk)
                    require(
                        size <= LIMIT,
                        "GitHub response exceeds the bounded response limit",
                    )
                    chunks.append(chunk)
            require(
                process.wait(timeout=max(0.1, deadline - time.monotonic())) == 0,
                "GitHub operation failed; reconcile remote state before retry",
            )
            output = b"".join(chunks)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    if binary:
        return output
    return json.loads(output) if output.strip() else {}


class GitHub:
    def __init__(self, repo):
        require(
            bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)),
            "Invalid repository",
        )
        self.repo = repo
        self.prefix = f"repos/{repo}"

    def api(self, path, method="GET", data=None):
        args = [
            "api",
            "--hostname",
            "github.com",
            "--method",
            method,
            "-H",
            "X-GitHub-Api-Version: 2026-03-10",
            f"{self.prefix}/{path}",
        ]
        if data is not None:
            args += ["--input", "-"]
        return gh(args, data)

    def pages(self, path, key=None):
        items = []
        for page in range(1, 101):
            sep = "&" if "?" in path else "?"
            response = self.api(f"{path}{sep}per_page=100&page={page}")
            rows = response[key] if key else response
            require(isinstance(rows, list), "Invalid paginated response")
            items.extend(rows)
            if len(rows) < 100:
                return items
        raise Blocked("Pagination limit reached; evidence is incomplete")

    def repo_info(self):
        return gh(["api", "--hostname", "github.com", self.prefix])

    def threads(self, number):
        owner, name = self.repo.split("/")
        cursor = None
        query = """query($owner:String!,$name:String!,$number:Int!,$cursor:String){
          repository(owner:$owner,name:$name){pullRequest(number:$number){
            reviewThreads(first:100,after:$cursor){nodes{isResolved}
              pageInfo{hasNextPage endCursor}}}}}"""
        for _ in range(100):
            value = gh(
                ["api", "--hostname", "github.com", "graphql", "--input", "-"],
                {
                    "query": query,
                    "variables": {
                        "owner": owner,
                        "name": name,
                        "number": number,
                        "cursor": cursor,
                    },
                },
            )
            require(not value.get("errors"), "Unable to verify review threads")
            threads = value["data"]["repository"]["pullRequest"]["reviewThreads"]
            require(
                all(t["isResolved"] for t in threads["nodes"]),
                "Unresolved review threads",
            )
            if not threads["pageInfo"]["hasNextPage"]:
                return
            cursor = threads["pageInfo"]["endCursor"]
        raise Blocked("Review thread pagination incomplete")

    def artifact(self, artifact_id, filename="admission.json"):
        raw = gh(
            [
                "api",
                "--hostname",
                "github.com",
                f"{self.prefix}/actions/artifacts/{int(artifact_id)}/zip",
            ],
            binary=True,
        )
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            require(
                len(entries) == 1
                and entries[0].filename == filename
                and entries[0].file_size <= 65536,
                "Invalid admission archive",
            )
            return json.loads(archive.read(entries[0]))


def operator_ids():
    try:
        ids = json.loads(os.environ.get("MERGE_OPERATOR_IDS", "[]"))
        require(
            isinstance(ids, list)
            and ids
            and all(type(v) is int and v > 0 for v in ids),
            "Configure MERGE_OPERATOR_IDS as a nonempty JSON list of numeric user IDs",
        )
        return ids
    except ValueError as exc:
        raise Blocked("Invalid operator configuration") from exc


def identity(pr, repo, head, default):
    require(pr["state"] == "open" and not pr["draft"], "PR is closed or draft")
    require(
        pr["head"]["repo"] and pr["head"]["repo"]["full_name"] == repo,
        "Automatic merge requires a same-repository PR",
    )
    require(
        pr["base"]["repo"]["full_name"] == repo and pr["base"]["ref"] == default,
        "PR must target the live repository default branch",
    )
    require(SHA.fullmatch(head) and pr["head"]["sha"] == head, "PR head drift")


def review_record(review, pr, allowed):
    require(
        review["user"]["id"] in allowed and review["state"] == "COMMENTED",
        "Review attestation is not from an allowed operator",
    )
    require(review["commit_id"] == pr["head"]["sha"], "Review commit drift")
    try:
        value = json.loads(review["body"])
    except ValueError as exc:
        raise Blocked("Review body is not a structured attestation") from exc
    require(
        value.get("schema") == SCHEMA
        and value.get("verdict") == "passed"
        and value.get("unresolved_findings") == 0,
        "Review did not pass",
    )
    require(
        value.get("repository") == pr["base"]["repo"]["full_name"]
        and value.get("pr") == pr["number"]
        and value.get("head") == pr["head"]["sha"]
        and value.get("base") == pr["base"]["ref"]
        and value.get("base_sha") == pr["base"]["sha"],
        "Review identity or base drift",
    )
    require(
        isinstance(value.get("validation"), list)
        and value["validation"]
        and all(isinstance(v, str) and 0 < len(v) <= 1000 for v in value["validation"]),
        "Review lacks validation evidence",
    )
    return value


def dependency_policy(value, files):
    require(
        value.get("schema") == ADMISSION_SCHEMA, "Invalid dependency admission schema"
    )
    require(
        value.get("update_type")
        in {
            "version-update:semver-major",
            "version-update:semver-minor",
            "version-update:semver-patch",
        },
        "Dependency update type is not eligible",
    )
    ecosystem = value.get("ecosystem")
    require(
        ecosystem in {"github_actions", "pip", "uv"},
        "Dependency ecosystem is not eligible",
    )
    require(bool(files), "Empty dependency change")
    for item in files:
        path = item["filename"]
        require(
            item["status"] != "renamed", "Renamed dependency files require local review"
        )
        if ecosystem == "github_actions":
            allowed = re.fullmatch(
                r"\.github/workflows/[^/]+\.ya?ml|\.github/actions/.+/action\.ya?ml",
                path,
            )
        else:
            allowed = re.search(
                r"(^|/)(pyproject\.toml|uv\.lock|poetry\.lock|pdm\.lock|Pipfile|Pipfile\.lock|(requirements|constraints)([-._/][^/]+)?\.txt)$",
                path,
            )
        require(bool(allowed), "Dependency change contains an ineligible file")


def dependabot_admission(api, pr, policy, files):
    require(
        pr["user"]["id"] == BOT_ID and pr["user"]["login"] == "dependabot[bot]",
        "PR is not authenticated Dependabot work",
    )
    commits = api.pages(f"pulls/{pr['number']}/commits")
    require(
        len(commits) == pr["commits"] and commits, "Incomplete dependency commit list"
    )
    for commit in commits:
        require(
            (commit.get("author") or {}).get("id") == BOT_ID
            and commit["commit"]["verification"]["verified"],
            "Dependency branch contains unverified or human-authored commits",
        )
    producer = policy["dependabot_workflow"]
    name = f"dependabot-{pr['number']}-{pr['head']['sha']}"
    artifacts = api.pages(f"actions/artifacts?name={quote(name, safe='')}", "artifacts")
    for artifact in sorted(artifacts, key=lambda item: item["id"], reverse=True):
        if artifact["name"] != name or artifact["expired"]:
            continue
        run = api.api(f"actions/runs/{artifact['workflow_run']['id']}")
        require(
            run["path"] == f".github/workflows/{producer}"
            and run["event"] == "pull_request_target"
            and run["repository"]["full_name"] == api.repo,
            "Wrong admission producer",
        )
        if run["status"] != "completed" or run["conclusion"] != "success":
            continue
        require(run["actor"]["id"] == BOT_ID, "Untrusted admission actor")
        # pull_request_target run.head_sha is the BASE commit, not the PR head.
        value = api.artifact(artifact["id"])
        require(
            value.get("repository") == api.repo
            and value.get("pr") == pr["number"]
            and value.get("head") == pr["head"]["sha"]
            and value.get("base") == pr["base"]["ref"]
            and value.get("base_sha") == run["head_sha"]
            and value.get("run_id") == run["id"],
            "Dependency artifact identity mismatch",
        )
        comparison = api.api(f"compare/{run['head_sha']}...{pr['base']['sha']}")
        require(
            comparison["status"] in {"ahead", "identical"},
            "Untrusted producer revision",
        )
        dependency_policy(value, files)
        return
    raise Pending("No current verified Dependabot admission artifact")


def no_objections(reviews):
    latest = {}
    for review in sorted(reviews, key=lambda r: (r.get("submitted_at") or "", r["id"])):
        if review["state"] in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            latest[review["user"]["id"]] = review["state"]
    require("CHANGES_REQUESTED" not in latest.values(), "Outstanding requested changes")


def applicable(patterns, paths):
    return any(
        fnmatch.fnmatchcase(path, pattern) for path in paths for pattern in patterns
    )


def changed_paths(files):
    return [
        path
        for item in files
        for path in (item["filename"], item.get("previous_filename"))
        if path
    ]


def changes_workflows(files):
    return any(path.startswith(".github/workflows/") for path in changed_paths(files))


def run_matches_pr(api, run, pr):
    attached = run.get("pull_requests", [])
    if attached:
        return any(p["number"] == pr["number"] for p in attached)
    # GitHub omits this association for fork PRs. Bind the authoritative
    # fork/ref/head to one open PR and the repository-owned Actions check suite.
    if (
        pr["head"]["repo"]["full_name"] == api.repo
        or run.get("head_branch") != pr["head"]["ref"]
        or run.get("repository", {}).get("full_name") != api.repo
        or not run.get("check_suite_id")
        or run.get("created_at", "") < pr["created_at"]
    ):
        return False
    owner = pr["head"]["repo"]["full_name"].split("/")[0]
    selector = quote(f"{owner}:{pr['head']['ref']}", safe="")
    opened = api.pages(f"pulls?state=open&head={selector}")
    if [p["number"] for p in opened] != [pr["number"]]:
        return False
    suite = api.api(f"check-suites/{int(run['check_suite_id'])}")
    return (
        suite["app"]["id"] == 15368
        and suite["repository"]["full_name"] == api.repo
        and suite["head_sha"] == pr["head"]["sha"]
        and suite["head_branch"] == pr["head"]["ref"]
    )


def checks_ready(api, pr, files, policy):
    sha = pr["head"]["sha"]
    paths = changed_paths(files)
    # Explicit trusted workflow inventory prevents an empty rollup being a pass.
    expected = [w for w in policy["ci_workflows"] if applicable(w["paths"], paths)]
    require(expected, "No applicable CI policy; configure verification before merging")
    runs = api.pages(f"actions/runs?head_sha={sha}", "workflow_runs")
    for workflow in expected:
        matching = [
            r
            for r in runs
            if r["path"] == f".github/workflows/{workflow['file']}"
            and r["event"] == "pull_request"
            and r["head_sha"] == sha
            and r.get("head_repository", {}).get("full_name")
            == pr["head"]["repo"]["full_name"]
            and run_matches_pr(api, r, pr)
        ]
        if not matching:
            raise Pending("Expected PR workflow is missing")
        run = max(matching, key=lambda r: (r["run_number"], r["run_attempt"]))
        if run["status"] != "completed":
            raise Pending("Expected CI is pending")
        require(run["conclusion"] == "success", "Expected CI did not pass")
    checks = api.pages(f"commits/{sha}/check-runs?filter=latest", "check_runs")
    for check in checks:
        if check["name"] in policy.get("excluded_checks", []):
            continue
        if check["status"] != "completed":
            raise Pending("PR check is pending")
        require(
            check["conclusion"] in {"success", "neutral", "skipped"}, "PR check failed"
        )
    statuses = api.pages(f"commits/{sha}/statuses")
    latest = {}
    for status in sorted(statuses, key=lambda s: s["id"], reverse=True):
        latest.setdefault(status["context"], status)
    for status in latest.values():
        if status["context"] == CI_CONTEXT:
            continue
        if status["state"] == "pending":
            raise Pending("Commit status is pending")
        require(status["state"] == "success", "Commit status failed")


def snapshot(api, number, head, policy, review_id=0):
    default = api.repo_info()["default_branch"]
    pr = api.api(f"pulls/{number}")
    identity(pr, api.repo, head, default)
    comparison = api.api(f"compare/{pr['base']['sha']}...{head}")
    require(
        comparison["status"] in {"ahead", "identical"},
        "Current default branch is not included in the PR; synchronize and review again",
    )
    files = api.pages(f"pulls/{number}/files")
    require(len(files) == pr["changed_files"], "Incomplete changed-file listing")
    if review_id:
        review_record(
            api.api(f"pulls/{number}/reviews/{review_id}"), pr, operator_ids()
        )
    else:
        dependabot_admission(api, pr, policy, files)
    no_objections(api.pages(f"pulls/{number}/reviews"))
    api.threads(number)
    checks_ready(api, pr, files, policy)
    if pr["mergeable"] is None:
        raise Pending("Mergeability is not yet established")
    require(
        pr["mergeable"] is True
        and pr["mergeable_state"] not in {"dirty", "behind", "draft"},
        "PR is conflicted or requires base synchronization",
    )
    return pr, files


def trusted_runtime(api):
    default = api.repo_info()["default_branch"]
    require(
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("GITHUB_SERVER_URL") == "https://github.com"
        and os.environ.get("GITHUB_REPOSITORY") == api.repo
        and os.environ.get("GITHUB_REF") == f"refs/heads/{default}",
        "Merge effects run only in the trusted default-branch workflow",
    )
    return default


def activation(number):
    mode = os.environ.get("MERGE_AUTOMATION_MODE") or "disabled"
    require(mode in {"disabled", "canary", "enabled"}, "Invalid activation mode")
    if mode == "canary":
        allowed = json.loads(os.environ.get("MERGE_CANARY_PR_NUMBERS", "[]"))
        require(
            isinstance(allowed, list)
            and all(type(n) is int and n > 0 for n in allowed),
            "Invalid canary PR list",
        )
        require(number in allowed, "PR is not an enabled canary")
    else:
        require(mode == "enabled", "Merge automation is disabled")


def admitted_intent(api, number, head, review_id, method, policy):
    trusted_runtime(api)
    activation(number)
    event = os.environ.get("GITHUB_EVENT_NAME")
    require(
        event in {"workflow_dispatch", "workflow_run", "schedule"},
        "Unsupported merge event",
    )
    if event == "workflow_dispatch":
        require(
            int(os.environ.get("GITHUB_ACTOR_ID", "0")) in operator_ids(),
            "Unauthorized dispatcher",
        )
        require(review_id > 0, "Operator dispatch requires local review evidence")
    else:
        require(review_id == 0, "Automatic events accept Dependabot admission only")
    pr, files = snapshot(api, number, head, policy, review_id)
    require(
        pr["user"]["id"] != ACTIONS_BOT_ID,
        "Actions cannot approve its own PR; maintainer required",
    )
    return {
        "schema": INTENT_SCHEMA,
        "repository": api.repo,
        "pr": number,
        "head": head,
        "base": pr["base"]["ref"],
        "base_sha": pr["base"]["sha"],
        "review_id": review_id,
        "method": method,
        "run_id": int(os.environ["GITHUB_RUN_ID"]),
        "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
        "source_sha": os.environ["GITHUB_SHA"],
        "ci_workflows": [
            w["file"]
            for w in policy["ci_workflows"]
            if applicable(w["paths"], changed_paths(files))
        ],
    }


def execute(api, number, head, review_id, method, policy, intent=None):
    default = trusted_runtime(api)
    activation(number)
    current = api.api(f"pulls/{number}")
    if current.get("merged"):
        require(
            current["head"]["sha"] == head
            and current["base"]["repo"]["full_name"] == api.repo
            and current["base"]["ref"] == default,
            "Merged PR identity drift",
        )
        return {
            "outcome": "merged",
            "result_sha": current["merge_commit_sha"],
            "mutated": False,
        }
    expected = admitted_intent(api, number, head, review_id, method, policy)
    if intent is not None:
        require(intent == expected, "Stale pre-effect admission receipt")
    name = f"merge-intent-{number}-{head}-{expected['run_attempt']}"
    artifacts = [
        a
        for a in api.pages(f"actions/runs/{expected['run_id']}/artifacts", "artifacts")
        if a["name"] == name
    ]
    if not artifacts:
        raise Pending("Admission receipt is not available; no effects")
    require(len(artifacts) == 1, "Ambiguous admission receipt")
    artifact_id = artifacts[0]["id"]
    artifact = api.api(f"actions/artifacts/{artifact_id}")
    require(
        not artifact["expired"]
        and artifact["workflow_run"]["id"] == expected["run_id"]
        and artifact["name"]
        == f"merge-intent-{number}-{head}-{expected['run_attempt']}",
        "Untrusted admission receipt artifact",
    )
    require(
        api.artifact(artifact_id, "intent.json") == expected,
        "Uploaded admission receipt differs",
    )
    statuses = [
        s for s in api.pages(f"commits/{head}/statuses") if s["context"] == CI_CONTEXT
    ]
    if not statuses or max(statuses, key=lambda s: s["id"])["state"] != "success":
        raise Pending("Required CI aggregate is not successful")
    rules = api.pages(f"rules/branches/{quote(default, safe='')}")
    queue = any(r["type"] == "merge_queue" for r in rules)
    require(not queue, "GITHUB_TOKEN cannot enqueue merge queues; maintainer required")
    before = api.api(f"pulls/{number}")
    require(
        before["head"]["sha"] == head and before["base"]["sha"] == expected["base_sha"],
        "PR changed before approval",
    )
    approvals = api.pages(f"pulls/{number}/reviews")
    if not any(
        r["state"] == "APPROVED"
        and r["commit_id"] == head
        and r["user"]["id"] == ACTIONS_BOT_ID
        for r in approvals
    ):
        api.api(
            f"pulls/{number}/reviews",
            "POST",
            {
                "commit_id": head,
                "event": "APPROVE",
                "body": f"Verified {'local review ' + str(review_id) if review_id else 'Dependabot policy'} for {head}.",
            },
        )
    # Repeat admission/CI/objections after approval. A new push cannot inherit it.
    refreshed, _ = snapshot(api, number, head, policy, review_id)
    require(
        refreshed["base"]["sha"] == expected["base_sha"],
        "Base advanced; refresh review and checks",
    )
    payload = {
        "sha": head,
        "merge_action": "direct_merge",
        "bypass_rules": False,
        "merge_method": method,
    }
    try:
        result = api.api(f"pulls/{number}/merge-async", "PUT", payload)
    except Blocked as exc:
        raise Blocked(
            "Protected merge failed or is uncertain; reconcile the PR before retry. "
            "A token/protection denial, including unsupported workflow-file merges, "
            "requires a maintainer; no credential or bypass fallback"
        ) from exc
    return {
        "outcome": "requested",
        "head": head,
        "request": result,
        "note": "Request accepted is not merge completion; verify authoritative PR state",
    }


def candidates(api, event):
    default = api.repo_info()["default_branch"]
    if os.environ.get("GITHUB_EVENT_NAME") == "pull_request_target":
        return []
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        require(
            int(os.environ.get("GITHUB_ACTOR_ID", "0")) in operator_ids(),
            "Unauthorized dispatcher",
        )
        inputs = event["inputs"]
        number, review_id = int(inputs["pr"]), int(inputs["review_id"])
        head, method = inputs["head"], inputs.get("method", "squash")
        require(
            number > 0 and review_id > 0 and SHA.fullmatch(head),
            "Invalid dispatch identity",
        )
        require(method in {"squash", "merge", "rebase"}, "Invalid merge method")
        identity(api.api(f"pulls/{number}"), api.repo, head, default)
        activation(number)
        return [{"pr": number, "head": head, "review_id": review_id, "method": method}]
    targets = [
        {
            "pr": p["number"],
            "head": p["head"]["sha"],
            "review_id": 0,
            "method": "squash",
        }
        for p in api.pages(f"pulls?state=open&base={quote(default, safe='')}")
        if p["user"]["id"] == BOT_ID
        and not p["draft"]
        and p["head"]["repo"]
        and p["head"]["repo"]["full_name"] == api.repo
    ]
    selected = []
    for target in targets:
        try:
            activation(target["pr"])
        except Blocked:
            continue
        selected.append(target)
    return selected


def ci_targets(api):
    default = api.repo_info()["default_branch"]
    targets = [
        {"pr": p["number"], "head": p["head"]["sha"]}
        for p in api.pages(f"pulls?state=open&base={quote(default, safe='')}")
        if p["head"]["repo"]
    ]
    require(len(targets) <= 200, "Too many open PRs for one CI reconciliation")
    return targets


def publish_ci(api, number, head, policy):
    trusted_runtime(api)
    pr = api.api(f"pulls/{number}")
    require(
        pr["state"] == "open"
        and pr["head"]["sha"] == head
        and pr["base"]["repo"]["full_name"] == api.repo
        and pr["base"]["ref"] == api.repo_info()["default_branch"],
        "CI target drift",
    )
    state, reason = "success", "All applicable CI passed"
    try:
        files = api.pages(f"pulls/{number}/files")
        require(len(files) == pr["changed_files"], "Incomplete changed-file listing")
        checks_ready(api, pr, files, policy)
    except Pending as exc:
        state, reason = "pending", str(exc)
    except Blocked as exc:
        state, reason = "failure", str(exc)
    data = {
        "state": state,
        "context": CI_CONTEXT,
        "description": reason[:140],
        "target_url": f"https://github.com/{api.repo}/actions/runs/{int(os.environ['GITHUB_RUN_ID'])}",
    }
    existing = [
        s for s in api.pages(f"commits/{head}/statuses") if s["context"] == CI_CONTEXT
    ]
    if not existing or max(existing, key=lambda s: s["id"])["state"] != state:
        api.api(f"statuses/{head}", "POST", data)
    return {"state": state, "reason": reason}


def attest(api, number, evidence):
    pr = api.api(f"pulls/{number}")
    identity(pr, api.repo, evidence["head"], api.repo_info()["default_branch"])
    user = gh(["api", "--hostname", "github.com", "user"])
    # Reuse the server-side validator before posting. Broker verifies the allowlist.
    review_record(
        {
            "user": user,
            "state": "COMMENTED",
            "commit_id": evidence["head"],
            "body": json.dumps(evidence),
        },
        pr,
        [user["id"]],
    )
    return api.api(
        f"pulls/{number}/reviews",
        "POST",
        {
            "commit_id": evidence["head"],
            "event": "COMMENT",
            "body": json.dumps(evidence, sort_keys=True),
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "candidates",
            "ci-targets",
            "publish-ci",
            "intent",
            "check",
            "execute",
            "attest",
        ],
    )
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--policy", default=".github/merge-policy.json")
    parser.add_argument("--pr", type=int)
    parser.add_argument("--head")
    parser.add_argument("--review-id", type=int, default=0)
    parser.add_argument(
        "--method", choices=["squash", "merge", "rebase"], default="squash"
    )
    parser.add_argument("--evidence")
    parser.add_argument("--intent")
    args = parser.parse_args()
    try:
        api = GitHub(args.repo or "")
        if args.action == "attest":
            result = attest(api, args.pr, json.loads(Path(args.evidence).read_text()))
            print(json.dumps({"review_id": result["id"], "url": result["html_url"]}))
            return 0
        policy = json.loads(Path(args.policy).read_text())
        require(policy["schema"] == "merge-policy/v1", "Unsupported merge policy")
        if args.action == "ci-targets":
            result = ci_targets(api)
        elif args.action == "publish-ci":
            result = publish_ci(api, args.pr, args.head, policy)
        elif args.action == "intent":
            result = admitted_intent(
                api, args.pr, args.head, args.review_id, args.method, policy
            )
        elif args.action == "candidates":
            event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
            result = candidates(api, event)
        elif args.action == "check":
            _, files = snapshot(api, args.pr, args.head, policy, args.review_id)
            result = {
                "ready": True,
                "workflows": changes_workflows(files),
            }
        else:
            result = execute(
                api,
                args.pr,
                args.head,
                args.review_id,
                args.method,
                policy,
                json.loads(Path(args.intent).read_text()) if args.intent else None,
            )
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except Pending as exc:
        print(json.dumps({"outcome": "pending", "reason": str(exc)}))
        return 2
    except (
        Blocked,
        KeyError,
        ValueError,
        TypeError,
        OSError,
        subprocess.TimeoutExpired,
        zipfile.BadZipFile,
    ) as exc:
        message = (
            str(exc)
            if isinstance(exc, Blocked)
            else "Invalid or unavailable evidence; no further effects permitted"
        )
        print(json.dumps({"outcome": "blocked", "reason": message}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
