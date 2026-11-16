"""Narrow Docker adapter for frozen SDLC checks; no image builds or cloud actions.

The project owns its reviewed collection/run harness and prebuilt dependency
image. The workflow owns fresh containers, process observations and cleanup.
"""
from __future__ import annotations

from contextlib import nullcontext
import hashlib
import io
import json
import os
import shutil
from functools import partial
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile

from workflow_task_acceptance import require, SHA
from workflow_validation import digest, _run_check
from workflow_process import run_owned, Capture, admit_execution_policy, ProcessCleanupError


def support():
    directory = Path(__file__).resolve().parents[2] / "test-software-in-container/scripts"
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    from tsc_support.suite_contracts import validate_inventory, assess_receipt
    return validate_inventory, assess_receipt


def validate_check(check):
    require(set(check) == {"kind", "image", "platform", "uid", "collect_argv", "run_argv",
                          "execution_policy", "suite_sha256", "harness", "runner", "resource"},
            "container check fields are invalid")
    require(re.fullmatch(r"sha256:[a-f0-9]{64}", str(check["image"]))
            and check["platform"] in {"linux/amd64", "linux/arm64"}
            and type(check["uid"]) is int and 0 < check["uid"] <= 65535
            and SHA.fullmatch(str(check["suite_sha256"])), "container identity or limits are invalid")
    try:
        admit_execution_policy(check["execution_policy"])
    except ValueError as error:
        require(False, str(error))
    require(isinstance(check["resource"], str)
            and re.fullmatch(r"[a-z0-9][a-z0-9._:-]{0,127}", check["resource"]), "invalid runtime lease identity")
    for key in ("collect_argv", "run_argv"):
        require(isinstance(check[key], list) and check[key]
                and all(isinstance(arg, str) and arg and "\0" not in arg for arg in check[key]),
                "container commands must be explicit argv")
    require(isinstance(check["harness"], dict) and check["harness"], "reviewed harness hashes are required")
    for name, sha in check["harness"].items():
        require(isinstance(name, str) and not Path(name).is_absolute() and ".." not in Path(name).parts
                and SHA.fullmatch(str(sha)), "harness path or identity is invalid")
    require(isinstance(check["runner"], dict) and set(check["runner"]) == {"name", "version"}
            and all(isinstance(v, str) and v for v in check["runner"].values()), "runner identity is required")


def _docker_output(*arguments, client=None, limit=1048576):
    """Bound protocol memory without imposing a healthy-operation expiry."""
    observation = run_owned([*(client or ["docker"]), *arguments], stdout_capture=Capture(limit))
    try:
        require(observation["local_group_quiescent"], "owned container client termination is unproven")
        require(observation["stop_reason"] == "exited" and observation["exit_code"] == 0,
                "owned container command failed")
        output = observation["stdout"]["data"]
        require(output is not None, "owned container output is incomplete or exceeds its protocol bound")
    except BaseException as error:
        error.observation = observation
        raise
    return output


def _docker(*arguments, client=None):
    return _docker_output(*arguments, client=client).decode()


def _receipt(cid, *, client=None):
    # Docker cp to stdout yields an uncompressed tar stream. Never extract it
    # onto the host: validate the bounded archive and its sole regular member.
    raw = _docker_output("cp", cid + ":/evidence/result.json", "-", client=client, limit=1048576 + 65536)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        members = archive.getmembers()
        require(len(members) == 1, "container evidence must contain one file")
        member = members[0]
        require(member.name == "result.json" and member.isreg() and member.sparse is None
                and 0 <= member.size <= 1048576, "container evidence is unsafe or oversized")
        return json.loads(archive.extractfile(member).read(1048576 + 1))


def _client():
    context = _docker("context", "show").strip()
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", context), "invalid Docker context")
    configuration = str(Path(os.environ.get("DOCKER_CONFIG", str(Path.home() / ".docker"))).resolve())
    executable = shutil.which("docker")
    require(executable is not None, "Docker executable is unavailable")
    client = [executable, "--config", configuration, "--context", context]
    metadata = json.loads(_docker("context", "inspect", context, client=client))[0]
    require(metadata["Endpoints"]["docker"]["Host"].startswith("unix://"), "task trials require a local Docker endpoint")
    return client


