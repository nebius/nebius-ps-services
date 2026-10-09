"""Private release checkpoints and read-only GitHub observation; never grants authority."""

from __future__ import annotations

import argparse
import contextvars
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

from release_checkpoint import SCHEMA, SHA, ReleaseError, Store, state_home
from release_reconcile import reconcile_pr

PHASE_SECONDS = 10800
POLL_SECONDS = 15
REMOTE_DEADLINE = contextvars.ContextVar("release_remote_deadline", default=None)


class WaitExpired(ReleaseError):
    """The current observation exhausted its bounded wait budget."""


def command(
    args: list[str], cwd: Path, accepted: tuple[int, ...] = (0,), *, strip: bool = True
) -> str:
    deadline = REMOTE_DEADLINE.get()
    remaining = 45 if deadline is None else min(45, deadline - time.monotonic())
    if remaining <= 0:
        raise WaitExpired("Release observation deadline reached")
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=remaining,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        if deadline is not None and time.monotonic() >= deadline:
            raise WaitExpired("Release observation deadline reached") from exc
        raise ReleaseError(
            f"{args[0]} unavailable or timed out; progress retained"
        ) from exc
    if result.returncode not in accepted:
        raise ReleaseError(
            f"{args[0]} observation failed; inspect authentication and target access"
        )
    return result.stdout.strip() if strip else result.stdout


def git(cwd: Path, *args: str) -> str:
    return command(["git", *args], cwd)


def remote_target(remote: str) -> tuple[str, str]:
    if re.fullmatch(r"git@[^/:]+:[^/]+/[^/]+", remote):
        host, repo = remote[4:].split(":", 1)
    else:
        parsed = urlsplit(remote)
        if (
            parsed.scheme not in {"https", "ssh"}
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ReleaseError(
                "Origin must be a credential-free GitHub HTTPS or SSH remote"
            )
        if parsed.username and not (
            parsed.scheme == "ssh" and parsed.username == "git"
        ):
            raise ReleaseError("Do not embed credentials in origin")
        host, repo = parsed.hostname or "", parsed.path.lstrip("/")
        if parsed.port:
            raise ReleaseError("Custom remote ports are not supported")
    repo = repo.removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9.-]+", host) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo
    ):
        raise ReleaseError("Cannot resolve an unambiguous GitHub origin")
    return host, repo


def identity(project: Path) -> dict:
    forbidden = {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CONFIG_COUNT",
    }
    if forbidden.intersection(os.environ):
        raise ReleaseError("Repository-shaping Git environment is not supported")
    project = project.resolve(strict=True)
    root = Path(git(project, "rev-parse", "--show-toplevel")).resolve()
    fetch = git(root, "remote", "get-url", "--all", "origin").splitlines()
    push = git(root, "remote", "get-url", "--push", "--all", "origin").splitlines()
    if (
        len(fetch) != 1
        or len(push) != 1
        or remote_target(fetch[0]) != remote_target(push[0])
    ):
        raise ReleaseError("Origin fetch/push targets must identify one repository")
    host, repo = remote_target(fetch[0])
    return {
        "root": str(root),
        "project": str(project.relative_to(root)),
        "host": host,
        "repo": repo,
        "origin_digest": hashlib.sha256(json.dumps([fetch, push]).encode()).hexdigest(),
    }


