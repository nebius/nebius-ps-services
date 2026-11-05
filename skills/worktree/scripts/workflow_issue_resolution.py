"""Issue evidence for local Implement delivery and native GitHub closure.

This module never calls GitHub, publishes a branch, or grants merge authority.
The root coordinator supplies independently observed evidence; strict checks
bind it to accepted members and exact commits before rendering closing lines.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

from workflow_security import contains_sensitive
from workflow_state import WorkflowError, digest, git_environment, read, write


EVIDENCE_SCHEMA = "workflow/issue-resolution-evidence-v1"
RECEIPT_SCHEMA = "workflow/issue-resolution-v1"
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
IDENTITY = {"host", "repository", "number"}
START = "<!-- skills:issue-resolution:start -->"
END = "<!-- skills:issue-resolution:end -->"


def require(condition, message):
    if not condition:
        raise WorkflowError("ISSUE_RESOLUTION_INVALID", message)


def text(value):
    require(isinstance(value, str) and 0 < len(value) <= 8192,
            "issue evidence must be bounded nonempty text")
    value = " ".join(value.split())
    require(bool(value) and not contains_sensitive(value), "unsafe issue evidence")
    return value


def identity(value):
    require(isinstance(value, dict) and IDENTITY <= value.keys(), "full issue identity required")
    host, repo, number = value["host"], value["repository"], value["number"]
    require(isinstance(host, str) and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", host),
            "invalid GitHub host")
    require(isinstance(repo, str) and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)
            and all(part not in {".", ".."} for part in repo.split("/")), "invalid repository")
    require(type(number) is int and 0 < number < 2**63, "invalid issue number")
    return {"host": host.lower(), "repository": repo.lower(), "number": number}


def key(value):
    return tuple(identity(value)[field] for field in ("host", "repository", "number"))


def metadata(value):
    fields = {"plan_path", "plan_sha256", "close_after_merge", "closure_request_sha256", "members"}
    require(isinstance(value, dict) and set(value) == fields, "invalid issue plan metadata")
    path = value["plan_path"]
    require(isinstance(path, str) and path.startswith("docs/github-issues/")
            and path.endswith(".md") and not PurePosixPath(path).is_absolute()
            and ".." not in path.split("/") and "\\" not in path
            and str(PurePosixPath(path)) == path, "plan path must be project-relative")
    require(SHA256.fullmatch(str(value["plan_sha256"])), "plan digest required")
    close = value["close_after_merge"]
    authority = value["closure_request_sha256"]
    require(type(close) is bool, "closure intent must be boolean")
    require((close and SHA256.fullmatch(str(authority)) and authority != value["plan_sha256"])
            or (not close and authority is None), "separate direct-user closure provenance required")
    members = value["members"]
    require(isinstance(members, list) and 0 < len(members) <= 256, "bounded accepted members required")
    normalized, seen = [], set()
    for member in members:
        require(isinstance(member, dict) and set(member) == IDENTITY | {"criteria"}, "invalid member")
        item = identity(member)
        require(key(item) not in seen, "duplicate accepted member")
        seen.add(key(item))
        criteria = member["criteria"]
        require(isinstance(criteria, list) and 0 < len(criteria) <= 256, "member criteria required")
        item["criteria"] = [text(criterion) for criterion in criteria]
        require(len(set(item["criteria"])) == len(criteria), "duplicate acceptance criterion")
        normalized.append(item)
    return {**value, "members": normalized}


def evidence(context, value):
    if context is None:
        require(value is None, "issue evidence has no accepted issue objective")
        return None
    context = metadata(context)
    require(isinstance(value, dict) and set(value) == {"schema", "issues"}
            and value["schema"] == EVIDENCE_SCHEMA, "issue evidence schema required")
    rows = value["issues"]
    require(isinstance(rows, list) and len(rows) == len(context["members"]), "exact member coverage required")
    indexed = {}
    for row in rows:
        require(isinstance(row, dict) and set(row) == IDENTITY | {"summary", "criteria"}, "invalid issue result")
        token = key(row)
        require(token not in indexed, "duplicate issue result")
        indexed[token] = row
    result = []
    for member in context["members"]:
        row = indexed.get(key(member))
        require(row is not None, "accepted member result missing")
        checks = row["criteria"]
        require(isinstance(checks, list) and len(checks) == len(member["criteria"]), "exact criterion coverage required")
        normalized = []
        for criterion, check in zip(member["criteria"], checks):
            require(isinstance(check, dict) and set(check) == {"criterion", "status", "evidence"}
                    and check["criterion"] == criterion and isinstance(check["status"], str)
                    and check["status"] in {"passed", "failed", "not_run"},
                    "criteria must match accepted order and declare observed outcomes")
            normalized.append({**check, "evidence": text(check["evidence"])})
        result.append({**identity(member), "summary": text(row["summary"]), "criteria": normalized})
    return {"schema": EVIDENCE_SCHEMA, "issues": result}


def objective_context(run_dir):
    from workflow_objectives import validate_run
    run, binding = validate_run(run_dir)
    revision = binding["revisions"][-1]
    value = read(run_dir / revision["snapshot"])
    context = value.get("issue_resolution")
    require(context is None or run["owner"] == "implement", "issue handoffs belong to Implement")
    return run, binding, revision, context


def seal_evidence(run_dir, value):
    return evidence(objective_context(run_dir)[3], value)


def completion_receipt(run_dir, records):
    run, binding, revision, context = objective_context(run_dir)
    if context is None:
        return None
    require(not run.get("fence") and len(records) == 1, "one unfenced Implement execution required")
    record = records[0]
    head = record.get("promoted_head")
    require(record.get("status") == "done" and COMMIT.fullmatch(str(head))
            and head == record.get("integration_head"), "exact delivered commit required")
    results = evidence(context, record.get("issue_evidence"))
    require(record.get("issue_evidence_head") == head
            and record.get("issue_objective_sha256") == revision["sha256"], "stale issue evidence")
    rows = []
    for row in results["issues"]:
        passed = [check["status"] == "passed" for check in row["criteria"]]
        status = "resolved" if all(passed) else "partial" if any(passed) else "unresolved"
        rows.append({**row, "resolution": status,
                     "eligible": context["close_after_merge"] and status == "resolved"})
    return {"schema": RECEIPT_SCHEMA, "owner": "implement", "run_id": run["run_id"],
            "objective_id": binding["objective_id"], "objective_sha256": revision["sha256"],
            "plan_path": context["plan_path"], "plan_sha256": context["plan_sha256"],
            "closure_request_sha256": context["closure_request_sha256"],
            "delivered_head": head, "execution_sha256": digest(record),
            "merge_status": "pending", "issues": rows}


def write_completion(run_dir, records):
    """Called only by complete_run under coordinator authorization and its lock."""
    value = completion_receipt(run_dir, records)
    if value is None:
        return None
    path = run_dir / "issue-resolution.json"
    if path.exists():
        require(read(path) == value, "existing issue receipt conflicts with completion")
    else:
        write(path, value)
    return value


def read_handoff(run_dir):
    run, _, _, _ = objective_context(run_dir)
    require(run["status"] == "done", "local delivery must be complete")
    records = [read(path) for path in (run_dir / "execution").glob("*/coordinator.json")]
    expected = completion_receipt(run_dir, records)
    require(expected is not None and read(run_dir / "issue-resolution.json") == expected,
            "missing or changed issue handoff")
    return expected


def closing_block(receipt, *, head, host, base, default_branch, observations):
    """Fresh coordinator observations, not issue prose, determine publication."""
    require(COMMIT.fullmatch(str(head)), "reviewed PR head required")
    require(isinstance(observations, dict) and set(observations) == {"head", "issues"}
            and observations["head"] == head, "publication evidence must bind reviewed head")
    rows = observations["issues"]
    require(isinstance(rows, list) and len(rows) == len(receipt["issues"]), "refresh every member")
    indexed = {}
    for row in rows:
        require(isinstance(row, dict) and set(row) == IDENTITY | {
            "state", "scope_unchanged", "comment_verified", "auto_close", "criteria", "summary"}, "invalid live observation")
        require(key(row) not in indexed and isinstance(row["state"], str)
                and row["state"] in {"open", "closed"}
                and type(row["scope_unchanged"]) is bool and type(row["comment_verified"]) is bool
                and isinstance(row["auto_close"], str)
                and row["auto_close"] in {"enabled", "disabled", "unknown"},
                "invalid live issue state")
        indexed[key(row)] = row
    candidates = []
    for member in receipt["issues"]:
        row = indexed.get(key(member))
        require(row is not None, "live issue identity mismatch")
        context = {"plan_path": receipt["plan_path"], "plan_sha256": receipt["plan_sha256"],
                   "close_after_merge": False, "closure_request_sha256": None,
                   "members": [{**identity(member), "criteria": [c["criterion"] for c in member["criteria"]]}]}
        fresh = evidence(context, {"schema": EVIDENCE_SCHEMA, "issues": [{
            **identity(row), "criteria": row["criteria"], "summary": row["summary"]}]})["issues"][0]
        if (receipt["closure_request_sha256"] and row["state"] == "open" and row["scope_unchanged"]
                and row["comment_verified"] and row["auto_close"] != "disabled"
                and all(c["status"] == "passed" for c in fresh["criteria"])
                and member["host"] == host.lower() and base == default_branch):
            candidates.append(f"Closes {member['repository']}#{member['number']}")
    return "\n".join([START, *candidates, END]) if candidates else ""


def replace_block(body, block):
    require(isinstance(body, str), "PR body required")
    require(body.count(START) == body.count(END) and body.count(START) <= 1, "ambiguous owned PR section")
    if START in body:
        start, end = body.index(START), body.index(END)
        require(start < end, "invalid owned PR section")
        return body[:start] + block + body[end + len(END):]
    return body + ("\n\n" if body else "") + block if block else body


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--default-branch", required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--body-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        receipt = read_handoff(args.run_dir)
        def git(*arguments):
            return subprocess.run(["git", "-C", str(args.checkout), *arguments],
                                  capture_output=True, text=True, timeout=30,
                                  env=git_environment(args.checkout))
        current = git("rev-parse", "HEAD")
        require(current.returncode == 0 and current.stdout.strip() == args.head,
                "checkout does not match reviewed PR head")
        status = git("status", "--porcelain=v1", "--untracked-files=all")
        require(status.returncode == 0 and not status.stdout, "reviewed checkout must be clean")
        require(git("merge-base", "--is-ancestor", receipt["delivered_head"], args.head).returncode == 0,
                "PR head does not contain the delivered change")
        observations = read(args.observations)
        block = closing_block(receipt, head=args.head, host=args.host, base=args.base,
                              default_branch=args.default_branch, observations=observations)
        print(json.dumps({"body": replace_block(args.body_file.read_text(), block),
                          "handoff_sha256": digest(receipt), "head": args.head,
                          "closure": ("configured-unverified" if any(row["auto_close"] == "unknown" for row in observations["issues"])
                                      else "configured") if block else "pending"}))
    except (WorkflowError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "blocked", "code": getattr(error, "code", "ISSUE_RESOLUTION_INVALID")}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