def _source(root, tree, destination):
    from workflow_task_acceptance import source_entries
    for name, mode, contents in source_entries(root, tree):
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if mode == "120000":
            target = contents.decode()
            require((path.parent / target).resolve().is_relative_to(destination.resolve()), "source symlink escapes snapshot")
            path.symlink_to(target)
        else:
            path.write_bytes(contents)
            path.chmod(0o755 if mode == "100755" else 0o644)
    # docker cp preserves modes but gives copied paths root ownership. Exported
    # directories must remain traversable by the declared nonroot container UID.
    # The enclosing TemporaryDirectory stays private to the host caller.
    destination.chmod(0o755)
    for path in destination.rglob("*"):
        if not path.is_symlink() and path.is_dir():
            path.chmod(0o755)


def _phase(check, source, scope, directory, operation, phase, inputs, inventory=None, *, client=None):
    docker = partial(_docker, client=client)
    name = "sdlc-acceptance-" + operation + "-" + phase
    argv = check["collect_argv" if phase == "collect" else "run_argv"]
    environment = {"SDLC_ACCEPTANCE_PHASE": phase, "SDLC_ACCEPTANCE_INPUT_SHA256": inputs,
                   "SDLC_ACCEPTANCE_SUITE_SHA256": check["suite_sha256"],
                   "SDLC_ACCEPTANCE_SELECTION_SHA256": digest(check),
                   "SDLC_ACCEPTANCE_CASE_ID": operation, "SDLC_ACCEPTANCE_PLAN_SHA256": digest(check),
                   "SDLC_ACCEPTANCE_OUTPUT": "/evidence/result.json",
                   "SDLC_ACCEPTANCE_INVENTORY": "/evidence/inventory.json"}
    args = ["create", "--name", name, "--label", "workflow.acceptance=" + operation,
            "--pull", "never", "--platform", check["platform"], "--network", "none",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges=true", "--pids-limit", "128",
            "--memory", "1g", "--cpus", "1", "--user", str(check["uid"]), "--no-healthcheck",
            "--workdir", "/workspace" + ("/" + scope if scope != "." else ""),
            "--entrypoint", argv[0]]
    for key, value in environment.items():
        args.extend(["--env", key + "=" + value])
    args.extend([check["image"], *argv[1:]])
    cid = None
    failure = None
    try:
        cid = docker(*args).strip()
        require(re.fullmatch(r"[a-f0-9]{64}", cid), "container creation returned no exact identity")
        inspected = json.loads(docker("inspect", cid))[0]
        require(inspected["Image"] == check["image"] and not inspected["Mounts"]
                and inspected["Config"]["Labels"].get("workflow.acceptance") == operation,
                "container identity or isolation differs from declaration")
        docker("cp", str(source) + "/.", cid + ":/workspace")
        if inventory is not None:
            inventory_path = directory / "inventory.json"
            inventory_path.write_text(json.dumps(inventory))
            inventory_path.chmod(0o644)
            docker("cp", str(inventory_path), cid + ":/evidence/inventory.json")
        # The caller owns physical-host monitoring, including for sandboxed
        # workers that cannot probe the host. Keep process and container cleanup
        # here; a second sandbox-local memory probe cannot replace that owner.
        process = _run_check({"id": phase, "argv": [*(client or ["docker"]), "start", "--attach", cid],
                              "execution_policy": check["execution_policy"], "environment_keys": ["PATH"]}, directory)
        after = json.loads(docker("inspect", cid))[0]
        state = after["State"]
        require(not state["Running"] and not state.get("OOMKilled") and not state.get("Error"),
                "container process did not finish normally")
        require(state.get("Status") == "exited",
                f"container launch did not complete (command exit {process.get('exit_code')})")
        result = _receipt(cid, client=client)
        return result, {"container_id": cid, "image": after["Image"], "process": process,
                        "exit_code": state["ExitCode"], "fresh_writable_state": True}
    except BaseException as error:
        failure = error
        raise
    finally:
        # A lost create acknowledgement is not proof of backend absence. Keep
        # the operation/resource reservation if exact reconciliation is unknown.
        try:
            selector = "id=" + cid if isinstance(cid, str) and re.fullmatch(r"[a-f0-9]{64}", cid) else "name=^/" + name + "$"
            exact_ids = docker("ps", "--all", "--no-trunc", "--filter", selector, "--format", "{{.ID}}").splitlines()
            require(len(exact_ids) <= 1, "container cleanup name is ambiguous")
            owned = json.loads(docker("inspect", exact_ids[0])) if exact_ids else []
            require(owned or isinstance(cid, str) and re.fullmatch(r"[a-f0-9]{64}", cid),
                    "container create acknowledgement is unknown; retain exact-name reservation")
            if owned:
                require(owned[0]["Config"].get("Labels", {}).get("workflow.acceptance") == operation,
                        "cleanup target ownership changed")
                exact = owned[0]["Id"]
                docker("rm", "--force", exact)
                remaining = json.loads(docker("ps", "--all", "--no-trunc", "--filter", "id=" + exact,
                                              "--format", "json") or "null")
                require(remaining is None, "owned container cleanup is unproven")
            if failure is not None:
                observed = getattr(failure, "observation", {})
                failure.observation = {**observed, "cleanup_proven": observed.get("local_group_quiescent") is not False}
        except BaseException as error:
            cleanup_error = ProcessCleanupError("owned container cleanup is unproven; exact-name reservation retained")
            cleanup_error.observation = {"cleanup_proven": False, "local_group_quiescent": False}
            raise cleanup_error from error