class GitHub:
    def __init__(self, scope: dict):
        self.scope = scope
        self.cwd = Path(scope["root"])
        self.target = f"{scope['host']}/{scope['repo']}"

    def api(self, endpoint: str) -> object:
        route = f"repos/{self.scope['repo']}"
        if endpoint:
            route += f"/{endpoint}"
        raw = command(
            [
                "gh",
                "api",
                "--hostname",
                self.scope["host"],
                route,
            ],
            self.cwd,
        )
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise ReleaseError("GitHub returned malformed evidence") from exc

    def pr(self, number: int) -> dict:
        raw = command(
            [
                "gh",
                "pr",
                "view",
                str(number),
                "--repo",
                self.target,
                "--json",
                "number,state,isDraft,headRefName,headRefOid,baseRefName,headRepository,"
                "headRepositoryOwner,reviewDecision,mergeStateStatus,mergeable,mergeCommit,statusCheckRollup,url",
            ],
            self.cwd,
        )
        return json.loads(raw)

    def queue(self, number: int) -> dict:
        owner, repo = self.scope["repo"].split("/")
        query = """query($owner:String!,$repo:String!,$number:Int!){
          repository(owner:$owner,name:$repo){pullRequest(number:$number){
            number state mergeCommit{oid} headRefOid baseRefName isInMergeQueue
            mergeQueueEntry{id enqueuedAt state position pullRequest{number}}
          }}}"""
        raw = command(
            [
                "gh",
                "api",
                "graphql",
                "--hostname",
                self.scope["host"],
                "-f",
                f"query={query}",
                "-f",
                f"owner={owner}",
                "-f",
                f"repo={repo}",
                "-F",
                f"number={number}",
            ],
            self.cwd,
        )
        return json.loads(raw)["data"]["repository"]["pullRequest"]

    def tag(self, tag: str) -> tuple[str | None, str | None]:
        lines = git(
            self.cwd,
            "ls-remote",
            "--tags",
            "origin",
            f"refs/tags/{tag}",
            f"refs/tags/{tag}^{{}}",
        )
        refs = dict(reversed(line.split()) for line in lines.splitlines())
        return refs.get(f"refs/tags/{tag}"), refs.get(f"refs/tags/{tag}^{{}}")


def check_pr(value: dict, pr: dict) -> None:
    repo = f"{pr['headRepositoryOwner']['login']}/{pr['headRepository']['name']}"
    if (
        pr["number"] != value["pr"]
        or repo.lower() != value["scope"]["repo"].lower()
        or pr["headRefName"] != value["branch"]
        or pr["headRefOid"] != value["head"]
        or pr["baseRefName"] != value["base"]
    ):
        raise ReleaseError(
            "PR identity drift; reconcile the changed head/base before continuing"
        )


def check_results(checks: list[dict]) -> str:
    pending = False
    for item in checks:
        status = item.get("status", item.get("state", "")).upper()
        conclusion = (item.get("conclusion") or "").upper()
        if status in {"ERROR", "FAILURE"} or conclusion in {
            "FAILURE",
            "CANCELLED",
            "TIMED_OUT",
            "ACTION_REQUIRED",
            "STARTUP_FAILURE",
            "STALE",
        }:
            return "failed"
        if status not in {"COMPLETED", "SUCCESS"} or (
            status == "COMPLETED"
            and conclusion not in {"SUCCESS", "SKIPPED", "NEUTRAL"}
        ):
            pending = True
    return "pending" if pending else "settled"


def merged_observation(value: dict, pr: dict, github: GitHub) -> dict:
    merged = (pr.get("mergeCommit") or {}).get("oid")
    if not merged or not SHA.fullmatch(merged) or value["merge"] not in (None, merged):
        raise ReleaseError("Missing or conflicting merged-result SHA")
    comparison = github.api(f"compare/{merged}...{quote(value['base'], safe='')}")
    if comparison.get("status") not in {"ahead", "identical"}:
        raise ReleaseError("Merged commit is not proven in remote default history")
    value["merge"] = merged
    return {
        "status": "ready",
        "next": "merge-pr must verify exact-result CI",
        "commit": merged,
    }


