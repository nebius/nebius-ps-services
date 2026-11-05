"""Execute a frozen repository-owned acceptance harness and admit exact evidence.

The reviewed harness owns assertions and independent product observations. The
coordinator owns scheduling and authority; it cannot turn supplied labels into
execution evidence. No raw harness output or environment values are persisted.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid

from workflow_state import git_environment, WorkflowError, digest, lock, read, write
from workflow_objectives import _git, check_dispatch
from workflow_program import accepted, verify_inputs
from project_specs_lib.source_bundle import read_source, relative_path

SHA = re.compile(r"[0-9a-f]{64}\Z")
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
SCHEMA = "workflow/qualification-plan-v1"
RECEIPT = "workflow/qualification-receipt-v1"


def require(condition, message):
    if not condition:
        raise WorkflowError("QUALIFICATION_BLOCKED", message)


def target(run_dir, execution_id):
    run, latest, snapshot = accepted(run_dir)
    project = Path(run["project_root"])
    program = verify_inputs(run_dir, project)
    require(program is not None, "qualification requires accepted source coverage")
    if execution_id.startswith("PHASE:"):
        phase_id = execution_id.removeprefix("PHASE:")
        selected = next((r for r in program["definition"]["phases"] if r["id"] == phase_id), None)
        require(selected is not None, "unknown phase")
        require(not any(s["phase"] == phase_id for s in program["definition"]["slices"]),
                "delivery phases qualify through their slices")
        criteria, head, basis = selected["exit_criteria"], _git(project, "rev-parse", "HEAD"), None
        kind = "external"
    else:
        selected = next((r for r in program["definition"]["slices"] if r["id"] == execution_id), None)
        require(selected is not None, "unknown delivery slice")
        coordinator = read(run_dir / "execution" / execution_id / "coordinator.json")
        require(coordinator.get("schema") == "workflow/execution-coordinator-v2"
                and coordinator.get("status") in {"promoted", "cleanup", "done"}
                and coordinator.get("promoted_head"), "qualification requires a promoted candidate")
        from workflow_impact import verify_execution
        basis = verify_execution(run_dir, execution_id, coordinator["plan_digest"], project)
        head = coordinator["promoted_head"]
        criteria = sorted(set(selected["criteria"] + selected["regression_criteria"]))
        kind = selected["change_kind"]
    require(_git(project, "rev-parse", "HEAD") == head and not _git(project, "status", "--porcelain"),
            "candidate checkout is dirty or differs from promotion")
    return {"project": project, "head": head, "criteria": criteria, "kind": kind,
            "obligation_basis": {c: program["bundle"]["obligation_basis"][c] for c in criteria},
            "basis_sha256": digest(basis) if basis else None, "objective_sha256": latest["sha256"],
            "inputs": snapshot["inputs"], "completion": snapshot["completion"], "definition": selected}


def validate_plan(plan, expected):
    keys = {"schema", "execution_id", "runner", "runner_sha256", "criteria", "environment",
            "operations", "timeout_seconds", "environment_keys", "live"}
    require(isinstance(plan, dict) and set(plan) == keys and plan["schema"] == SCHEMA, "invalid qualification plan")
    require(isinstance(plan["criteria"], list) and all(isinstance(v, str) for v in plan["criteria"])
            and len(plan["criteria"]) == len(set(plan["criteria"])) == len(expected["criteria"])
            and set(plan["criteria"]) == set(expected["criteria"]), "all slice and inherited criteria must be tested")
    relative_path(plan["runner"])
    require(plan["runner"].endswith(".py") and SHA.fullmatch(str(plan["runner_sha256"])), "runner must be a pinned Python harness")
    require(type(plan["timeout_seconds"]) is int and 1 <= plan["timeout_seconds"] <= 3600, "invalid command deadline")
    require(type(plan["live"]) is bool, "explicit live applicability is required")
    environment = plan["environment"]
    require(isinstance(environment, dict) and set(environment) == {"id", "resource", "nonproduction"}
            and environment["nonproduction"] is True
            and all(isinstance(environment[k], str) and LABEL.fullmatch(environment[k]) for k in ("id", "resource")),
            "qualification requires an identified confirmed disposable/nonproduction target")
    require(isinstance(plan["operations"], list) and all(isinstance(v, str) and v for v in plan["operations"]), "invalid operation scope")
    require(set(plan["operations"]) <= set(expected["completion"].get("live_operations", [])),
            "live operations exceed accepted run authority")
    require(isinstance(plan["environment_keys"], list)
            and all(isinstance(v, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", v)
                    and not v.startswith(("PYTHON", "LD_", "DYLD_"))
                    for v in plan["environment_keys"]), "invalid runtime environment allowlist")
    if not plan["live"]:
        require(expected["kind"] == "non-runtime" and expected["completion"].get("allow_nonruntime_na") is True,
                "live N/A is limited to accepted non-runtime delivery")
        require(not plan["operations"], "non-runtime validation cannot claim live operations")


def _runner(project, plan):
    body = read_source(project, plan["runner"])
    require(hashlib.sha256(body).hexdigest() == plan["runner_sha256"], "qualification harness changed")
    tracked = _git(project, "ls-files", "--", plan["runner"])
    require(tracked == plan["runner"], "qualification harness must be reviewed committed source")
    return project / plan["runner"]


def frozen_plan(run_dir, execution_id, plan, expected):
    validate_plan(plan, expected)
    require(plan["execution_id"] == execution_id, "qualification target changed")
    _runner(expected["project"], plan)
    if not execution_id.startswith("PHASE:"):
        coordinator = read(run_dir / "execution" / execution_id / "coordinator.json")
        text = Path(coordinator["plan_path"]).read_text()
        require(re.search(r"(?m)^Qualification plan SHA256: " + digest(plan) + r"$", text),
                "qualification must be frozen in the locked implementation plan")


def command(project, plan, action, criterion=None):
    """One bounded process group; output digests only, strict structured result."""
    runner = _runner(project, plan)
    argv = [sys.executable, "-I", str(runner), action]
    if criterion is not None:
        argv.extend(["--criterion", criterion])
    environment = {"PATH": os.defpath, "LANG": "C.UTF-8"}
    environment.update({k: os.environ[k] for k in plan["environment_keys"] if k in os.environ})
    owned = git_environment(project)
    if "SKILLS_GIT_PROFILE" in owned:
        for key in ("SKILLS_GIT_PROFILE", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_ATTR_NOSYSTEM", "GIT_TERMINAL_PROMPT"):
            environment[key] = owned[key]
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(argv, cwd=project, env=environment, stdout=output,
                                   stderr=errors, start_new_session=True)
        deadline = time.monotonic() + plan["timeout_seconds"]
        try:
            while process.poll() is None:
                require(time.monotonic() < deadline, "qualification command timed out")
                require(output.tell() <= 1024 * 1024 and errors.tell() <= 1024 * 1024, "qualification output exceeded its bound")
                time.sleep(0.05)
        finally:
            # Also terminate background children before releasing the live resource.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=10)
        output.seek(0)
        raw = output.read(1024 * 1024 + 1)
        errors.seek(0)
        stderr = errors.read(1024 * 1024 + 1)
    require(process.returncode == 0 and len(raw) <= 1024 * 1024 and len(stderr) <= 1024 * 1024,
            "qualification command failed or exceeded output limits")
    value = json.loads(raw)
    require(isinstance(value, dict), "harness result must be an object")
    return value, {"action": action, "criterion": criterion, "exit_code": 0,
                   "stdout_sha256": hashlib.sha256(raw).hexdigest(), "stderr_sha256": hashlib.sha256(stderr).hexdigest()}


def _candidate(value, expected):
    require(isinstance(value, dict) and set(value) == {"head", "artifact_sha256", "configuration_sha256"}
            and value["head"] == expected["head"]
            and all(SHA.fullmatch(str(value[k])) for k in ("artifact_sha256", "configuration_sha256")),
            "candidate artifact/configuration identity is invalid")


def _observation(value, candidate, plan):
    require(set(value) == set(candidate) | {"environment", "trial", "reset", "intervened", "writers_quiescent"}
            and all(value.get(k) == v for k, v in candidate.items())
            and value["environment"] == plan["environment"]["id"]
            and value["intervened"] is False and value["writers_quiescent"] is True
            and all(isinstance(value[k], str) and LABEL.fullmatch(value[k]) for k in ("trial", "reset")),
            "deployment identity, trial provenance or intervention status is invalid")


def qualify(run_dir: Path, execution_id: str, plan: dict):
    check_dispatch(run_dir)
    expected = target(run_dir, execution_id)
    frozen_plan(run_dir, execution_id, plan, expected)
    root = run_dir / "qualification" / execution_id.replace(":", "-")
    identity = uuid.uuid4().hex
    path = root / (identity + ".json")
    receipt = {"schema": RECEIPT, "id": identity, "execution_id": execution_id, "status": "incomplete",
               "objective_sha256": expected["objective_sha256"], "inputs": expected["inputs"],
               "basis_sha256": expected["basis_sha256"], "head": expected["head"], "plan": plan,
               "obligation_basis": expected["obligation_basis"],
               "plan_sha256": digest(plan), "started_at": time.time(), "commands": [], "criteria": {}}
    write(path, receipt, exclusive=True)
    # Global across runs/projects; serialize the identified shared target.
    lease = run_dir.parents[2] / "qualification-leases" / digest(plan["environment"]["resource"])
    def execute(action, criterion=None):
        with lock(run_dir.parent):
            check_dispatch(run_dir)
            require(accepted(run_dir)[1]["sha256"] == expected["objective_sha256"], "qualification authority changed")
            return command(expected["project"], plan, action, criterion)
    # All admission paths use coordinator -> resource ordering. Holding both
    # also prevents another root transition from changing authority mid-trial.
    with lock(run_dir.parent), lock(lease):
        try:
            candidate, proof = execute("candidate")
            _candidate(candidate, expected)
            receipt.update(candidate=candidate, resource_lease=digest(plan["environment"]["resource"]))
            receipt["commands"].append(proof)
            before, proof = execute("observe")
            if isinstance(before.get("trial"), str) and LABEL.fullmatch(before["trial"]):
                receipt["trial_id"] = before["trial"]
                write(path, receipt)
            _observation(before, candidate, plan)
            receipt.update(before=before)
            receipt["commands"].append(proof)
            write(path, receipt)
            for old_path in root.glob("*.json"):
                old = read(old_path)
                if old_path != path and old.get("trial_id", old.get("before", {}).get("trial")) == before["trial"]:
                    require(old.get("status") == "passed", "a failed/intervened trial requires a fresh reset and trial")
            for criterion in plan["criteria"]:
                receipt["attempted_criterion"] = criterion
                write(path, receipt)
                value, proof = execute("criterion", criterion)
                require(set(value) == {"criterion", "tests", "passed", "failed", "skipped"}
                        and value["criterion"] == criterion
                        and all(type(value[k]) is int for k in ("tests", "passed", "failed", "skipped"))
                        and value["tests"] > 0 and value["passed"] == value["tests"]
                        and value["failed"] == value["skipped"] == 0, "mandatory criterion did not execute and pass completely")
                receipt["criteria"][criterion] = value
                receipt["commands"].append(proof)
                receipt.pop("attempted_criterion")
                write(path, receipt)
            after, proof = execute("observe")
            _observation(after, candidate, plan)
            require(after == before, "qualification target or trial changed during testing")
            receipt.update(after=after)
            receipt["commands"].append(proof)
            require(target(run_dir, execution_id) == expected, "accepted source/candidate changed during qualification")
            receipt["status"] = "passed"
        except BaseException:
            receipt["status"] = "failed"
            write(path, receipt)
            raise
    receipt["finished_at"] = time.time()
    write(path, receipt)
    with lock(root):
        index_path = root / "produced.json"
        index = read(index_path) if index_path.exists() else {"receipts": {}}
        index["receipts"][identity] = digest(receipt)
        write(index_path, index)
    return {"status": "QUALIFIED", "receipt": str(path.relative_to(run_dir)), "sha256": digest(receipt)}


def verify_receipt(run_dir, reference, *, current=True):
    require(isinstance(reference, dict) and set(reference) == {"receipt", "sha256"}, "invalid qualification reference")
    relative_path(reference["receipt"])
    require(reference["receipt"].startswith("qualification/"), "qualification reference escapes its owner")
    value = read(run_dir / reference["receipt"])
    require(digest(value) == reference["sha256"] and value.get("schema") == RECEIPT
            and value.get("status") == "passed", "qualification is missing, changed or failed")
    require(Path(reference["receipt"]).name == value["id"] + ".json"
            and read((run_dir / reference["receipt"]).parent / "produced.json")["receipts"].get(value["id"]) == reference["sha256"],
            "receipt was not admitted by the qualification producer")
    require(value["plan_sha256"] == digest(value["plan"]), "qualification plan changed")
    criteria = value["plan"]["criteria"]
    require(set(value["criteria"]) == set(criteria) and len(value["commands"]) == len(criteria) + 3,
            "executed qualification coverage is incomplete")
    _candidate(value["candidate"], value)
    _observation(value["before"], value["candidate"], value["plan"])
    require(value["before"] == value["after"], "trial evidence changed")
    for criterion in criteria:
        result = value["criteria"][criterion]
        require(result["criterion"] == criterion and type(result["tests"]) is int and result["tests"] > 0
                and result["tests"] == result["passed"] and result["failed"] == result["skipped"] == 0,
                "criterion result is incomplete")
    require(all(c["exit_code"] == 0 and SHA.fullmatch(c["stdout_sha256"]) and SHA.fullmatch(c["stderr_sha256"])
                for c in value["commands"]), "execution receipts are invalid")
    require([(c["action"], c["criterion"]) for c in value["commands"]]
            == [("candidate", None), ("observe", None)] + [("criterion", c) for c in criteria] + [("observe", None)],
            "qualification command sequence is invalid")
    if current:
        expected = target(run_dir, value["execution_id"])
        frozen_plan(run_dir, value["execution_id"], value["plan"], expected)
        require(value["head"] == expected["head"] and value["basis_sha256"] == expected["basis_sha256"]
                and value["objective_sha256"] == expected["objective_sha256"]
                and value["inputs"] == expected["inputs"]
                and value["obligation_basis"] == expected["obligation_basis"], "qualification basis is stale")
        lease = run_dir.parents[2] / "qualification-leases" / digest(value["plan"]["environment"]["resource"])
        with lock(lease):
            observation, _proof = command(expected["project"], value["plan"], "observe")
        _observation(observation, value["candidate"], value["plan"])
        require(observation == value["after"], "live target changed since qualification")
    return value
