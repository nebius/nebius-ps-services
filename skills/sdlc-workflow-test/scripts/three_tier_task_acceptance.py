"""Owned early-acceptance fixture setup and independent receipt observation."""
import hashlib
import json
from pathlib import Path


def seed(project):
    source = Path(__file__).resolve().parents[1] / "assets/task_domain_acceptance.py"
    target = project / "project-a/acceptance_fixture/task_domain_acceptance.py"
    target.parent.mkdir()
    target.write_bytes(source.read_bytes())


def prepare(state, command, write_json, *, python_base, ownership_label, compose_label):
    directory = Path(state["private_root"]) / "task-acceptance-base"
    directory.mkdir(mode=0o700, exist_ok=True)
    base = command(["docker", "image", "inspect", python_base])
    if base.returncode:
        raise RuntimeError("Task acceptance Python base is unavailable")
    metadata = json.loads(base.stdout)[0]
    image_name = "sdlc-task-acceptance:" + state["verification_id"]
    dockerfile = directory / "Dockerfile"
    digests = metadata.get("RepoDigests", [])
    if not digests:
        raise RuntimeError("Task acceptance base lacks an immutable repository digest")
    dockerfile.write_text("FROM " + digests[0] + "\n"
                          "RUN mkdir -p /workspace /evidence && chmod 1777 /workspace /evidence\n"
                          "USER 10001\n")
    built = command(["docker", "build", "--pull=false", "--tag", image_name,
                     "--label", ownership_label + "=" + state["verification_id"],
                     "--label", compose_label + "=" + state["compose_project"], str(directory)], timeout=None)
    if built.returncode:
        raise RuntimeError("Owned task acceptance dependency image failed to build")
    inspected = command(["docker", "image", "inspect", image_name])
    if inspected.returncode:
        raise RuntimeError("Owned task acceptance image cannot be inspected")
    image = json.loads(inspected.stdout)[0]
    labels = image["Config"].get("Labels", {})
    if (labels.get(ownership_label) != state["verification_id"]
            or labels.get(compose_label) != state["compose_project"] or image["Config"].get("Volumes")):
        raise RuntimeError("Task acceptance image ownership or isolation changed")
    harness = Path(state["selected_project_root"]) / "acceptance_fixture/task_domain_acceptance.py"
    sha = hashlib.sha256(harness.read_bytes()).hexdigest()
    profile = {"kind": "container", "image": image["Id"], "platform": image["Os"] + "/" + image["Architecture"],
               "uid": 10001, "collect_argv": ["python", "-B", "acceptance_fixture/task_domain_acceptance.py", "collect", "title"],
               "run_argv": ["python", "-B", "acceptance_fixture/task_domain_acceptance.py", "run", "title"],
               "execution_policy": {"observation_interval_seconds": 30, "deadline": None}, "suite_sha256": sha,
               "harness": {"acceptance_fixture/task_domain_acceptance.py": sha},
               "runner": {"name": "stdlib-acceptance", "version": "1"},
               "resource": "task-acceptance-" + state["verification_id"] + "-title"}
    path = Path(state["private_root"]) / "task-acceptance-profile.json"
    if path.exists() and json.loads(path.read_text()) != profile:
        raise RuntimeError("Frozen task acceptance profile changed; start a new fixture trial")
    write_json(path, profile)
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "image": image["Id"]}


def verify_final_harness(repository, head, scope, profile):
    """A later task must not weaken the independent deferred-check oracle."""
    from workflow_task_acceptance import source_entries
    expected = {str(scope / name): sha for name, sha in profile["harness"].items()}
    seen = set()
    for name, mode, content in source_entries(repository, head):
        if name not in expected:
            continue
        if mode not in {"100644", "100755"} or hashlib.sha256(content).hexdigest() != expected[name]:
            raise RuntimeError("Final candidate changed the independently frozen acceptance harness")
        seen.add(name)
    if seen != set(expected):
        raise RuntimeError("Final candidate omitted the independently frozen acceptance harness")


def observe(run_dir, repository, promoted, aggregate, profile):
    from workflow_state import read
    from workflow_task_acceptance import assignment_policy, obligations, require_final, _verify_produced, _root, _task_key
    from workflow_execution import _validate_assignment_record, _validate_result_record, result_path, wave_path, _wave_satisfied
    containers, proofs = [], []
    images = set()
    run = read(run_dir / "run.json")
    scope = Path(run["project_root"]).relative_to(Path(run["git_root"]))
    for head in (promoted, aggregate):
        verify_final_harness(repository, head, scope, profile)
    for path in sorted((run_dir / "execution").glob("*/assignments/*/*.json")):
        assignment = read(path)
        _validate_assignment_record(assignment)
        wave = read(wave_path(run_dir, assignment["execution_id"], assignment["wave_id"]))
        if wave["status"] == "superseded":
            coordinator = read(run_dir / "execution" / assignment["execution_id"] / "coordinator.json")
            if not _wave_satisfied(run_dir, assignment["execution_id"], coordinator, assignment["wave_id"]):
                raise RuntimeError("Superseded acceptance assignment has unresolved lifecycle evidence")
            continue
        policy = assignment_policy(assignment)
        checks = [c for c in policy["criteria"] if c["disposition"] == "check" and c["check"]["kind"] == "container"]
        if not checks:
            continue
        if any(c["check"] != profile for c in checks):
            raise RuntimeError("Early check differs from the independently frozen fixture profile")
        result = read(result_path(run_dir, assignment["execution_id"], assignment["wave_id"], assignment["task_id"]))
        _validate_result_record(result, assignment)
        reference = read(_root(run_dir) / "tasks" / (_task_key(assignment, result["attempt"]) + ".json"))
        receipt = _verify_produced(Path(reference["path"]))
        proofs.append(result["task_acceptance_receipt_sha256"])
        for row in receipt["observations"]:
            proof = row["observation"]
            if "collection" not in proof:
                continue
            containers.extend([proof["collection"]["container_id"], proof["execution"]["container_id"]])
            images.add(proof["execution"]["image"])
    debt = obligations(run_dir)
    if len(proofs) < 2 or not debt or len(containers) != len(set(containers)) or len(images) != 1:
        raise RuntimeError("Early container checks, isolated trials, shared immutable base, or deferred coverage is missing")
    expected = ["{python}", "-B", "acceptance_fixture/task_domain_acceptance.py", "check", "filter"]
    if not any(c["check"]["kind"] == "command" and c["check"]["definition"]["argv"] == expected for c in debt.values()):
        raise RuntimeError("Required deferred filtering criterion is missing")
    require_final(run_dir, repository, promoted)
    require_final(run_dir, repository, aggregate)
    return {"task_receipts": proofs, "isolated_containers": len(containers), "base_images": sorted(images),
            "deferred_criteria": sorted(debt), "promoted_head": promoted, "aggregate_head": aggregate,
            "outcome": "passed"}