def observation(value: dict, github: GitHub, phase: str) -> dict:
    if not value["pr"]:
        raise ReleaseError("Bind the pushed PR before observing release progress")
    pr = github.pr(value["pr"])
    check_pr(value, pr)
    if pr["state"] == "CLOSED":
        return {"status": "blocked", "reason": "PR closed without merging"}
    if phase in {"pr", "merge"}:
        if pr["state"] == "MERGED":
            return merged_observation(value, pr, github)
        if (
            pr["isDraft"]
            or pr["reviewDecision"] == "CHANGES_REQUESTED"
            or pr["mergeable"] == "CONFLICTING"
        ):
            return {
                "status": "blocked",
                "reason": "Draft, requested changes, or merge conflict",
            }
        checks = check_results(pr["statusCheckRollup"])
        if checks == "failed":
            return {"status": "blocked", "reason": "PR checks failed"}
        if pr["reviewDecision"] == "REVIEW_REQUIRED":
            return {
                "status": "waiting",
                "gate": f"approval:pr:{value['pr']}",
                "url": pr["url"],
            }
        if checks == "pending":
            return {"status": "waiting", "gate": f"checks:{phase}"}
        if phase == "merge":
            queue = github.queue(value["pr"])
            if (
                queue["number"] != value["pr"]
                or queue["headRefOid"] != value["head"]
                or queue["baseRefName"] != value["base"]
            ):
                raise ReleaseError("Merge queue identity drift")
            if queue["state"] == "MERGED":
                return merged_observation(value, queue, github)
            if queue["state"] != "OPEN":
                return {
                    "status": "blocked",
                    "reason": "Queued PR closed without merging",
                }
            entry = queue.get("mergeQueueEntry")
            if queue["isInMergeQueue"] is False and entry is None:
                if value.get("observed_queue_entry"):
                    return {
                        "status": "blocked",
                        "reason": "PR is no longer in the merge queue; do not re-enqueue",
                    }
                return {"status": "waiting", "gate": "merge_settlement"}
            if (
                queue["isInMergeQueue"] is not True
                or not entry
                or entry["pullRequest"]["number"] != value["pr"]
                or not entry.get("id")
                or not entry.get("enqueuedAt")
            ):
                raise ReleaseError("Merge queue membership is unverified")
            value["observed_queue_entry"] = entry["id"]
            return {"status": "waiting", "gate": "merge_queue"}
        return {
            "status": "ready",
            "next": "merge-pr must refresh required checks and merge readiness",
        }
    if (
        not value["merge"]
        or pr["state"] != "MERGED"
        or pr["mergeCommit"]["oid"] != value["merge"]
    ):
        raise ReleaseError("Release requires the frozen, confirmed merged commit")
    tag_object, commit = github.tag(value["tag"])
    if (
        not value["tag_object"]
        or tag_object != value["tag_object"]
        or commit != value["merge"]
    ):
        raise ReleaseError(
            "Remote annotated tag differs from the recorded object/commit"
        )
    runs = github.api(
        f"actions/workflows/{value['workflow']}/runs?event=push&head_sha={commit}&per_page=100"
    )["workflow_runs"]
    if len(runs) >= 100:
        raise ReleaseError(
            "Workflow listing may be truncated; inspect the frozen run explicitly"
        )
    matches = [
        r
        for r in runs
        if r["head_sha"] == commit
        and r["head_branch"] == value["tag"]
        and r["event"] == "push"
    ]
    if value["run"]:
        matches = [r for r in matches if r["id"] == value["run"]]
    if not matches:
        return {"status": "waiting", "gate": "release_workflow"}
    if len(matches) != 1:
        raise ReleaseError("Ambiguous matching release workflow runs")
    run = matches[0]
    value["run"] = run["id"]
    deployments = github.api(f"actions/runs/{run['id']}/pending_deployments")
    if deployments:
        return {
            "status": "waiting",
            "gate": f"approval:environment:{run['id']}",
            "url": run["html_url"],
        }
    if run["status"] != "completed":
        return {"status": "waiting", "gate": "release_workflow", "url": run["html_url"]}
    if run["conclusion"] != "success":
        return {
            "status": "blocked",
            "reason": "Release workflow failed; inspect the existing run",
            "url": run["html_url"],
        }
    release = github.api(f"releases/tags/{value['tag']}")
    assets = github.api(f"releases/{release['id']}/assets?per_page=100")
    if len(assets) >= 100:
        raise ReleaseError(
            "Asset listing may be truncated; independent verification required"
        )
    if release["draft"] or release["tag_name"] != value["tag"]:
        raise ReleaseError("Release is draft or has a different tag")
    expected = value["assets"]
    if any(
        not any(
            fnmatch.fnmatchcase(a["name"], pattern) and a["size"] > 0 for a in assets
        )
        for pattern in expected
    ):
        return {
            "status": "blocked",
            "reason": "Published release is missing expected assets",
        }
    return {
        "status": "ready",
        "next": "download and verify artifact versions and available digests",
        "release_url": release["html_url"],
        "assets": [
            {k: a.get(k) for k in ("id", "name", "size", "digest")} for a in assets
        ],
    }


