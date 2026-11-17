#!/usr/bin/env python3
"""Exact-commit release qualification. Read-only except isolated CI checkout."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote
import zipfile

from publication import GitHub, SHA, Unverifiable, command, require

SCHEMA = "release-qualification/v1"
FIELDS = ("qualification_sha", "qualification_profile", "qualification_id")
OTHER_FIELDS = (
    "candidate_pr",
    "candidate_head",
    "candidate_base",
    "candidate_request_id",
    "candidate_source",
    "result_sha",
    "merge_pr",
    "broker_run_id",
    "broker_run_attempt",
    "correlation_id",
)


def merge_owner_path():
    """Support the source catalog and the documented flat Actions deployment."""
    here = Path(__file__).resolve().parent
    owner = (
        here
        if (here / "merge_gate.py").is_file()
        else here.parents[1] / "merge-pr/scripts"
    )
    require(
        (owner / "merge_gate.py").is_file()
        and (owner / "merge_completion.py").is_file(),
        "Install the matching merge gate and completion evidence owners",
    )
    return str(owner)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def profile_at(api, commit, project):
    require(bool(SHA.fullmatch(commit)), "Invalid qualification commit")
    response = api.api("contents/.github/merge-policy.json?ref=" + commit)
    require(
        response.get("encoding") == "base64" and response.get("type") == "file",
        "Missing release profile at the selected commit",
    )
    policy = json.loads(base64.b64decode(response["content"], validate=False))
    profiles = policy.get("release_profiles", {})
    require(project in profiles, "No release profile at the selected commit")
    profile = profiles[project]
    require(
        isinstance(profile, dict)
        and profile.get("workflows")
        and re.fullmatch(r"[A-Za-z0-9_.-]+\.yml", profile.get("publisher", "")),
        "Malformed release profile",
    )
    for workflow, definition in profile["workflows"].items():
        require(
            re.fullmatch(r"[A-Za-z0-9_.-]+\.yml", workflow)
            and isinstance(definition.get("jobs"), dict)
            and definition["jobs"],
            "Malformed qualification producer",
        )
        names = []
        for job, members in definition["jobs"].items():
            require(
                re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", job)
                and isinstance(members, list)
                and members
                and all(isinstance(n, str) and n for n in members),
                "Malformed required jobs",
            )
            names.extend(members)
        require(len(names) == len(set(names)), "Ambiguous required job names")
    return profile, digest(profile)


def main_history(api, commit):
    repo = api.api("")
    tip = api.api("commits/" + quote(repo["default_branch"], safe=""))["sha"]
    require(
        api.api(f"compare/{commit}...{tip}")["status"] in {"ahead", "identical"},
        "Qualification commit is outside default history",
    )
    return repo


def qualification_inputs(api, event, workflow):
    values = event.get("inputs") or {}
    require(
        all(values.get(k) for k in FIELDS)
        and not any(values.get(k) for k in OTHER_FIELDS),
        "Qualification identity is incomplete or mixed with another CI mode",
    )
    commit, project, correlation = (values[k] for k in FIELDS)
    require(
        re.fullmatch(r"[a-f0-9]{32}", correlation), "Invalid qualification correlation"
    )
    repo = main_history(api, commit)
    require(
        os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        and os.environ.get("GITHUB_REF") == "refs/heads/" + repo["default_branch"]
        and os.environ.get("GITHUB_WORKFLOW_REF")
        == f"{api.repo}/.github/workflows/{workflow}@refs/heads/{repo['default_branch']}",
        "Qualification requires the trusted default workflow",
    )
    permission = api.api(
        "collaborators/" + quote(os.environ["GITHUB_ACTOR"], safe="") + "/permission"
    )
    require(
        permission.get("permission") in {"write", "maintain", "admin"},
        "Qualification requires an authorized repository operator",
    )
    profile, policy_digest = profile_at(api, commit, project)
    require(workflow in profile["workflows"], "Workflow is outside the release profile")
    return {
        "qualification": "true",
        "post_merge": "false",
        "candidate": "false",
        "checkout_sha": commit,
        "profile": project,
        "profile_digest": policy_digest,
        "qualification_id": correlation,
    }


def checkout(api, event, workflow):
    identity = qualification_inputs(api, event, workflow)
    require(
        Path.cwd().resolve() == Path(os.environ["GITHUB_WORKSPACE"]).resolve(),
        "Qualification checkout requires the workspace root",
    )
    require(
        command(["git", "rev-parse", "HEAD"]) == os.environ["GITHUB_SHA"]
        and not command(["git", "status", "--porcelain"]),
        "Qualification must start from clean trusted code",
    )
    # Fetch alone receives the read-only token through process-local Git config.
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"GH_TOKEN", "GITHUB_TOKEN"} and not k.startswith("GIT_CONFIG_")
    }
    env["GIT_TERMINAL_PROMPT"] = "0"
    token = os.environ.get("GH_TOKEN")
    require(bool(token), "Qualification checkout requires the read-only Actions token")
    auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    fetch_env = {
        **env,
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {auth}",
    }
    for args in [
        [
            "fetch",
            "--quiet",
            "--no-tags",
            "--no-recurse-submodules",
            f"https://github.com/{api.repo}.git",
            identity["checkout_sha"],
        ],
        ["checkout", "--quiet", "--detach", identity["checkout_sha"]],
    ]:
        result = subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "credential.helper=",
                *args,
            ],
            env=fetch_env if args[0] == "fetch" else env,
            capture_output=True,
            timeout=120,
        )
        require(result.returncode == 0, "Qualification checkout failed")
    require(
        command(["git", "rev-parse", "HEAD"]) == identity["checkout_sha"],
        "Checkout identity drift",
    )
    return identity


def archive(api, artifact, filename=None):
    require(
        not artifact.get("expired")
        and 0 < artifact["size_in_bytes"] <= 64 * 1024 * 1024,
        "Qualification artifact is missing, expired or oversized",
    )
    raw = command(api.args(f"actions/artifacts/{int(artifact['id'])}/zip"), binary=True)
    require(len(raw) <= 64 * 1024 * 1024, "Qualification archive exceeds limit")
    expected = artifact.get("digest", "")
    require(
        expected == "sha256:" + hashlib.sha256(raw).hexdigest(),
        "Artifact digest mismatch",
    )
    values = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        entries = bundle.infolist()
        require(
            len(entries) <= 1000
            and sum(e.file_size for e in entries) <= 128 * 1024 * 1024,
            "Qualification archive expansion exceeds limit",
        )
        for entry in entries:
            require(
                not entry.filename.startswith("/")
                and ".." not in Path(entry.filename).parts,
                "Unsafe artifact member",
            )
            if entry.filename.endswith(".json"):
                require(
                    entry.file_size <= 8 * 1024 * 1024,
                    "Qualification report exceeds limit",
                )
                require(entry.filename not in values, "Duplicate qualification report")
                values[entry.filename] = json.loads(bundle.read(entry))
    if filename:
        require(set(values) == {filename}, "Unexpected qualification manifest contents")
        return values[filename]
    return values


def successful_jobs(jobs, definition):
    expected = [n for names in definition["jobs"].values() for n in names]
    result = {}
    for name in expected:
        found = [j for j in jobs if j["name"] == name]
        require(
            len(found) == 1 and found[0]["conclusion"] == "success",
            "Missing, duplicated, skipped or failed required job: "
            + name
            + "; qualification requires rerun-all or a new complete qualification",
        )
        result[name] = {"id": found[0]["id"], "conclusion": "success"}
    return result


def report_evidence(api, artifacts, definition, attempt):
    records = []
    for contract in definition.get("reports", []):
        require(
            contract.get("contract") in {"native-cases/v1", "package-history/v1"},
            "Unknown required report contract",
        )
        name = contract["name"].format(attempt=attempt)
        found = [a for a in artifacts if a["name"] == name]
        require(len(found) == 1, "Missing or ambiguous qualification report: " + name)
        artifact = found[0]
        values = archive(api, artifact)
        native = [v for v in values.values() if isinstance(v, dict) and "outcomes" in v]
        if contract["contract"] == "native-cases/v1":
            require(len(native) == 1, "Missing native case outcomes")
            value = native[0]
            nodes = value.get("expected_nodeids", [])
            inputs = [
                v
                for v in values.values()
                if isinstance(v, dict) and "dependencies" in v
            ]
            require(
                nodes
                and len(nodes) == len(set(nodes))
                and value.get("status") == "passed"
                and value.get("exit_code") == 0
                and value.get("topology") == "all"
                and value.get("expected_cases") == len(nodes)
                and len(inputs) == 1
                and inputs[0].get("expected_nodeids") == nodes
                and inputs[0].get("image_id")
                and inputs[0].get("platform"),
                "Incomplete native qualification identity or coverage",
            )
            require(
                all(
                    value["outcomes"].get(n, {}).get(phase) == "passed"
                    for n in nodes
                    for phase in ("setup", "call", "teardown")
                ),
                "Native cases were skipped, failed or unrun",
            )
        if contract["contract"] == "package-history/v1":
            matrices = [v for k, v in values.items() if Path(k).name == "matrix.json"]
            require(
                len(matrices) == 1 and matrices[0].get("status") == "passed",
                "Historical package matrix is incomplete",
            )
            matrix = matrices[0]
            cases = matrix.get("expected_case_ids", [])
            rows = matrix.get("cases", [])
            require(
                matrix.get("coverage") == "complete"
                and matrix.get("topology") == "all"
                and cases
                and len(cases) == len(set(cases))
                and matrix.get("expected_cases") == len(cases)
                and matrix.get("expected_repeats") == len(cases)
                and [row.get("case") for row in rows] == cases
                and all(
                    row.get("status") == "passed" and row.get("repeat") for row in rows
                ),
                "Historical package coverage is partial or has missing repeats",
            )
        records.append(
            {"id": artifact["id"], "name": name, "digest": artifact["digest"]}
        )
    return records


def emit(api, workflow, commit, project, needs, event):
    repo = main_history(api, commit)
    profile, policy_digest = profile_at(api, commit, project)
    require(workflow in profile["workflows"], "Unexpected qualification producer")
    definition = profile["workflows"][workflow]
    require(
        all(needs.get(j, {}).get("result") == "success" for j in definition["jobs"]),
        "Required qualification jobs did not succeed",
    )
    run_id, attempt = (
        int(os.environ["GITHUB_RUN_ID"]),
        int(os.environ["GITHUB_RUN_ATTEMPT"]),
    )
    run = api.api(f"actions/runs/{run_id}/attempts/{attempt}")
    validate_origin(api, run, repo, workflow, commit, project, event=event)
    jobs = successful_jobs(
        api.pages(f"actions/runs/{run_id}/attempts/{attempt}/jobs", "jobs"), definition
    )
    reports = report_evidence(
        api,
        api.pages(f"actions/runs/{run_id}/artifacts", "artifacts"),
        definition,
        attempt,
    )
    tree = api.api("git/commits/" + commit)["tree"]["sha"]
    return {
        "schema": SCHEMA,
        "repository": api.repo,
        "repository_id": repo["id"],
        "project": project,
        "profile_digest": policy_digest,
        "sha": commit,
        "tree": tree,
        "workflow": workflow,
        "workflow_sha": run["head_sha"],
        "run_id": run_id,
        "run_attempt": attempt,
        "event": run["event"],
        "required_jobs": definition["jobs"],
        "jobs": jobs,
        "reports": reports,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def title(project, commit, correlation):
    return f"release-qualification-{project}-{commit}-{correlation}"


def broker_result(api, run):
    """Authenticate a broker route; this proves identity, never CI success."""
    sys.path.insert(0, merge_owner_path())
    import merge_completion as completion
    import merge_gate as gate

    owner = gate.GitHub(api.repo)
    policy = json.loads(
        base64.b64decode(
            api.api("contents/.github/merge-policy.json?ref=" + run["head_sha"])[
                "content"
            ]
        )
    )
    try:
        target = completion.route_target(owner, policy, run)
        intent = completion.read_intent(
            owner, target["run_id"], target["run_attempt"],
            target["pr"], target["head"], policy,
        )
        return completion.merged_result(owner, intent)
    except gate.Blocked:
        raise Unverifiable("Broker routing evidence is unavailable or invalid") from None


def validate_origin(api, run, repo, workflow, commit, project, event=None):
    require(
        run["path"] == ".github/workflows/" + workflow
        and run["head_repository"]["full_name"] == api.repo
        and run["head_branch"] == repo["default_branch"],
        "Untrusted qualification producer",
    )
    require(
        api.api(f"compare/{run['head_sha']}...{repo['default_branch']}")["status"]
        in {"ahead", "identical"},
        "Producer revision is outside default history",
    )
    if run["event"] == "push":
        require(run["head_sha"] == commit, "Push qualified a different commit")
    elif run["event"] == "workflow_dispatch" and run.get(
        "display_title", ""
    ).startswith("release-qualification-"):
        prefix = title(project, commit, "")
        require(
            run["display_title"].startswith(prefix)
            and re.fullmatch("[a-f0-9]{32}", run["display_title"][len(prefix) :]),
            "Qualification dispatch identity mismatch",
        )
        permission = api.api(
            "collaborators/" + quote(run["actor"]["login"], safe="") + "/permission"
        )
        require(
            permission.get("permission") in {"write", "maintain", "admin"},
            "Untrusted qualification actor",
        )
        if event is not None:
            identity = qualification_inputs(api, event, workflow)
            require(
                identity["checkout_sha"] == commit and identity["profile"] == project,
                "Qualification inputs changed",
            )
    elif run["event"] == "workflow_dispatch" and run.get(
        "display_title", ""
    ).startswith("post-merge-"):
        require(
            broker_result(api, run) == commit,
            "Broker result differs from release commit",
        )
    else:
        raise Unverifiable("Unsupported qualification event")


def candidate_runs(api, repo, workflow, commit, project):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    runs = api.pages(
        f"actions/workflows/{quote(workflow, safe='')}/runs?created=>={cutoff}",
        "workflow_runs",
    )
    candidates = []
    for run in runs:
        known = (
            run["event"] == "push"
            and run["head_sha"] == commit
            and run["head_branch"] == repo["default_branch"]
        )
        known = known or (
            run["event"] == "workflow_dispatch"
            and run.get("display_title", "").startswith(title(project, commit, ""))
        )
        if not known and run.get("display_title", "").startswith("post-merge-"):
            sys.path.insert(0, merge_owner_path())
            import merge_gate as gate

            if not (
                run["event"] == "workflow_dispatch"
                and run["path"] == ".github/workflows/" + workflow
                and run["head_repository"]["full_name"] == api.repo
                and run["head_branch"] == repo["default_branch"]
                and run.get("actor", {}).get("id") == gate.ACTIONS_BOT_ID
            ):
                continue
            attempts = []
            for artifact in api.pages(f"actions/runs/{run['id']}/artifacts", "artifacts"):
                match = re.fullmatch(
                    rf"result-route-{run['id']}-(\d+)", artifact["name"]
                )
                if match and not artifact["expired"]:
                    attempt = int(match[1])
                    require(1 <= attempt <= run["run_attempt"], "Invalid route attempt")
                    attempts.append(attempt)
            require(
                attempts,
                "Post-merge producer routing unavailable; source association unresolved",
            )
            if attempts:
                attempt = max(attempts)
                historical = api.api(f"actions/runs/{run['id']}/attempts/{attempt}")
                require(
                    historical["id"] == run["id"]
                    and historical["run_attempt"] == attempt
                    and all(historical[key] == run[key] for key in (
                        "path", "head_sha", "event", "display_title",
                    )),
                    "Historical route producer changed",
                )
                # A rerun has the same source association even before it emits
                # a new routing artifact. Keep it in the candidate set so an
                # older green run cannot mask this pending or failed attempt.
                known = broker_result(api, historical) == commit
        if known:
            candidates.append(run)
    return candidates


def verify(api, project, commit, *, publisher=None):
    repo = main_history(api, commit)
    profile, policy_digest = profile_at(api, commit, project)
    require(
        publisher is None or publisher == profile["publisher"],
        "Publisher differs from release profile",
    )
    proofs, missing, pending = {}, [], []
    for workflow, definition in profile["workflows"].items():
        candidates = candidate_runs(api, repo, workflow, commit, project)
        if not candidates:
            missing.append(workflow)
            continue
        run = max(
            candidates, key=lambda r: (r["created_at"], r["id"], r["run_attempt"])
        )
        if run["status"] != "completed":
            pending.append(workflow)
            continue
        require(
            run["conclusion"] == "success", "Latest qualification failed: " + workflow
        )
        validate_origin(api, run, repo, workflow, commit, project)
        artifacts = api.pages(f"actions/runs/{run['id']}/artifacts", "artifacts")
        name = f"release-qualification-{project.replace('/', '-')}-{run['id']}-{run['run_attempt']}"
        matches = [a for a in artifacts if a["name"] == name]
        require(len(matches) == 1, "Missing qualification manifest: " + workflow)
        artifact = matches[0]
        value = archive(api, artifact, "qualification.json")
        required = {
            "schema": SCHEMA,
            "repository": api.repo,
            "repository_id": repo["id"],
            "project": project,
            "profile_digest": policy_digest,
            "sha": commit,
            "tree": api.api("git/commits/" + commit)["tree"]["sha"],
            "workflow": workflow,
            "workflow_sha": run["head_sha"],
            "run_id": run["id"],
            "run_attempt": run["run_attempt"],
            "event": run["event"],
            "required_jobs": definition["jobs"],
        }
        require(
            all(value.get(k) == v for k, v in required.items()),
            "Qualification identity mismatch",
        )
        created = datetime.fromisoformat(value["created_at"].replace("Z", "+00:00"))
        require(
            datetime.now(timezone.utc) - timedelta(days=14)
            <= created
            <= datetime.now(timezone.utc),
            "Qualification evidence expired or has a future timestamp",
        )
        jobs = successful_jobs(
            api.pages(
                f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs", "jobs"
            ),
            definition,
        )
        reports = report_evidence(api, artifacts, definition, run["run_attempt"])
        require(
            value.get("jobs") == jobs and value.get("reports") == reports,
            "Qualification coverage drift",
        )
        current = api.api(f"actions/runs/{run['id']}")
        require(
            current.get("run_attempt") == run["run_attempt"]
            and current.get("status") == "completed"
            and current.get("conclusion") == "success",
            "Qualification was superseded by a new or unsuccessful attempt",
        )
        proofs[workflow] = {
            "run_id": run["id"],
            "run_attempt": run["run_attempt"],
            "artifact_id": artifact["id"],
            "digest": artifact["digest"],
        }
    # A later producer may take time to inspect. Recheck the complete selected
    # set at the consumption boundary, not only while each producer is visited.
    for workflow, proof in proofs.items():
        candidates = candidate_runs(api, repo, workflow, commit, project)
        require(candidates, "Qualification disappeared during verification")
        latest = max(
            candidates, key=lambda r: (r["created_at"], r["id"], r["run_attempt"])
        )
        require(
            (latest["id"], latest["run_attempt"])
            == (proof["run_id"], proof["run_attempt"]),
            "Qualification was superseded while verifying the profile",
        )
        current = api.api(f"actions/runs/{proof['run_id']}")
        require(
            current.get("run_attempt") == proof["run_attempt"]
            and current.get("status") == "completed"
            and current.get("conclusion") == "success",
            "Qualification was superseded while verifying the profile",
        )
    return {
        "status": "waiting" if pending else "missing" if missing else "qualified",
        "sha": commit,
        "project": project,
        "profile_digest": policy_digest,
        "proofs": proofs,
        "missing": missing,
        "pending": pending,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["inputs", "checkout", "emit", "verify"])
    parser.add_argument("--workflow")
    parser.add_argument("--project")
    parser.add_argument("--commit")
    parser.add_argument("--publisher")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        api = GitHub()
        event = (
            json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
            if os.environ.get("GITHUB_EVENT_PATH")
            else {}
        )
        if args.action in {"inputs", "checkout"}:
            result = (checkout if args.action == "checkout" else qualification_inputs)(
                api, event, args.workflow
            )
            with open(os.environ["GITHUB_OUTPUT"], "a") as out:
                out.writelines(f"{key}={value}\n" for key, value in result.items())
        elif args.action == "emit":
            result = emit(
                api,
                args.workflow,
                args.commit,
                args.project,
                json.loads(os.environ["CI_NEEDS"]),
                event,
            )
        else:
            result = verify(api, args.project, args.commit, publisher=args.publisher)
        if args.output:
            Path(args.output).write_text(json.dumps(result, sort_keys=True) + "\n")
        else:
            print(json.dumps(result, sort_keys=True))
        return 2 if result.get("status") in {"missing", "waiting"} else 0
    except (Unverifiable, KeyError, ValueError, zipfile.BadZipFile) as error:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "reason": str(error)
                    if isinstance(error, Unverifiable)
                    else "Malformed qualification evidence",
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
