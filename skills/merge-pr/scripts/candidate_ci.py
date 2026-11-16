#!/usr/bin/env python3
"""Trusted default-ref candidate CI identity; never mistake workflow SHA for PR SHA."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from merge_gate import SHA, GitHub, require, trusted_runtime


def identity(api, inputs):
    trusted_runtime(api)
    require(
        os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch",
        "Candidate CI requires dispatch",
    )
    number = inputs.get("candidate_pr", "")
    require(
        isinstance(number, str) and number.isdecimal() and int(number) > 0,
        "Invalid candidate PR",
    )
    head, base = inputs.get("candidate_head", ""), inputs.get("candidate_base", "")
    require(SHA.fullmatch(head) and SHA.fullmatch(base), "Invalid candidate commit")
    require(
        not any(
            inputs.get(k)
            for k in (
                "result_sha",
                "merge_pr",
                "broker_run_id",
                "broker_run_attempt",
                "correlation_id",
            )
        ),
        "Mixed candidate/result inputs",
    )
    pr = api.api(f"pulls/{int(number)}")
    require(
        pr["state"] == "open"
        and not pr["draft"]
        and pr["head"]["sha"] == head
        and pr["base"]["sha"] == base,
        "Candidate changed",
    )
    require(
        pr["head"].get("repo") and pr["base"]["repo"]["id"] == api.repo_info()["id"],
        "Invalid source repository",
    )
    request_id = inputs.get("candidate_request_id", "")
    source = inputs.get("candidate_source", "")
    require(
        re.fullmatch(r"[a-f0-9]{64}", request_id)
        and re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*", source),
        "Missing request authority",
    )
    source_run, source_attempt = map(int, source.split(":"))
    run = api.api(f"actions/runs/{source_run}")
    require(
        run["repository"]["id"] == api.repo_info()["id"]
        and run["path"] == ".github/workflows/github-ai-automation.yml"
        and run["head_branch"] == api.repo_info()["default_branch"]
        and run["head_sha"] == os.environ["GITHUB_SHA"]
        and run["event"] == "workflow_dispatch"
        and run["status"] == "completed"
        and run["conclusion"] == "success"
        and run["run_attempt"] == source_attempt,
        "Untrusted candidate admission producer",
    )
    artifacts = [
        a
        for a in api.pages(f"actions/runs/{source_run}/artifacts", "artifacts")
        if a["name"] == f"automation-result-{source_run}-{source_attempt}"
    ]
    require(
        len(artifacts) == 1
        and not artifacts[0]["expired"]
        and artifacts[0]["workflow_run"]["id"] == source_run,
        "Missing candidate admission",
    )
    admission = api.artifact(artifacts[0]["id"], "result.json")
    require(
        admission.get("code") == "CI_REQUIRED"
        and admission.get("request_id") == request_id
        and admission.get("operation") == "process",
        "Candidate CI was not admitted",
    )
    tree = api.api(f"git/commits/{head}")["tree"]["sha"]
    record = {
        "schema": "automation-candidate-ci/v2",
        "request_id": request_id,
        "source": source,
        "repository": api.repo,
        "repository_id": pr["base"]["repo"]["id"],
        "head_repository": pr["head"]["repo"]["full_name"],
        "head_repository_id": pr["head"]["repo"]["id"],
        "head_ref": pr["head"]["ref"],
        "pr_id": pr["id"],
        "pr_number": int(number),
        "head_sha": head,
        "base_sha": base,
        "tree_sha": tree,
        "run_id": int(os.environ["GITHUB_RUN_ID"]),
        "attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
        "workflow_sha": os.environ["GITHUB_SHA"],
    }

    require(
        admission.get("candidate")
        == {
            k: record[k]
            for k in (
                "repository",
                "repository_id",
                "head_repository",
                "head_repository_id",
                "head_ref",
                "pr_id",
                "pr_number",
                "head_sha",
                "base_sha",
                "tree_sha",
            )
        },
        "Candidate admission identity changed",
    )
    workflow = os.environ["GITHUB_WORKFLOW_REF"].split("@", 1)[0].rsplit("/", 1)[1]
    require(workflow in admission.get("expected_ci", []), "Candidate workflow not admitted")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["inputs", "checkout", "evidence"])
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    api = GitHub(os.environ["GITHUB_REPOSITORY"])
    inputs = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())["inputs"]
    record = identity(api, inputs)
    record["workflow"] = args.workflow
    if args.action == "inputs":
        with open(os.environ["GITHUB_OUTPUT"], "a") as out:
            out.write(f"post_merge=false\ncandidate=true\ncheckout_sha={record['head_sha']}\n")
    elif args.action == "checkout":
        # The workflow initially checked out its trusted revision with credentials disabled.
        subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "fetch",
                "--no-tags",
                "origin",
                f"refs/pull/{record['pr_number']}/head",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        fetched = subprocess.check_output(["git", "rev-parse", "FETCH_HEAD"], text=True).strip()
        require(fetched == record["head_sha"], "Candidate head moved")
        subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "checkout",
                "--detach",
                record["head_sha"],
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        actual = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], text=True).strip()
        require(actual == record["tree_sha"], "Candidate checkout mismatch")
    else:
        needs = json.loads(os.environ["CANDIDATE_JOB_RESULTS"])
        require(
            needs and all(value["result"] == "success" for value in needs.values()),
            "Candidate CI is incomplete or unsuccessful",
        )
        record["jobs"] = sorted(needs)
        record["conclusion"] = "success"
        Path(args.output).write_text(json.dumps(record, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