def timed_observation(value: dict, result: dict, phase: str, now: float) -> dict:
    if result.get("commit") == value.get("merge") and value.get("merge"):
        # Start the independent verification phase at first confirmed merge.
        value["waits"].setdefault("phase:verification", now + PHASE_SECONDS)
    if result["status"] != "waiting":
        return result
    # Every gate consumes the same fixed phase budget, including approvals.
    waits = value["waits"]
    deadline = waits.setdefault(f"phase:{phase}", now + PHASE_SECONDS)
    result = {
        **result,
        "deadline": deadline,
        "remaining_seconds": max(0, int(deadline - now)),
        "poll_seconds": POLL_SECONDS,
    }
    if now >= deadline:
        result.update(
            status="timed_out", resume=f"$publish-release --resume --tag {value['tag']}"
        )
    return result


def verification_budget(value: dict, now: float) -> dict:
    if not value["merge"]:
        raise ReleaseError("Confirm the merged result before verification")
    result = timed_observation(
        value, {"status": "waiting", "gate": "verification"}, "verification", now
    )
    # merge-pr owns verification and its existing 30-second polling cadence.
    return {**result, "poll_seconds": 30}


def watch(
    value: dict,
    github: GitHub,
    store: Store,
    phase: str,
    *,
    clock=time.time,
    sleep=time.sleep,
    emit=print,
) -> dict:
    last_message = -float("inf")
    prior_gate = None
    prior_result = None
    phase_deadline = value["waits"].setdefault(
        f"phase:{phase}", clock() + PHASE_SECONDS
    )
    while True:
        now = clock()
        deadline = prior_result["deadline"] if prior_result else phase_deadline
        token = REMOTE_DEADLINE.set(time.monotonic() + max(0, deadline - now))
        try:
            if now >= deadline:
                raise WaitExpired("Release observation deadline reached")
            observed = observation(value, github, phase)
            if clock() >= deadline:
                raise WaitExpired("Release observation deadline reached")
            result = timed_observation(value, observed, phase, clock())
        except WaitExpired:
            result = {
                "status": "timed_out",
                "gate": prior_gate or phase,
                "deadline": deadline,
                "resume": f"$publish-release --resume --tag {value['tag']}",
            }
        finally:
            REMOTE_DEADLINE.reset(token)
        store.save(value)
        if result["status"] != "waiting":
            return result
        now = clock()
        if now - last_message >= 60 or result["gate"] != prior_gate:
            if result["gate"].startswith("approval:"):
                emit(
                    f"Waiting for approval on GitHub: {result['url']}. An eligible reviewer must approve. "
                    f"Continuing automatically; {result['remaining_seconds']} seconds remaining.",
                    flush=True,
                )
            else:
                emit(
                    f"Waiting for {result['gate']}; {result['remaining_seconds']} seconds remaining.",
                    flush=True,
                )
            last_message, prior_gate = now, result["gate"]
        prior_result = result
        sleep(min(POLL_SECONDS, max(0, result["deadline"] - clock())))


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "action",
        choices=(
            "open",
            "resume",
            "status",
            "bind-pr",
            "reconcile-pr",
            "verification-budget",
            "observe",
            "wait",
            "record-tag",
            "restore-tag",
            "prepare",
            "complete",
        ),
    )
    p.add_argument("--project-dir", type=Path, default=Path.cwd())
    p.add_argument("--tag")
    p.add_argument("--branch")
    p.add_argument("--base")
    p.add_argument("--workflow", help="Exact release workflow filename")
    p.add_argument(
        "--asset",
        action="append",
        help="Expected asset basename or glob; repeat for every required family",
    )
    p.add_argument("--pr", type=int)
    p.add_argument("--expected-head", help="Frozen PR head before an authorized repair")
    p.add_argument("--head", help="Exact newly reviewed PR head")
    p.add_argument(
        "--review-id", type=int, help="Fresh configured-operator COMMENT review"
    )
    p.add_argument(
        "--checkout",
        type=Path,
        help="Isolated checkout used for tag/runtime verification",
    )
    p.add_argument("--phase", choices=("pr", "merge", "release"), default="pr")
    return p