def assess(check, inventory, receipt, inputs, operation):
    validate_inventory, assess_receipt = support()
    validate_inventory(inventory)
    require(inventory["runner"] == check["runner"] and inventory["suite_sha256"] == check["suite_sha256"]
            and inventory["selection_sha256"] == digest(check), "collection differs from frozen suite")
    suite = {"kind": "acceptance", "sha256": check["suite_sha256"], "inventory": inventory, "exemptions": []}
    return assess_receipt(suite, receipt, "container", inputs, case_id=operation, plan_sha256=digest(check))


def run(check, cwd, tree, operation, *, assignment=None, session=None):
    validate_check(check)
    from workflow_resources import acquire, consume, release
    lease = None
    cleanup_proven = True  # No backend writer has been admitted yet.
    if assignment is not None:
        from workflow_state import read
        home = Path(read(assignment)["run_dir"]).parents[2]
        lease = acquire(home, assignment, check["resource"], session)
    try:
        with consume(home, assignment, check["resource"], session) if lease else nullcontext():
            client = _client()
            inspected = json.loads(_docker("image", "inspect", check["image"], client=client))[0]
            require(inspected["Id"] == check["image"] and inspected["Os"] + "/" + inspected["Architecture"] == check["platform"]
                    and not inspected["Config"].get("Volumes"), "base image identity or platform differs")
            root = Path(subprocess.check_output(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"], text=True).strip())
            scope = str(cwd.relative_to(root))
            inputs = digest({"tree": tree, "check": check, "scope": scope})
            with tempfile.TemporaryDirectory(prefix="sdlc-acceptance-") as temporary:
                directory = Path(temporary)
                source = directory / "source"
                source.mkdir()
                _source(root, tree, source)
                for name, sha in check["harness"].items():
                    path = source / scope / name
                    require(path.is_file() and not path.is_symlink()
                            and hashlib.sha256(path.read_bytes()).hexdigest() == sha, "reviewed harness changed")
                cleanup_proven = False
                inventory, collection = _phase(check, source, scope, directory, operation, "collect", inputs, client=client)
                cleanup_proven = True
                require(collection["exit_code"] == 0 and collection["process"]["outcome"] == "passed",
                        "independent inventory collection failed")
                support()[0](inventory)
                cleanup_proven = False
                receipt, execution = _phase(check, source, scope, directory, operation, "run", inputs, inventory, client=client)
                cleanup_proven = True
                require(collection["container_id"] != execution["container_id"], "collection reused writable state")
                receipt.update(process_completed=True, process_exit_code=execution["exit_code"],
                               interrupted=execution["process"]["exit_code"] is None)
                assessment = assess(check, inventory, receipt, inputs, operation)
                cleanup_proven = True
                return {"outcome": "passed" if assessment["status"] == "PASS" else "failed",
                        "assessment": assessment, "inventory": inventory, "receipt": receipt,
                        "input_sha256": inputs, "operation": operation, "collection": collection,
                        "execution": execution, "cleanup_proven": True}
    except BaseException as error:
        cleanup_proven = cleanup_proven or getattr(error, "observation", {}).get("cleanup_proven") is True
        error.observation = {"cleanup_proven": cleanup_proven}
        raise
    finally:
        # Release only after _phase has independently established resource absence.
        if lease and cleanup_proven:
            release(home, assignment, check["resource"], session)
