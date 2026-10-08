#!/usr/bin/env python3
"""Deterministic receipt recovery, exact-result CI and Pages verification."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote
import zipfile

from merge_gate import (
    ACTIONS_BOT_ID,
    Blocked,
    GitHub,
    INTENT_SCHEMA,
    Pending,
    SHA,
    operator_ids,
    require,
    trusted_runtime,
)

CI_SCHEMA = "merge-result-ci/v1"
FIELDS = (
    "result_sha",
    "merge_pr",
    "broker_run_id",
    "broker_run_attempt",
    "correlation_id",
)


def workflow_path(filename):
    require(
        bool(re.fullmatch(r"[A-Za-z0-9_.-]+\.ya?ml", filename)),
        "Invalid workflow filename",
    )
    return f".github/workflows/{filename}"


def contains(api, ancestor, descendant):
    require(
        bool(SHA.fullmatch(ancestor) and SHA.fullmatch(descendant)),
        "Invalid commit identity",
    )
    require(
        api.api(f"compare/{ancestor}...{descendant}")["status"]
        in {"ahead", "identical"},
        "Commit is not in the required history",
    )


def producer_matches(api, run, filename, default):
    return (
        run["path"] == workflow_path(filename)
        and run["head_branch"] == default
        and run["head_repository"]["full_name"] == api.repo
    )


def trusted_run(api, run, filename, default):
    require(
        producer_matches(api, run, filename, default),
        "Untrusted producer workflow",
    )
    contains(
        api,
        run["head_sha"],
        api.api(f"branches/{quote(default, safe='')}")["commit"]["sha"],
    )


def read_intent(api, run_id, attempt, number, head, policy):
    default = api.repo_info()["default_branch"]
    run = api.api(f"actions/runs/{run_id}/attempts/{attempt}")
    trusted_run(api, run, policy["merge_workflow"], default)
    require(
        run["event"] in {"workflow_dispatch", "workflow_run", "schedule"},
        "Unsupported intent producer event",
    )
    name = f"merge-intent-{number}-{head}-{attempt}"
    artifacts = [
        a
        for a in api.pages(f"actions/runs/{run_id}/artifacts", "artifacts")
        if a["name"] == name
    ]
    require(
        len(artifacts) == 1 and not artifacts[0]["expired"],
        "Missing or expired merge intent; maintainer reconciliation required",
    )
    value = api.artifact(artifacts[0]["id"], "intent.json")
    require(
        value["schema"] == INTENT_SCHEMA
        and value["repository"] == api.repo
        and value["pr"] == number
        and value["head"] == head
        and value["base"] == default
        and value["run_id"] == run_id
        and value["run_attempt"] == attempt
        and value["source_sha"] == run["head_sha"],
        "Merge intent identity mismatch",
    )
    if run["event"] == "workflow_dispatch":
        require(
            run["actor"]["id"] in operator_ids() and value["review_id"] > 0,
            "Untrusted operator intent",
        )
    else:
        require(
            value["review_id"] == 0, "Automatic intent must use Dependabot admission"
        )
    allowed = {w["file"] for w in policy["ci_workflows"]}
    require(
        value["ci_workflows"]
        and len(set(value["ci_workflows"])) == len(value["ci_workflows"])
        and set(value["ci_workflows"]) <= allowed,
        "Intent refers to unsupported CI workflows",
    )
    return value


def merged_result(api, intent):
    pr = api.api(f"pulls/{intent['pr']}")
    require(
        pr["head"]["sha"] == intent["head"]
        and pr["head"]["repo"]["full_name"] == api.repo
        and pr["base"]["repo"]["full_name"] == api.repo
        and pr["base"]["ref"] == intent["base"],
        "Merged PR identity drift",
    )
    if not pr["merged"]:
        require(pr["state"] == "open", "PR closed without merging")
        raise Pending("PR has not merged; no completion effects")
    result = pr["merge_commit_sha"]
    tip = api.api(f"branches/{quote(intent['base'], safe='')}")["commit"]["sha"]
    contains(api, result, tip)
    return result


def correlation(repo, number, result, workflow):
    return hashlib.sha256(
        json.dumps([repo, number, result, workflow], separators=(",", ":")).encode()
    ).hexdigest()[:32]


def ci_name(corr):
    return f"post-merge-{corr}"


def matching_runs(api, workflow, corr):
    return [
        r
        for r in api.pages(
            f"actions/workflows/{quote(workflow, safe='')}/runs?event=workflow_dispatch",
            "workflow_runs",
        )
        if r["display_title"] == ci_name(corr)
    ]


def validate_ci_run(api, run, intent, result, workflow, policy):
    trusted_run(api, run, workflow, intent["base"])
    corr = correlation(api.repo, intent["pr"], result, workflow)
    require(
        run["event"] == "workflow_dispatch"
        and run["actor"]["id"] == ACTIONS_BOT_ID
        and run["display_title"] == ci_name(corr),
        "Untrusted result CI run",
    )
    if run["status"] != "completed":
        raise Pending("Post-merge CI is pending")
    require(run["conclusion"] == "success", "Post-merge CI failed; publication blocked")
    artifacts = [
        a
        for a in api.pages(f"actions/runs/{run['id']}/artifacts", "artifacts")
        if a["name"] == f"result-ci-{run['id']}-{run['run_attempt']}"
    ]
    require(
        len(artifacts) == 1 and not artifacts[0]["expired"],
        "Missing exact-result CI evidence",
    )
    value = api.artifact(artifacts[0]["id"], "result.json")
    require(
        value["schema"] == CI_SCHEMA
        and value["repository"] == api.repo
        and value["result_sha"] == result
        and value["checkout_sha"] == result
        and value["merge_pr"] == intent["pr"]
        and value["workflow"] == workflow
        and value["run_id"] == run["id"]
        and value["run_attempt"] == run["run_attempt"]
        and value["correlation_id"] == corr,
        "Result CI receipt mismatch",
    )
    original = read_intent(
        api,
        value["broker_run_id"],
        value["broker_run_attempt"],
        intent["pr"],
        intent["head"],
        policy,
    )
    require(workflow in original["ci_workflows"], "CI was not admitted by its producer")
    return {
        "workflow": workflow,
        "run_id": run["id"],
        "run_attempt": run["run_attempt"],
        "result_sha": result,
    }


def prepare_dispatch(api, intent, policy):
    result = merged_result(api, intent)
    missing = [
        w
        for w in intent["ci_workflows"]
        if not matching_runs(api, w, correlation(api.repo, intent["pr"], result, w))
    ]
    if missing:
        prefix = f"merge-dispatch-{intent['pr']}-{result}-"
        for artifact in api.pages("actions/artifacts", "artifacts"):
            if not artifact["name"].startswith(prefix) or artifact["expired"]:
                continue
            run = api.api(f"actions/runs/{artifact['workflow_run']['id']}")
            if not producer_matches(
                api, run, policy["completion_workflow"], intent["base"]
            ):
                continue
            trusted_run(api, run, policy["completion_workflow"], intent["base"])
            previous = api.artifact(artifact["id"], "dispatch.json")
            require(
                previous["schema"] == "merge-dispatch/v1"
                and previous["repository"] == api.repo
                and previous["pr"] == intent["pr"]
                and previous["result_sha"] == result,
                "Invalid dispatch journal",
            )
            require(
                not set(missing).intersection(previous["workflows"]),
                "Previous dispatch has an uncertain outcome; reconcile its run before retry",
            )
    return {
        "schema": "merge-dispatch/v1",
        "repository": api.repo,
        "pr": intent["pr"],
        "result_sha": result,
        "workflows": missing,
        "run_id": int(os.environ["GITHUB_RUN_ID"]),
        "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
    }


def verify_or_dispatch(api, intent, policy, dispatch=False, journal=None):
    result = merged_result(api, intent)
    evidence, pending = [], []
    for workflow in intent["ci_workflows"]:
        corr = correlation(api.repo, intent["pr"], result, workflow)
        runs = matching_runs(api, workflow, corr)
        if not runs:
            if dispatch:
                require(
                    journal
                    and workflow in journal["workflows"]
                    and journal["repository"] == api.repo
                    and journal["pr"] == intent["pr"]
                    and journal["result_sha"] == result
                    and journal["run_id"] == int(os.environ["GITHUB_RUN_ID"])
                    and journal["run_attempt"] == int(os.environ["GITHUB_RUN_ATTEMPT"]),
                    "Missing current dispatch journal",
                )
                artifact_id = int(os.environ.get("DISPATCH_ARTIFACT_ID", "0"))
                require(
                    artifact_id > 0
                    and api.artifact(artifact_id, "dispatch.json") == journal,
                    "Dispatch journal must be uploaded before effects",
                )
                # A failed/ambiguous response is never blindly retried. A later
                # reconciliation first searches the stable correlation identity.
                response = api.api(
                    f"actions/workflows/{quote(workflow, safe='')}/dispatches",
                    "POST",
                    {
                        "ref": intent["base"],
                        "inputs": {
                            "result_sha": result,
                            "merge_pr": str(intent["pr"]),
                            "broker_run_id": str(intent["run_id"]),
                            "broker_run_attempt": str(intent["run_attempt"]),
                            "correlation_id": corr,
                        },
                    },
                )
                require(
                    type(response.get("workflow_run_id")) is int,
                    "Dispatch response is ambiguous; reconcile before retry",
                )
                pending.append(
                    {"workflow": workflow, "run_id": response["workflow_run_id"]}
                )
            else:
                pending.append({"workflow": workflow})
            continue
        # Never select an older successful attempt over a newer failed attempt.
        run = max(runs, key=lambda r: (r["run_number"], r["run_attempt"]))
        try:
            evidence.append(validate_ci_run(api, run, intent, result, workflow, policy))
        except Pending:
            pending.append({"workflow": workflow, "run_id": run["id"]})
    return {
        "outcome": "pending" if pending else "ci-verified",
        "pr": intent["pr"],
        "head": intent["head"],
        "result_sha": result,
        "ci": evidence,
        "pending": pending,
    }


def prepare_pages(api, intent, policy):
    ci = verify_or_dispatch(api, intent, policy)
    if ci["outcome"] != "ci-verified":
        raise Pending("Exact-result CI must pass before Pages publication")
    if not policy.get("pages_enabled", False):
        return {"request": False, "result_sha": ci["result_sha"]}
    state = verify_pages(api, intent)
    needed = state["outcome"] == "pending" and not state["build_active"]
    if needed:
        prefix = f"merge-pages-{intent['pr']}-{ci['result_sha']}-"
        for artifact in api.pages("actions/artifacts", "artifacts"):
            if not artifact["name"].startswith(prefix) or artifact["expired"]:
                continue
            run = api.api(f"actions/runs/{artifact['workflow_run']['id']}")
            if not producer_matches(
                api, run, policy["completion_workflow"], intent["base"]
            ):
                continue
            trusted_run(api, run, policy["completion_workflow"], intent["base"])
            previous = api.artifact(artifact["id"], "pages.json")
            require(
                previous["schema"] == "merge-pages/v1"
                and previous["repository"] == api.repo
                and previous["pr"] == intent["pr"]
                and previous["result_sha"] == ci["result_sha"],
                "Invalid Pages request journal",
            )
            require(
                not previous["request"],
                "Previous Pages request is uncertain; reconcile its build before retry",
            )
    return {
        "schema": "merge-pages/v1",
        "repository": api.repo,
        "pr": intent["pr"],
        "result_sha": ci["result_sha"],
        "request": needed,
        "run_id": int(os.environ["GITHUB_RUN_ID"]),
        "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
    }


def verify_pages(api, intent, request=False, journal=None):
    result = merged_result(api, intent)
    site = api.api("pages")
    require(
        site["build_type"] == "legacy"
        and site["source"] == {"branch": intent["base"], "path": "/"},
        "Pages source changed; reconcile publication configuration",
    )
    builds = api.pages("pages/builds")
    for build in builds:
        if build["status"] != "built":
            continue
        comparison = api.api(f"compare/{result}...{build['commit']}")
        if comparison["status"] not in {"ahead", "identical"}:
            continue
        return {
            "outcome": "pages-verified",
            "result_sha": result,
            "build_commit": build["commit"],
            "build_url": build["url"],
        }
    for build in builds:
        if build["status"] == "errored" and api.api(
            f"compare/{result}...{build['commit']}"
        )["status"] in {"ahead", "identical"}:
            raise Blocked(
                "Pages build failed; inspect the build before an explicit retry"
            )
    active = any(b["status"] in {"queued", "building"} for b in builds)
    if request and not active:
        require(
            journal
            and journal["schema"] == "merge-pages/v1"
            and journal["repository"] == api.repo
            and journal["pr"] == intent["pr"]
            and journal["result_sha"] == result
            and journal["request"]
            and journal["run_id"] == int(os.environ["GITHUB_RUN_ID"])
            and journal["run_attempt"] == int(os.environ["GITHUB_RUN_ATTEMPT"]),
            "Missing current Pages request journal",
        )
        artifact_id = int(os.environ.get("PAGES_ARTIFACT_ID", "0"))
        require(
            artifact_id > 0 and api.artifact(artifact_id, "pages.json") == journal,
            "Pages request journal must be uploaded before effects",
        )
        api.api("pages/builds", "POST")
    return {
        "outcome": "pending",
        "reason": "Pages build containing merge result is pending",
        "result_sha": result,
        "build_active": active,
    }


def targets(api, policy, event):
    # Run metadata and JSON artifacts only: no executable artifact is extracted.
    artifacts = api.pages("actions/artifacts", "artifacts")
    default = api.repo_info()["default_branch"]
    completed = set()
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    for artifact in artifacts:
        if not artifact["name"].startswith("merge-complete-") or artifact["expired"]:
            continue
        run = api.api(f"actions/runs/{artifact['workflow_run']['id']}")
        if (
            not producer_matches(api, run, policy["completion_workflow"], default)
            or run["status"] != "completed"
        ):
            continue
        trusted_run(api, run, policy["completion_workflow"], default)
        value = api.artifact(artifact["id"], "completion.json")
        require(
            value["schema"] == "merge-completion/v1"
            and value["repository"] == api.repo
            and value["run_id"] == run["id"]
            and value["source_sha"] == run["head_sha"]
            and artifact["name"]
            == f"merge-complete-{value['pr']}-{value['head']}-{run['id']}-{value['run_attempt']}",
            "Invalid terminal completion receipt",
        )
        jobs = api.pages(
            f"actions/runs/{run['id']}/attempts/{value['run_attempt']}/jobs", "jobs"
        )
        if not any(
            j["name"] == f"verify-pages-{value['pr']}" and j["conclusion"] == "success"
            for j in jobs
        ):
            continue
        pr = api.api(f"pulls/{value['pr']}")
        require(
            pr["merged"]
            and pr["head"]["sha"] == value["head"]
            and pr["merge_commit_sha"] == value["result_sha"],
            "Terminal completion identity drift",
        )
        completed.add((value["pr"], value["head"]))
    selected = {}
    for artifact in artifacts:
        match = re.fullmatch(
            r"merge-intent-(\d+)-([0-9a-f]{40}(?:[0-9a-f]{24})?)-(\d+)",
            artifact["name"],
        )
        if not match:
            continue
        number, head, attempt = int(match[1]), match[2], int(match[3])
        if (number, head) in completed:
            continue
        # Recovery is bounded to the advertised receipt-retention window. Older
        # missing evidence is an explicit manual reconciliation case, not replay.
        if (
            datetime.fromisoformat(artifact["created_at"].replace("Z", "+00:00"))
            < cutoff
        ):
            continue
        producer = artifact["workflow_run"]["id"]
        # Ignore lookalike artifacts from unrelated workflows without trusting them.
        run = api.api(f"actions/runs/{producer}")
        if not producer_matches(api, run, policy["merge_workflow"], default):
            continue
        if run["status"] != "completed":
            continue
        trusted_run(api, run, policy["merge_workflow"], default)
        pr = api.api(f"pulls/{number}")
        if pr["merged"] and pr["head"]["sha"] == head:
            key = (number, head)
            value = {
                "pr": number,
                "head": head,
                "run_id": producer,
                "run_attempt": attempt,
            }
            if key not in selected or (producer, attempt) > (
                selected[key]["run_id"],
                selected[key]["run_attempt"],
            ):
                selected[key] = value
    require(
        len(selected) <= 200,
        "Completion matrix capacity exceeded; reconcile older receipts",
    )
    return list(selected.values())


def ci_inputs(api, policy, event, workflow):
    values = event.get("inputs") or {}
    present = [bool(values.get(k)) for k in FIELDS]
    if not any(present):
        return {"post_merge": "false", "checkout_sha": os.environ["GITHUB_SHA"]}
    require(
        all(present) and os.environ["GITHUB_EVENT_NAME"] == "workflow_dispatch",
        "Post-merge inputs must be supplied together",
    )
    trusted_runtime(api)
    require(
        int(os.environ["GITHUB_ACTOR_ID"]) == ACTIONS_BOT_ID,
        "Post-merge CI requires Actions dispatch",
    )
    number = int(values["merge_pr"])
    pr = api.api(f"pulls/{number}")
    intent = read_intent(
        api,
        int(values["broker_run_id"]),
        int(values["broker_run_attempt"]),
        number,
        pr["head"]["sha"],
        policy,
    )
    result = merged_result(api, intent)
    require(
        values["result_sha"] == result
        and workflow in intent["ci_workflows"]
        and values["correlation_id"] == correlation(api.repo, number, result, workflow),
        "Invalid post-merge CI target",
    )
    return {"post_merge": "true", "checkout_sha": result}


def emit_ci_evidence(api, policy, event, workflow, needs, expected):
    identity = ci_inputs(api, policy, event, workflow)
    require(identity["post_merge"] == "true", "No result evidence for ordinary CI")
    require(
        expected
        and all(needs.get(job, {}).get("result") == "success" for job in expected),
        "Push-equivalent CI job did not succeed",
    )
    require(
        all(v.get("result") in {"success", "skipped"} for v in needs.values()),
        "CI contains failed or cancelled jobs",
    )
    values = event["inputs"]
    result = {
        "schema": CI_SCHEMA,
        "repository": api.repo,
        "workflow": workflow,
        "result_sha": values["result_sha"],
        "checkout_sha": identity["checkout_sha"],
        "merge_pr": int(values["merge_pr"]),
        "broker_run_id": int(values["broker_run_id"]),
        "broker_run_attempt": int(values["broker_run_attempt"]),
        "correlation_id": values["correlation_id"],
        "run_id": int(os.environ["GITHUB_RUN_ID"]),
        "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "targets",
            "prepare-dispatch",
            "prepare-pages",
            "dispatch",
            "verify",
            "pages",
            "ci-inputs",
            "ci-evidence",
        ],
    )
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--policy", default=".github/merge-policy.json")
    parser.add_argument("--pr", type=int)
    parser.add_argument("--head")
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--run-attempt", type=int)
    parser.add_argument("--workflow")
    parser.add_argument("--output")
    parser.add_argument("--dispatch-record")
    parser.add_argument("--pages-record")
    parser.add_argument("--request", action="store_true")
    args = parser.parse_args()
    try:
        api = GitHub(args.repo or "")
        policy = json.loads(Path(args.policy).read_text())
        event = (
            json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
            if os.environ.get("GITHUB_EVENT_PATH")
            else {}
        )
        if args.action in {
            "targets",
            "prepare-dispatch",
            "prepare-pages",
            "dispatch",
            "pages",
        }:
            trusted_runtime(api)
        if args.action == "targets":
            result = targets(api, policy, event)
        elif args.action == "ci-inputs":
            result = ci_inputs(api, policy, event, args.workflow)
            with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
                for key, value in result.items():
                    stream.write(f"{key}={value}\n")
        elif args.action == "ci-evidence":
            result = emit_ci_evidence(
                api,
                policy,
                event,
                args.workflow,
                json.loads(os.environ["CI_NEEDS"]),
                json.loads(os.environ["CI_EXPECTED_JOBS"]),
            )
        else:
            intent = read_intent(
                api, args.run_id, args.run_attempt, args.pr, args.head, policy
            )
            if args.action == "prepare-dispatch":
                result = prepare_dispatch(api, intent, policy)
                with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
                    stream.write(f"result_sha={result['result_sha']}\n")
            elif args.action == "prepare-pages":
                result = prepare_pages(api, intent, policy)
                with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
                    stream.write(f"ready=true\nresult_sha={result['result_sha']}\n")
            elif args.action in {"pages", "verify"}:
                # Pages publication never runs before exact-result CI succeeds.
                ci = verify_or_dispatch(api, intent, policy)
                if ci["outcome"] != "ci-verified":
                    raise Pending("Exact-result CI must pass before Pages publication")
                if policy.get("pages_enabled", False):
                    journal = (
                        json.loads(Path(args.pages_record).read_text())
                        if args.pages_record
                        else None
                    )
                    result = verify_pages(
                        api,
                        intent,
                        args.request and bool(journal and journal["request"]),
                        journal,
                    )
                else:
                    result = {
                        "outcome": "not-applicable",
                        "reason": "Pages is not enabled in merge policy",
                    }
                if result["outcome"] in {"pages-verified", "not-applicable"}:
                    result = {
                        "schema": "merge-completion/v1",
                        "repository": api.repo,
                        "outcome": "complete",
                        "pr": intent["pr"],
                        "head": intent["head"],
                        "result_sha": ci["result_sha"],
                        "ci": ci["ci"],
                        "pages": result,
                        "run_id": int(os.environ.get("GITHUB_RUN_ID", "0")),
                        "run_attempt": int(os.environ.get("GITHUB_RUN_ATTEMPT", "0")),
                        "source_sha": os.environ.get("GITHUB_SHA", ""),
                    }
                if os.environ.get("GITHUB_OUTPUT"):
                    with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
                        stream.write(
                            "complete="
                            + str(result["outcome"] == "complete").lower()
                            + "\n"
                        )
            else:
                result = verify_or_dispatch(
                    api,
                    intent,
                    policy,
                    args.action == "dispatch",
                    json.loads(Path(args.dispatch_record).read_text())
                    if args.dispatch_record
                    else None,
                )
        if args.output:
            Path(args.output).write_text(json.dumps(result, sort_keys=True))
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except (
        Blocked,
        KeyError,
        ValueError,
        TypeError,
        OSError,
        subprocess.TimeoutExpired,
        zipfile.BadZipFile,
    ) as exc:
        print(
            json.dumps(
                {
                    "outcome": "pending" if isinstance(exc, Pending) else "blocked",
                    "reason": str(exc)
                    if isinstance(exc, Blocked)
                    else "Invalid or unavailable completion evidence",
                }
            )
        )
        return 2 if isinstance(exc, Pending) else 1


if __name__ == "__main__":
    sys.exit(main())