def main() -> int:
    args = parser().parse_args()
    try:
        scope = identity(args.project_dir)
        store = Store(state_home(), scope)
        github = GitHub(scope)
        with store.locked():
            if args.action == "open":
                if not all(
                    (args.tag, args.branch, args.base, args.workflow, args.asset)
                ):
                    raise ReleaseError(
                        "open requires tag, branch, base, workflow and expected assets"
                    )
                store.require_preparation_available(args.tag)
                if store.path(args.tag).exists():
                    value = store.load(args.tag)
                    if any(
                        value[k] != v
                        for k, v in {
                            "branch": args.branch,
                            "base": args.base,
                            "workflow": args.workflow,
                            "assets": args.asset,
                        }.items()
                    ):
                        raise ReleaseError(
                            "Release inputs differ from the existing checkpoint"
                        )
                else:
                    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.ya?ml", args.workflow):
                        raise ReleaseError("Use a release workflow filename")
                    if any("/" in a or "\\" in a or not a for a in args.asset):
                        raise ReleaseError("Expected assets must be basename patterns")
                    git(
                        Path(scope["root"]),
                        "check-ref-format",
                        f"refs/heads/{args.base}",
                    )
                    git(
                        Path(scope["root"]),
                        "check-ref-format",
                        f"refs/heads/{args.branch}",
                    )
                    if args.base == args.branch:
                        raise ReleaseError(
                            "Preparation target must not be the default branch"
                        )
                    default = github.api("")["default_branch"]
                    if default != args.base:
                        raise ReleaseError(
                            "Selected base differs from repository default"
                        )
                    value = dict(
                        schema=SCHEMA,
                        scope=scope,
                        tag=args.tag,
                        branch=args.branch,
                        base=args.base,
                        workflow=args.workflow,
                        assets=args.asset,
                        pr=None,
                        head=None,
                        merge=None,
                        tag_object=None,
                        tag_payload=None,
                        run=None,
                        waits={},
                        complete=False,
                    )
                    store.save(value)
                result = value
            else:
                value = store.select(args.tag)
                if args.action == "resume":
                    # Only explicit resume restarts wait budgets; ordinary polling never does.
                    value["waits"] = {}
                    store.save(value)
                    result = value
                elif args.action == "verification-budget":
                    result = verification_budget(value, time.time())
                    store.save(value)
                elif args.action == "prepare":
                    store.require_preparation_available(value["tag"])
                    if (
                        value["pr"]
                        or git(Path(scope["root"]), "branch", "--show-current")
                        != value["branch"]
                    ):
                        raise ReleaseError(
                            "Prepare requires the selected unbound PR branch"
                        )
                    helper = Path(__file__).with_name("publish-release-doer.sh")
                    output = command(
                        [
                            "bash",
                            str(helper),
                            "--mode",
                            "prep",
                            "--project-dir",
                            str(args.project_dir.resolve()),
                            "--tag",
                            value["tag"],
                            "--tag-prefix",
                            value["tag"].rsplit("-v", 1)[0],
                            "--main-branch",
                            value["base"],
                        ],
                        Path(scope["root"]),
                    )
                    result = {
                        "status": "prepared",
                        "next": "create-pr validates and commits the complete repository candidate",
                        "detail": output,
                    }
                elif args.action == "bind-pr":
                    if not args.pr or (value["pr"] and value["pr"] != args.pr):
                        raise ReleaseError("Expected the same positive PR number")
                    pr = github.pr(args.pr)
                    head = git(
                        Path(scope["root"]),
                        "rev-parse",
                        f"refs/heads/{value['branch']}",
                    )
                    if value["head"] not in (None, head):
                        raise ReleaseError(
                            "Frozen PR head changed; do not absorb new work on resume"
                        )
                    value.update(pr=args.pr, head=head)
                    check_pr(value, pr)
                    store.save(value)
                    result = value
                elif args.action == "reconcile-pr":
                    value = reconcile_pr(
                        value,
                        github,
                        git,
                        expected_head=args.expected_head,
                        head=args.head,
                        review_id=args.review_id,
                    )
                    store.save(value)
                    result = value
                elif args.action in {"observe", "wait", "complete"}:
                    phase = "release" if args.action == "complete" else args.phase
                    if args.action == "wait":
                        result = watch(value, github, store, phase)
                    else:
                        result = timed_observation(
                            value, observation(value, github, phase), phase, time.time()
                        )
                    if args.action == "complete":
                        # Asset contents must be verified, not merely existence asserted.
                        if result["status"] != "ready" or not args.checkout:
                            raise ReleaseError(
                                "Complete requires ready release and downloaded assets directory"
                            )
                        verify_downloads(args.checkout, result["assets"], value)
                        value["complete"] = True
                        result = {
                            "status": "published",
                            "release_url": result["release_url"],
                            "tag": value["tag"],
                        }
                    store.save(value)
                elif args.action in {"record-tag", "restore-tag"}:
                    if not args.checkout or not value["merge"]:
                        raise ReleaseError(
                            "record-tag requires isolated checkout and observed merged commit"
                        )
                    checkout = args.checkout.resolve(strict=True)
                    if (
                        checkout / git(checkout, "rev-parse", "--git-common-dir")
                    ).resolve() == (
                        Path(scope["root"])
                        / git(Path(scope["root"]), "rev-parse", "--git-common-dir")
                    ).resolve():
                        raise ReleaseError("Use an isolated clone for tag verification")
                    clone_scope = identity(checkout)
                    if any(
                        clone_scope[k] != scope[k]
                        for k in ("host", "repo", "origin_digest")
                    ):
                        raise ReleaseError("Tag checkout has a different origin")
                    if git(checkout, "rev-parse", "HEAD") != value["merge"] or git(
                        checkout, "status", "--porcelain", "--untracked-files=all"
                    ):
                        raise ReleaseError(
                            "Tag checkout must be clean at the frozen merged commit"
                        )
                    ref = f"refs/tags/{value['tag']}"
                    if args.action == "restore-tag":
                        payload = value.get("tag_payload")
                        if not payload or not value["tag_object"]:
                            raise ReleaseError(
                                "No recorded annotated tag payload to recover"
                            )
                        raw = payload.encode("utf-8")
                        header = f"tag {len(raw)}\0".encode()
                        algorithm = (
                            hashlib.sha1
                            if len(value["tag_object"]) == 40
                            else hashlib.sha256
                        )
                        if algorithm(header + raw).hexdigest() != value["tag_object"]:
                            raise ReleaseError(
                                "Recorded annotated tag payload is corrupt"
                            )
                        # Restore the same public Git object, never mint a replacement tag.
                        effect = subprocess.run(
                            ["git", "hash-object", "-t", "tag", "-w", "--stdin"],
                            cwd=checkout,
                            input=payload,
                            text=True,
                            capture_output=True,
                            timeout=45,
                            check=False,
                        )
                        if (
                            effect.returncode
                            or effect.stdout.strip() != value["tag_object"]
                        ):
                            raise ReleaseError(
                                "Cannot restore the recorded annotated tag object"
                            )
                        current = command(
                            ["git", "show-ref", "--verify", "--hash", ref],
                            checkout,
                            accepted=(0, 1, 128),
                        )
                        if current and current != value["tag_object"]:
                            raise ReleaseError(
                                "Recovery would replace an existing local tag"
                            )
                        git(
                            checkout,
                            "update-ref",
                            ref,
                            value["tag_object"],
                            current or "0" * len(value["tag_object"]),
                        )
                    obj = git(checkout, "rev-parse", ref)
                    if (
                        git(checkout, "cat-file", "-t", obj) != "tag"
                        or git(checkout, "rev-parse", f"{ref}^{{commit}}")
                        != value["merge"]
                        or git(checkout, "rev-parse", "HEAD") != value["merge"]
                        or git(
                            checkout, "status", "--porcelain", "--untracked-files=all"
                        )
                    ):
                        raise ReleaseError(
                            "Tag checkout/object does not match the verified merged result"
                        )
                    if value["tag_object"] not in (None, obj):
                        raise ReleaseError(
                            "Annotated tag object differs from checkpoint"
                        )
                    payload = command(
                        ["git", "cat-file", "tag", obj], checkout, strip=False
                    )
                    if not payload.startswith(
                        f"object {value['merge']}\ntype commit\ntag {value['tag']}\n"
                    ):
                        raise ReleaseError(
                            "Annotated tag payload differs from release identity"
                        )
                    if len(payload.encode()) > 16384:
                        raise ReleaseError(
                            "Annotated tag metadata exceeds checkpoint limit"
                        )
                    value["tag_object"] = obj
                    value["tag_payload"] = payload
                    store.save(value)
                    result = value
                else:
                    result = value
            print(json.dumps(result, sort_keys=True, indent=2))
            return 0 if result.get("status") not in {"blocked", "timed_out"} else 2
    except (ReleaseError, OSError, ValueError, KeyError, TypeError) as exc:
        message = (
            str(exc)
            if isinstance(exc, ReleaseError)
            else "Invalid or unavailable release evidence; progress retained"
        )
        print(json.dumps({"status": "blocked", "error": message}))
        return 2


