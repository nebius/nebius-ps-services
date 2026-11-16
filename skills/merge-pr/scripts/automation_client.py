#!/usr/bin/env python3
"""Provider-neutral client for a repository's enrolled Actions authority."""

import argparse
import base64
import json
import re
import subprocess
import time
import uuid


def gh(*args):
    result = subprocess.run(
        ["gh", *args], capture_output=True, text=True, timeout=60, check=False
    )
    if result.returncode:
        raise RuntimeError("GITHUB_CLIENT_UNAVAILABLE")
    return result.stdout


def enrollment(repository=None):
    repo = (
        json.loads(gh("api", "repos/" + repository))
        if repository
        else json.loads(gh("repo", "view", "--json", "nameWithOwner,defaultBranchRef"))
    )
    name = repo.get("full_name", repo.get("nameWithOwner"))
    branch = repo.get("default_branch") or repo["defaultBranchRef"]["name"]
    # Missing configuration, denial and network failure all stop effects.
    value = json.loads(
        gh(
            "api",
            f"repos/{name}/contents/.github/ai-automation.json",
            "--method",
            "GET",
            "-f",
            f"ref={branch}",
        )
    )
    config = json.loads(base64.b64decode(value["content"]))
    if config.get("schema_version") != 1 or config.get("authority") != "actions":
        raise RuntimeError("AUTOMATION_NOT_ENROLLED")
    for workflow in config["workflows"].values():
        if not re.fullmatch(r"[a-z0-9-]+\.yml", workflow):
            raise RuntimeError("AUTOMATION_CONFIG_INVALID")
    return name, branch, config


def dispatch(repository, branch, workflow, inputs):
    args = ["workflow", "run", workflow, "--repo", repository, "--ref", branch]
    for key, value in inputs.items():
        args.extend(["-f", f"{key}={value}"])
    gh(*args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo")
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("status")
    request = commands.add_parser("request")
    request.add_argument("pr", type=int)
    request.add_argument(
        "--operation", choices=["process", "review"], default="process"
    )
    waiver = commands.add_parser("waiver")
    waiver.add_argument("pr", type=int)
    waiver.add_argument("--reason", required=True)
    waiver.add_argument("--revoke", action="store_true")
    waiver.add_argument("--wait", action="store_true")
    release = commands.add_parser("release")
    release.add_argument("project")
    release.add_argument("--pr", type=int, required=True)
    release.add_argument("--completion-run", type=int, required=True)
    release.add_argument("--tag", default="")
    args = parser.parse_args()
    try:
        repository, branch, config = enrollment(args.repo)
        if args.action == "status":
            print(json.dumps({"repository": repository, "authority": "actions"}))
            return
        if args.pr <= 0:
            raise RuntimeError("INVALID_PR")
        if args.action == "request":
            dispatch(
                repository,
                branch,
                config["workflows"]["controller"],
                {"pr_number": args.pr, "operation": args.operation},
            )
        elif args.action == "release":
            if (
                args.project not in config["release_projects"]
                or args.completion_run <= 0
            ):
                raise RuntimeError("INVALID_RELEASE_REQUEST")
            dispatch(
                repository,
                branch,
                config["workflows"]["release"],
                {
                    "project": args.project,
                    "pr_number": args.pr,
                    "completion_run": args.completion_run,
                    "tag": args.tag,
                },
            )
        else:
            request_id = uuid.uuid4().hex
            workflow = config["workflows"]["waiver"]
            dispatch(
                repository,
                branch,
                workflow,
                {
                    "pr_number": args.pr,
                    "reason": args.reason,
                    "revoke": str(args.revoke).lower(),
                    "request_id": request_id,
                },
            )
            if args.wait:
                deadline = time.monotonic() + 600
                title = f"ai-waiver-{args.pr}-{request_id}"
                while time.monotonic() < deadline:
                    runs = json.loads(
                        gh(
                            "run",
                            "list",
                            "--repo",
                            repository,
                            "--workflow",
                            workflow,
                            "--branch",
                            branch,
                            "--event",
                            "workflow_dispatch",
                            "--limit",
                            "100",
                            "--json",
                            "databaseId,displayTitle,status,conclusion",
                        )
                    )
                    matches = [r for r in runs if r["displayTitle"] == title]
                    if len(matches) == 1 and matches[0]["status"] == "completed":
                        if matches[0]["conclusion"] != "success":
                            raise RuntimeError("WAIVER_NOT_REGISTERED")
                        print("WAIVER_REGISTERED")
                        return
                    time.sleep(10)
                raise RuntimeError("WAIVER_PENDING")
        print("REQUEST_SUBMITTED")
    except (RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        code = (
            str(exc)
            if re.fullmatch(r"[A-Z_]+", str(exc))
            else "AUTOMATION_CLIENT_FAILED"
        )
        parser.exit(2, code + "\n")


if __name__ == "__main__":
    main()
