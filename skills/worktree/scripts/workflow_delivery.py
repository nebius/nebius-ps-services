"""SDLC-owned admission and recovery using the shared protected merge broker."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import quote

from workflow_state import WorkflowError, digest, lock, read, write
from workflow_objectives import _git, authorize_coordinator, check_dispatch
from workflow_program import accepted
from workflow_qualification import target, verify_receipt, require

CONTRACT = "workflow/sdlc-delivery-v1"


def broker():
    from workflow_intake import _load_skill_support
    modules = _load_skill_support("protected-merge", __file__, "worktree/scripts/workflow_delivery.py", source_only=True)
    return modules["merge_gate"], modules["merge_completion"]


def _authorize(run_dir, *, effects=False):
    from agent_runtime import native_session_id
    authorize_coordinator(run_dir, native_session_id(), owner="agentic-sdlc")
    if effects:
        check_dispatch(run_dir)


def _root(run_dir, execution_id):
    require(isinstance(execution_id, str) and re.fullmatch(r"SLICE-[0-9]{3,}", execution_id), "delivery requires a slice identity")
    return run_dir / "delivery" / execution_id


def _remote(api, completion):
    default = api.repo_info()["default_branch"]
    if completion["boundary"] == "merge-verified":
        require(api.repo == completion["repository"] and default == completion["base"], "repository/default changed outside accepted authority")
    value = api.api("contents/.github/merge-policy.json?ref=" + quote(default, safe=""))
    policy = json.loads(base64.b64decode(value["content"], validate=False))
    require(policy.get("schema") == "merge-policy/v1" and policy.get("sdlc_contract") == CONTRACT,
            "compatible protected SDLC workflows must already be installed")
    require(not any(r["type"] == "merge_queue" for r in api.pages("rules/branches/" + quote(default, safe=""))),
            "merge queues are not supported by SDLC delivery")
    effects = {policy["merge_workflow"], policy["completion_workflow"]} | {w["file"] for w in policy["ci_workflows"]}
    if policy.get("pages_enabled"):
        effects.add("pages")
    if completion["boundary"] == "merge-verified":
        require(effects <= set(completion["merge_effects"]), "merge-triggered effects exceed accepted authority")
    return policy


def _operators(api):
    value = json.loads(api.api("actions/variables/MERGE_OPERATOR_IDS")["value"])
    require(isinstance(value, list) and value and all(type(v) is int and v > 0 for v in value),
            "protected operator allowlist is missing or invalid")
    return value


def _scope(expected, execution_id, repository, *, merge=False):
    policy = expected["completion"]
    if merge:
        require(policy["boundary"] == "merge-verified" and execution_id in policy["delivery_scope"],
                "run is not opted in to merging this delivery slice")
    remote = _git(expected["project"], "remote", "get-url", "origin")
    require(remote in {f"https://github.com/{repository}.git", f"https://github.com/{repository}", f"git@github.com:{repository}.git"},
            "PR repository differs from the accepted source checkout")
    from workflow_interop import inspect_anchor
    require(inspect_anchor(expected["project"]).get("status") == "unmanaged",
            "unattended delivery requires the unmanaged source checkout")


def attest(run_dir, execution_id, review, qualification):
    _authorize(run_dir, effects=True)
    root = _root(run_dir, execution_id)
    with lock(run_dir.parent), lock(root):
        _authorize(run_dir, effects=True)
        require(not (root / "submission.json").exists(), "submitted deliveries cannot be re-attested")
        expected = target(run_dir, execution_id)
        receipt = verify_receipt(run_dir, qualification)
        require(receipt["execution_id"] == execution_id, "qualification belongs to another slice")
        _scope(expected, execution_id, review["repository"])
        gate, _completion = broker()
        api = gate.GitHub(review["repository"])
        policy = _remote(api, expected["completion"]) if expected["completion"]["boundary"] == "merge-verified" else None
        pr = api.api(f"pulls/{review['pr']}")
        require(review["head"] == expected["head"] and review["base_sha"] == pr["base"]["sha"], "review/promotion/base drift")
        if not receipt["plan"]["live"]:
            files = api.pages(f"pulls/{review['pr']}/files")
            require(len(files) == pr["changed_files"] and files
                    and all(f["filename"].endswith((".md", ".txt", ".rst"))
                            and not f["filename"].startswith((".github/", ".agents/", ".codex/")) for f in files),
                    "live N/A is limited to reviewed prose-only changes")
        evidence = {**review, "sdlc": {"schema": CONTRACT, "execution_id": execution_id,
                    "objective_sha256": expected["objective_sha256"], "basis_sha256": expected["basis_sha256"],
                    "qualification_sha256": qualification["sha256"], "candidate": receipt["candidate"],
                    "live": receipt["plan"]["live"], "criteria": sorted(receipt["criteria"])}}
        # The caller performed report-only review; the broker authenticates its
        # COMMENT attestation. This does not masquerade as a GitHub approval.
        _authorize(run_dir, effects=True)
        require(target(run_dir, execution_id) == expected, "review authority changed before publication")
        posted = gate.attest(api, review["pr"], evidence)
        value = {"schema": CONTRACT, "execution_id": execution_id, "status": "reviewed-pr",
                 "review_id": posted["id"], "review": evidence, "qualification": qualification,
                 "policy_sha256": digest(policy)}
        write(root / "review.json", value)
        return value


def submit(run_dir, execution_id):
    _authorize(run_dir, effects=True)
    root = _root(run_dir, execution_id)
    with lock(run_dir.parent), lock(root):
        _authorize(run_dir, effects=True)
        require(not (root / "submission.json").exists(), "submission already attempted; reconcile without resubmitting")
        expected = target(run_dir, execution_id)
        reviewed = read(root / "review.json")
        review = reviewed["review"]
        _scope(expected, execution_id, review["repository"], merge=True)
        receipt = verify_receipt(run_dir, reviewed["qualification"])
        require(receipt["execution_id"] == execution_id and review["sdlc"]["qualification_sha256"] == digest(receipt)
                and review["sdlc"]["objective_sha256"] == expected["objective_sha256"], "review admission is stale")
        gate, _completion = broker()
        api = gate.GitHub(review["repository"])
        policy = _remote(api, expected["completion"])
        require(digest(policy) == reviewed["policy_sha256"], "protected policy changed; review again")
        allowed = _operators(api)
        pr, _files, remote_review = gate.snapshot(api, review["pr"], expected["head"], policy, reviewed["review_id"], allowed_ids=allowed)
        require(pr["base"]["sha"] == review["base_sha"], "base drift requires corrective integration and fresh qualification")
        # Strict review_record validation binds this exact SDLC receipt remotely.
        require(remote_review == review, "remote review differs from the admitted evidence")
        actor = gate.gh(["api", "--hostname", "github.com", "user"])["id"]
        require(actor in allowed, "current operator is not admitted by the protected broker")
        value = {"schema": CONTRACT, "execution_id": execution_id, "status": "submission-attempted",
                 "repository": api.repo, "pr": review["pr"], "head": expected["head"],
                 "base": review["base"], "base_sha": review["base_sha"], "review_id": reviewed["review_id"],
                 "method": expected["completion"]["method"], "actor": actor,
                 "objective_sha256": expected["objective_sha256"], "review_sha256": digest(reviewed),
                 "policy": policy, "attempted_at": datetime.now(timezone.utc).isoformat(),
                 **gate.source_identity(pr)}
        _authorize(run_dir, effects=True)
        require(target(run_dir, execution_id) == expected, "merge authority changed before dispatch")
        write(root / "submission.json", value, exclusive=True)
        response = api.api(f"actions/workflows/{policy['merge_workflow']}/dispatches", "POST",
                           {"ref": value["base"], "inputs": {k: str(value[k]) for k in ("pr", "head", "review_id", "method")}})
        require(type(response.get("workflow_run_id")) is int and response["workflow_run_id"] > 0,
                "dispatch outcome is ambiguous; reconcile without resubmitting")
        value.update(status="submitted", run_id=response["workflow_run_id"], run_attempt=1)
        write(root / "submission.json", value)
        return value


def _match_intent(intent, submission):
    require(all(intent.get(k) == submission.get(k) for k in
                ("repository", "pr", "head", "base", "base_sha", "review_id", "method",
                 "base_repository_id", "head_repository_id", "head_repository", "head_ref")),
            "broker intent does not match the exact submitted delivery")


def reconcile(run_dir, execution_id):
    _authorize(run_dir)
    root = _root(run_dir, execution_id)
    with lock(root):
        value = read(root / "submission.json")
        gate, completion = broker()
        api = gate.GitHub(value["repository"])
        policy = value["policy"]
        allowed = _operators(api)
        if not value.get("run_id"):
            matches = []
            runs = api.pages(f"actions/workflows/{policy['merge_workflow']}/runs?event=workflow_dispatch", "workflow_runs")
            for run in runs:
                if (run["created_at"] < value["attempted_at"][:19] + "Z" or run["actor"]["id"] != value["actor"]
                        or run["head_branch"] != value["base"]):
                    continue
                try:
                    intent = completion.read_intent(api, run["id"], run["run_attempt"], value["pr"], value["head"], policy, allowed_ids=allowed)
                    _match_intent(intent, value)
                except (gate.Blocked, gate.Pending, WorkflowError):
                    continue
                matches.append((run["id"], run["run_attempt"]))
            require(len(matches) == 1, "ambiguous submission remains unverified; do not repeat dispatch")
            value.update(run_id=matches[0][0], run_attempt=matches[0][1], status="submitted")
            write(root / "submission.json", value)
        intent = completion.read_intent(api, value["run_id"], value["run_attempt"], value["pr"], value["head"], policy, allowed_ids=allowed)
        _match_intent(intent, value)
        reviewed = read(root / "review.json")
        require(digest(reviewed) == value["review_sha256"] and intent.get("sdlc") == reviewed["review"]["sdlc"],
                "protected intent differs from the admitted qualification")
        try:
            result = completion.merged_result(api, intent)
        except gate.Pending:
            return {"status": "submitted", "execution_id": execution_id}
        value.update(status="merged", result_sha=result)
        write(root / "submission.json", value)
        try:
            ci = completion.verify_or_dispatch(api, intent, policy, dispatch=False)
            require(ci["outcome"] == "ci-verified", "merged result checks are pending")
            pages = completion.verify_pages(api, intent) if policy.get("pages_enabled") else {"outcome": "not-applicable"}
            require(pages["outcome"] in {"pages-verified", "not-applicable"}, "merged result publication is pending")
        except (gate.Blocked, gate.Pending, WorkflowError):
            value.update(status="merged-verification-failed")
            write(root / "submission.json", value)
            raise WorkflowError("MERGED_VERIFICATION_FAILED", "merged, verification failed or pending; dependent delivery is paused") from None
        reviewed = read(root / "review.json")
        require(digest(reviewed) == value["review_sha256"], "review receipt changed after submission")
        record = {"schema": "workflow/delivery-completion-v1", "execution_id": execution_id,
                  "status": "result-verified", "submission_sha256": digest(value), "intent": intent,
                  "review": reviewed, "ci": ci, "pages": pages, "result_sha": result}
        write(root / "completion.json", record)
        return record


def verify_completion(run_dir, value):
    root = _root(run_dir, value["execution_id"])
    require(value.get("schema") == "workflow/delivery-completion-v1" and value.get("status") == "result-verified",
            "slice completion lacks verified merge results")
    submission = read(root / "submission.json")
    require(digest(submission) == value["submission_sha256"] and submission["status"] == "merged"
            and value["result_sha"] == submission["result_sha"] == value["ci"]["result_sha"]
            and value["ci"]["outcome"] == "ci-verified"
            and value["pages"]["outcome"] in {"pages-verified", "not-applicable"}, "merged-result evidence changed")
    _match_intent(value["intent"], submission)
    require(digest(value["review"]) == submission["review_sha256"], "review evidence changed")
    verify_receipt(run_dir, value["review"]["qualification"], current=False)
    return value


def phase_exit(run_dir, phase_id, qualifications):
    _authorize(run_dir)
    run, _latest, snapshot = accepted(run_dir)
    from workflow_program import verify_inputs
    program = verify_inputs(run_dir, Path(run["project_root"]))["definition"]
    phase = next((p for p in program["phases"] if p["id"] == phase_id), None)
    require(phase is not None, "unknown phase exit")
    slices = [s["id"] for s in program["slices"] if s["phase"] == phase_id]
    for slice_id in slices:
        verify_completion(run_dir, read(_root(run_dir, slice_id) / "completion.json"))
    value = {"schema": "workflow/phase-exit-v1", "phase": phase_id, "definition_sha256": digest(phase),
             "inputs": snapshot["inputs"], "qualifications": qualifications, "slices": slices}
    verify_phase_exit(run_dir, value, phase)
    write(run_dir / "program" / "phase-exits" / (phase_id + ".json"), value)
    return value


def verify_phase_exit(run_dir, value, phase):
    inputs = accepted(run_dir)[2]["inputs"]
    program = read(run_dir / "program" / (inputs["program_sha256"] + ".json"))
    require(digest(program) == inputs["program_sha256"], "phase program changed")
    phases = {p["id"]: p for p in program["phases"]}
    pending, verified = [(value, phase)], set()
    while pending:
        record, definition = pending.pop()
        require(phases.get(definition["id"]) == definition, "phase definition differs from its accepted program")
        if definition["id"] in verified:
            continue
        _verify_phase_record(run_dir, record, definition, program)
        verified.add(definition["id"])
        for parent in definition["depends_on"]:
            pending.append((read(run_dir / "program/phase-exits" / (parent + ".json")), phases[parent]))


def _verify_phase_record(run_dir, value, phase, program):
    require(value.get("schema") == "workflow/phase-exit-v1" and value.get("phase") == phase["id"]
            and value.get("definition_sha256") == digest(phase),
            "phase exit basis is stale")
    require(value["slices"] == [s["id"] for s in program["slices"] if s["phase"] == phase["id"]],
            "phase completion omits frozen slice membership")
    require(isinstance(value["qualifications"], list) and value["qualifications"], "phase has no executed exit evidence")
    from workflow_program import verify_inputs
    run = accepted(run_dir)[0]
    current_basis = verify_inputs(run_dir, Path(run["project_root"]))["bundle"]["obligation_basis"]
    covered = set()
    for reference in value["qualifications"]:
        receipt = verify_receipt(run_dir, reference, current=False)
        require(receipt["execution_id"] in value["slices"] + ["PHASE:" + phase["id"]], "phase evidence belongs elsewhere")
        covered.update(c for c in receipt["criteria"] if receipt["obligation_basis"].get(c) == current_basis.get(c))
    require(set(phase["exit_criteria"]) <= covered, "phase exit obligations are incomplete")
    for slice_id in value["slices"]:
        verify_completion(run_dir, read(_root(run_dir, slice_id) / "completion.json"))


def prepare_source(run_dir, project, execution_id):
    """After verified delivery only, start the next candidate from current default."""
    from workflow_program import verify_inputs
    program = verify_inputs(run_dir, project)
    if not program:
        return
    run, _latest, snapshot = accepted(run_dir)
    slices = program["definition"]["slices"]
    index = next(i for i, row in enumerate(slices) if row["id"] == execution_id)
    if index == 0:
        require(_git(project, "branch", "--show-current") == run["source_branch"], "initial selected source branch changed")
        _git(project, "merge-base", "--is-ancestor", run["source_head"], "HEAD")
        return
    prior = read(_root(run_dir, slices[index - 1]["id"]) / "completion.json")
    verify_completion(run_dir, prior)
    require(not _git(project, "status", "--porcelain"), "next delivery source must be clean")
    default = prior["intent"]["base"]
    branch = f"codex/sdlc-{run_dir.name}-{execution_id.lower()}"
    pointer = run_dir / "delivery" / execution_id / "source.json"
    if pointer.exists():
        identity = read(pointer)
        require(_git(project, "branch", "--show-current") == branch
                and _git(project, "rev-parse", "HEAD") == identity["head"]
                and identity["prior_completion_sha256"] == digest(prior), "next source branch changed")
        return
    _scope({"project": project, "completion": snapshot["completion"]}, execution_id,
           snapshot["completion"].get("repository", ""), merge=True)
    coordinator = read(run_dir / "execution" / slices[index - 1]["id"] / "coordinator.json")
    original = {"branch": coordinator["base_branch"], "head": coordinator["promoted_head"]}
    intent_path = pointer.with_name("source-intent.json")
    if intent_path.exists():
        intent = read(intent_path)
        require(intent["prior_completion_sha256"] == digest(prior) and intent["source"] == original
                and intent["branch"] == branch and intent["default"] == default, "next-source transaction changed")
    else:
        require(_git(project, "branch", "--show-current") == original["branch"]
                and _git(project, "rev-parse", "HEAD") == original["head"], "SOURCE_DRIFT: prior delivered checkout changed")
        from git_promotion import resolve_remote_default
        remote = resolve_remote_default(project)
        require(remote["default_branch"] == default == snapshot["completion"]["base"], "remote default changed outside accepted scope")
        _git(project, "merge-base", "--is-ancestor", prior["result_sha"], remote["default_head"])
        intent = {"source": original, "branch": branch, "head": remote["default_head"], "default": default,
                  "prior_completion_sha256": digest(prior)}
        write(intent_path, intent, exclusive=True)
    current = {"branch": _git(project, "branch", "--show-current"), "head": _git(project, "rev-parse", "HEAD")}
    require(current in (original, {"branch": branch, "head": intent["head"]}), "source drift during next-branch recovery")
    if current["branch"] != branch:
        require(not _git(project, "branch", "--list", branch), "unexpected existing delivery branch")
        _git(project, "switch", "-c", branch, intent["head"])
    write(pointer, intent, exclusive=True)