def verify_downloads(directory: Path, assets: list[dict], value: dict) -> None:
    from email import message_from_bytes
    from zipfile import ZipFile

    directory = directory.resolve(strict=True)
    selected = [
        a
        for a in assets
        if any(fnmatch.fnmatchcase(a["name"], p) for p in value["assets"])
    ]
    for asset in selected:
        name = asset["name"]
        if Path(name).name != name or name in {".", ".."}:
            raise ReleaseError("Unsafe asset filename")
        path = directory / name
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != asset["size"]
        ):
            raise ReleaseError("Downloaded asset missing or size differs")
        digest = asset.get("digest")
        if digest:
            if (
                not digest.startswith("sha256:")
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest[7:]
            ):
                raise ReleaseError("Downloaded asset digest mismatch")
        if path.suffix == ".whl":
            with ZipFile(path) as wheel:
                names = [
                    n for n in wheel.namelist() if n.endswith(".dist-info/METADATA")
                ]
                if (
                    len(names) != 1
                    or message_from_bytes(wheel.read(names[0])).get("Version")
                    != value["tag"].rsplit("-v", 1)[1]
                ):
                    raise ReleaseError("Downloaded wheel version mismatch")
        elif not digest:
            raise ReleaseError(
                "Non-wheel asset requires digest evidence or project-owned verification"
            )


if __name__ == "__main__":
    raise SystemExit(main())
