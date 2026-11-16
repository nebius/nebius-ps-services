"""Hosted job boundaries: trusted preparation, disposable evaluation, trusted ingestion."""

from __future__ import annotations

import copy
import json
import os
import re
import time
from pathlib import Path

from . import requests, scheduler
from .contracts import (
    AdoptionLiveCoverage,
    AdoptionPlan,
    Applicability,
    Blocked,
    Candidate,
    Diagnosis,
    GateAssessment,
    IssueAssessment,
    Producer,
    Review,
    Scenario,
    Usefulness,
    digest,
)
from .controller import Heartbeat, process, trusted_runtime
from .live import execute_live, require_cleanup
from .policy import policy_digest, qualification, read_policy, validate_scenario
from .qualification import ReadOnlyGitHub
from .rollout import key as rollout_key
from .source import snapshot
from .stages import human_context, public_text
from .state import acquire


def execution_key(repository_id, run_id, attempt):
    return f"repositories/{repository_id}/executions/{run_id}_{attempt}"


class SnapshotStore:
    """CAS simulation private to one worker; contains no storage credentials."""

    def __init__(self, values):
        self.values = copy.deepcopy(values)

    def read(self, key):
        value = self.values.get(key)
        return (copy.deepcopy(value), digest(value)) if value is not None else (None, None)

    def put(self, key, value, expected):
        if self.read(key)[1] != expected:
            raise Blocked("STATE_CONFLICT")
        self.values[key] = copy.deepcopy(value)
        return digest(value)

    def scan(self, prefix):
        return sorted(k for k in self.values if k.startswith(prefix + "/"))


def prepare(api, store, root, number, operation, request_id, nonce, env=os.environ):
    repo = trusted_runtime(api, env)
    if env.get("AI_AUTOMATION_PR_REVIEW", "disabled") == "disabled":
        return {"ready": False, "code": "AUTOMATION_DISABLED"}
    from .images import selected

    selected(api, root, env, store=store)
    candidate, pr = api.candidate(number)
    if not request_id:
        event = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_text())
        record = requests.admit_manual(api, store, candidate, pr, operation, event, env)
        if not record:
            return {"ready": False, "code": "REQUEST_ALREADY_COMPLETE"}
        request_id = record["id"]
        if record["candidate"]["pr_number"] != number:
            number = record["candidate"]["pr_number"]
            candidate, pr = api.candidate(number)
    record = requests.require_capability(store, candidate, request_id, "assess", env=env)
    if operation != record["operation"]:
        raise Blocked("REQUEST_OPERATION_CHANGED")
    require_cleanup(store, candidate)
    run_id, attempt = int(env["GITHUB_RUN_ID"]), int(env["GITHUB_RUN_ATTEMPT"])
    owner = record.get("active_execution")
    prior_terminal = False
    if owner and owner != {"run_id": run_id, "attempt": attempt}:
        prior_run = api.request(f"actions/runs/{owner['run_id']}/attempts/{owner['attempt']}")
        if prior_run["status"] != "completed":
            return {"ready": False, "code": "REQUEST_EXECUTION_ACTIVE"}
        prior_terminal = True
    row = scheduler.enqueue(
        store,
        {**record, "candidate": candidate.model_dump()},
        retry_completed=prior_terminal and not nonce,
    )
    if nonce:
        if row["nonce"] != nonce:
            raise Blocked("EVALUATION_DISPATCH_CHANGED")
        slot = scheduler.bind(store, repo["id"], nonce, run_id, attempt)
    else:
        slot = scheduler.reserve(store, repo["id"], row, run_id=run_id, attempt=attempt)
        if slot is None:
            return {"ready": False, "code": "EVALUATION_QUEUED"}
        nonce = slot["nonce"]
        scheduler.bind(store, repo["id"], nonce, run_id, attempt)
    if (slot["run_id"], slot["attempt"]) != (run_id, attempt):
        return {"ready": False, "code": "EVALUATION_ALREADY_RESERVED"}
    lease = acquire(store, candidate.key + "/lock")
    try:
        record = requests.require_capability(store, candidate, request_id, "assess", env=env)
        owner = record.get("active_execution")
        if owner and owner != {"run_id": run_id, "attempt": attempt}:
            previous_run = api.request(
                f"actions/runs/{owner['run_id']}/attempts/{owner['attempt']}"
            )
            if previous_run["status"] != "completed":
                raise Blocked("REQUEST_EXECUTION_ACTIVE")
        record = {
            **record,
            "candidate": candidate.model_dump(),
            "active_execution": {"run_id": run_id, "attempt": attempt},
            "rationale_sha256": digest(human_context(api, number, pr)),
            "state_events": [
                e
                for e in api.pages(f"issues/{number}/events")
                if e.get("event") in {"closed", "reopened"}
            ],
        }
        _, operation_revision = store.read(candidate.key + "/operation")
        store.put(candidate.key + "/operation", record, operation_revision)
        values = {
            candidate.key + "/operation": record,
            rollout_key(repo["id"]): store.read(rollout_key(repo["id"]))[0],
        }
        for path in store.scan(candidate.key + "/requests/" + request_id):
            if "/stages/" in path or "/ci-requests/" in path:
                values[path] = store.read(path)[0]
        if record.get("adoption_parent"):
            binding = record["adoption_parent"]
            parent = Candidate.model_validate(binding["candidate"])
            for extra in (parent.key + "/operation", binding["record"] + "/adoption"):
                values[extra] = store.read(extra)[0]
        waiver, _ = store.read(candidate.key + "/waiver")
        if waiver:
            values[candidate.key + "/waiver"] = {**waiver, "reason": "Recorded human waiver"}
        settings = {
            name: env.get(name, "")
            for name in (
                "AI_AUTOMATION_PR_REVIEW",
                "AI_AUTOMATION_MERGE_FORK_CONTRIBUTION",
                "AI_AUTOMATION_AGENT_IMAGE",
                "AI_AUTOMATION_AGENT_IMAGE_REF",
                "TF_MODEL_REVIEW",
                "TF_MODEL_FIX",
            )
        }
        from .issue_resolution import collect

        issues = collect(api, number)
        if record.get("adoption_parent"):
            issues += collect(api, record["adoption_parent"]["candidate"]["pr_number"])
            issues = list(
                {
                    (i["identity"]["repository"], i["identity"]["issue_id"]): i for i in issues
                }.values()
            )
        value = {
            "issues": issues,
            "schema": "evaluation-input/v2",
            "candidate": candidate.model_dump(),
            "request_id": request_id,
            "operation": operation,
            "nonce": nonce,
            "run_id": run_id,
            "attempt": attempt,
            "workflow_sha": env["GITHUB_SHA"],
            "settings": settings,
            "state": values,
            "rationale": human_context(api, number, pr),
            "policy_sha256": policy_digest(
                root, read_policy(root / "github-ai-automation/config/policy.json")
            ),
        }
        if len(json.dumps(value).encode()) > 700_000:
            raise Blocked("EVALUATION_INPUT_TOO_LARGE")
        path = execution_key(repo["id"], run_id, attempt)
        existing, revision = store.read(path)
        if existing and existing["input"] != value:
            raise Blocked("EVALUATION_INPUT_CHANGED")
        if not existing:
            store.put(
                path,
                {"input": value, "input_sha256": digest(value), "status": "prepared"},
                revision,
            )
        return {"ready": True, "code": "EVALUATION_PREPARED", "input": value}
    finally:
        lease.release()


def evaluate(api, root, scratch, value, env=os.environ):
    if not isinstance(api, ReadOnlyGitHub) or value.get("schema") != "evaluation-input/v2":
        raise Blocked("EVALUATION_INPUT_INVALID")
    trusted_runtime(api, env)
    if (value["run_id"], value["attempt"], value["workflow_sha"]) != (
        int(env["GITHUB_RUN_ID"]),
        int(env["GITHUB_RUN_ATTEMPT"]),
        env["GITHUB_SHA"],
    ):
        raise Blocked("EVALUATION_PRODUCER_CHANGED")
    candidate = Candidate.model_validate(value["candidate"])
    if api.candidate(candidate.pr_number)[0] != candidate:
        raise Blocked("PR_SNAPSHOT_CHANGED")
    policy = read_policy(root / "github-ai-automation/config/policy.json")
    if policy_digest(root, policy) != value["policy_sha256"]:
        raise Blocked("POLICY_CHANGED")
    local = SnapshotStore(value["state"])
    worker_env = {**env, **value["settings"]}
    result = process(
        api,
        local,
        root,
        scratch,
        candidate.pr_number,
        operation=value["operation"],
        request_id=value["request_id"],
        rationale=value["rationale"],
        issue_snapshots=value["issues"],
        env=worker_env,
    )
    changes = {
        k: v
        for k, v in local.values.items()
        if value["state"].get(k) != v and ("/stages/" in k or k.endswith(("/patch", "/adoption")))
    }
    return {
        "schema": "evaluation-output/v2",
        "input_sha256": digest(value),
        "nonce": value["nonce"],
        "result": result,
        "evidence": changes,
    }


def evaluator_job(api, run_id, attempt):
    rows = api.pages(f"actions/runs/{run_id}/attempts/{attempt}/jobs", field="jobs")
    matches = [j for j in rows if j["name"] == "evaluate"]
    if len(matches) > 1:
        raise Blocked("EVALUATOR_JOB_AMBIGUOUS")
    return matches[0] if matches else None


def supervise(api, store, env=os.environ):
    repo = trusted_runtime(api, env)
    run_id, attempt = int(env["GITHUB_RUN_ID"]), int(env["GITHUB_RUN_ATTEMPT"])
    record, _ = store.read(execution_key(repo["id"], run_id, attempt))
    if not record:
        raise Blocked("EVALUATION_INPUT_MISSING")
    value = record["input"]
    candidate = Candidate.model_validate(value["candidate"])
    while True:
        try:
            requests.require_capability(store, candidate, value["request_id"], "assess")
            scheduler.bind(store, repo["id"], value["nonce"], run_id, attempt)
            job = evaluator_job(api, run_id, attempt)
            if job and job["status"] == "completed":
                return
        except Exception:
            # Cancellation can be uncertain; only reconciliation may reclaim capacity.
            api.request(f"actions/runs/{run_id}/cancel", method="POST")
            raise
        time.sleep(15)


def validate_evidence(path, row, value, producer):
    candidate = Candidate.model_validate(value["candidate"])
    start = candidate.key + "/requests/" + value["request_id"]
    if path in {
        start + f"/runs/{value['run_id']}_{value['attempt']}/" + suffix
        for suffix in ("patch", "adoption")
    }:
        if (
            row.get("candidate") != value["candidate"]
            or row.get("producer") != producer.model_dump()
            or row.get("operation_id") != value["request_id"]
        ):
            raise Blocked("WORKER_PATCH_IDENTITY_INVALID")
        assessment = GateAssessment.model_validate(row["verification"]["assessment"])
        if assessment.verdict != "pass" or row["verification"]["patch_sha256"] != digest(
            row["patch"]
        ):
            raise Blocked("WORKER_PATCH_INVALID")
        return
    identity = row.get("identity", {})
    expected = {
        "candidate": value["candidate"],
        "request_id": value["request_id"],
        "policy": producer.policy_sha256,
        "skills": producer.skills_sha256,
        "image": producer.runtime_image,
        "model": producer.model,
        "workflow_sha": producer.workflow_sha,
    }
    if (
        set(identity) != {*expected, "context_sha256"}
        or any(identity.get(k) != v for k, v in expected.items())
        or not re.fullmatch(r"[a-f0-9]{64}", identity.get("context_sha256", ""))
        or row.get("producer") != producer.model_dump()
        or not path.startswith(start + "/stages/" + digest(identity) + "/")
    ):
        raise Blocked("WORKER_EVIDENCE_IDENTITY_INVALID")
    name = path.rsplit("/", 1)[-1]
    schema = {
        "adoption-live-coverage": AdoptionLiveCoverage,
        "adoption-plan": AdoptionPlan,
        "adoption-plan-review": GateAssessment,
        "adoption-coverage": GateAssessment,
        "issues": IssueAssessment,
        "applicability": Applicability,
        "diagnosis": Diagnosis,
        "value": Usefulness,
        "value-confirm": Usefulness,
        "review": Review,
        "alignment": GateAssessment,
        "scenario": Scenario,
    }.get(name)
    if schema:
        schema.model_validate(row["result"])
    elif re.fullmatch(r"(?:checks|repair-checks)_[a-f0-9]{64}", name):
        if not row["result"] or any(r.get("exit_code") != 0 for r in row["result"]):
            raise Blocked("WORKER_CHECKS_INVALID")
    else:
        raise Blocked("WORKER_EVIDENCE_PATH_INVALID")


def accept(api, store, root, output, env=os.environ):
    repo = trusted_runtime(api, env)
    run_id, attempt = int(env["GITHUB_RUN_ID"]), int(env["GITHUB_RUN_ATTEMPT"])
    key = execution_key(repo["id"], run_id, attempt)
    record, revision = store.read(key)
    if not record or output.get("schema") != "evaluation-output/v2":
        raise Blocked("WORKER_OUTPUT_UNEXPECTED")
    value = record["input"]
    if (
        output.get("input_sha256") != record["input_sha256"]
        or output.get("nonce") != value["nonce"]
    ):
        raise Blocked("WORKER_INPUT_CHANGED")
    if value["workflow_sha"] != env["GITHUB_SHA"] or value["policy_sha256"] != policy_digest(root):
        raise Blocked("WORKER_POLICY_CHANGED")
    job = evaluator_job(api, run_id, attempt)
    if not job or job["status"] != "completed" or job["conclusion"] != "success":
        raise Blocked("WORKER_NOT_SUCCESSFUL")
    candidate = Candidate.model_validate(value["candidate"])
    if api.candidate(candidate.pr_number)[0] != candidate:
        raise Blocked("PR_SNAPSHOT_CHANGED")
    requests.require_capability(store, candidate, value["request_id"], "assess")
    if record["status"] != "prepared":
        if record.get("output_sha256") != digest(output):
            raise Blocked("WORKER_OUTPUT_CHANGED")
        return record["result"]
    import tempfile

    from .bundle import package_skills

    with tempfile.TemporaryDirectory(prefix="ai-verify-skills-") as temporary:
        skills = package_skills(
            root, root / "github-ai-automation/config/skills.json", Path(temporary) / "skills"
        )
    producer = Producer(
        workflow=".github/workflows/github-ai-automation.yml",
        workflow_sha=value["workflow_sha"],
        run_id=run_id,
        attempt=attempt,
        policy_sha256=value["policy_sha256"],
        skills_sha256=skills,
        runtime_image=value["settings"]["AI_AUTOMATION_AGENT_IMAGE"],
        model=value["settings"]["TF_MODEL_REVIEW"],
    )
    result = output["result"]
    if (
        result.get("candidate") != value["candidate"]
        or result.get("request_id") != value["request_id"]
        or result.get("operation") != value["operation"]
        or result.get("code") == "MERGE_ELIGIBLE"
        or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", result.get("code", ""))
    ):
        raise Blocked("WORKER_RESULT_INVALID")
    expected_record = (
        candidate.key + "/requests/" + value["request_id"] + f"/runs/{run_id}_{attempt}"
    )
    if "record" in result and result["record"] != expected_record:
        raise Blocked("WORKER_RECORD_INVALID")
    if result["code"] in {"ASSESSMENT_READY", "MANUAL_MERGE", "LIVE_APPLICABILITY_UNCERTAIN"}:
        stage_key = result.get("stage_key", "")
        combined = {**value["state"], **output["evidence"]}
        if not stage_key.startswith(
            candidate.key + "/requests/" + value["request_id"] + "/stages/"
        ):
            raise Blocked("WORKER_STAGE_KEY_INVALID")
        for name, stage in [("usefulness", "value"), ("review", "review")]:
            if result["checks"][name] != combined.get(stage_key + "/" + stage, {}).get("result"):
                raise Blocked("WORKER_CHECKS_MISMATCH")
        if result["checks"]["alignment"]["assessment"] != combined.get(
            stage_key + "/alignment", {}
        ).get("result"):
            raise Blocked("WORKER_CHECKS_MISMATCH")
        receipts = [
            row["result"]
            for path, row in combined.items()
            if path.startswith(stage_key + "/checks_")
        ]
        if result["checks"]["alignment"]["checks"] not in receipts:
            raise Blocked("WORKER_CHECKS_MISMATCH")
        if result.get("scenario") is not None and result["scenario"] != combined.get(
            stage_key + "/scenario", {}
        ).get("result"):
            raise Blocked("WORKER_SCENARIO_MISMATCH")
    issue_evidence = result.get("issue_evidence")
    if issue_evidence:
        from .issue_resolution import validate

        if (
            issue_evidence.get("candidate") != value["candidate"]
            or issue_evidence.get("request_id") != value["request_id"]
            or issue_evidence.get("snapshots") != value["issues"]
        ):
            raise Blocked("WORKER_ISSUE_IDENTITY_CHANGED")
        combined = {**value["state"], **output["evidence"]}
        if (
            combined.get(issue_evidence["stage_key"] + "/issues", {}).get("result")
            != issue_evidence["assessment"]
        ):
            raise Blocked("WORKER_ISSUE_EVIDENCE_MISMATCH")
        validate(IssueAssessment.model_validate(issue_evidence["assessment"]), value["issues"])
    elif value["issues"] and result["code"] in {
        "ASSESSMENT_READY",
        "MANUAL_MERGE",
        "CLOSURE_PREPARED",
    }:
        raise Blocked("WORKER_ISSUE_EVIDENCE_MISSING")
    if result["code"] == "MANUAL_MERGE":
        applicability = combined.get(result["stage_key"] + "/applicability", {}).get("result", {})
        if applicability.get("decision") != "not_applicable" or value["state"][
            candidate.key + "/operation"
        ].get("live_failure"):
            raise Blocked("WORKER_MANUAL_MERGE_INVALID")
    for path, row in output["evidence"].items():
        validate_evidence(path, row, value, producer)
    if result["code"] in {"PATCH_PREPARED", "ADOPTION_PREPARED"}:
        suffix = "/adoption" if result["code"] == "ADOPTION_PREPARED" else "/patch"
        patch = output["evidence"].get(expected_record + suffix)
        if not patch:
            raise Blocked("WORKER_PATCH_MISSING")
        if result["code"] == "ADOPTION_PREPARED":
            from .adoption import validate

            validate(patch, api.paths(candidate))
            combined = {**value["state"], **output["evidence"]}
            for field, stage in (
                ("plan", "adoption-plan"),
                ("plan_review", "adoption-plan-review"),
            ):
                if patch[field] != combined.get(patch["stage_key"] + "/" + stage, {}).get("result"):
                    raise Blocked("WORKER_ADOPTION_EVIDENCE_MISMATCH")
            for part in patch["slices"]:
                proof = part["verification"]
                if proof["checks"] not in [
                    r["result"]
                    for k, r in combined.items()
                    if k.endswith("/repair-checks_" + proof["source_sha256"])
                ]:
                    raise Blocked("WORKER_ADOPTION_CHECKS_MISMATCH")
        verification = patch["verification"]
        receipts = [
            row["result"]
            for path, row in {**value["state"], **output["evidence"]}.items()
            if path.endswith("/repair-checks_" + verification["source_sha256"])
        ]
        if verification["checks"] not in receipts:
            raise Blocked("WORKER_PATCH_CHECKS_MISMATCH")
    lease = acquire(store, candidate.key + "/lock")
    try:
        record, revision = store.read(key)
        if record["status"] != "prepared":
            if record.get("output_sha256") != digest(output):
                raise Blocked("WORKER_OUTPUT_CHANGED")
            return record["result"]
        current = requests.require_capability(store, candidate, value["request_id"], "assess")
        for path, row in output["evidence"].items():
            previous, etag = store.read(path)
            if previous is not None and previous != row:
                raise Blocked("WORKER_EVIDENCE_CONFLICT")
            if previous is None:
                lease.assert_owned()
                store.put(path, row, etag)
        if result["code"] in {"PATCH_PREPARED", "ADOPTION_PREPARED"}:
            # Idempotency is bound to this trusted execution, not worker state.
            suffix = "/adoption" if result["code"] == "ADOPTION_PREPARED" else "/patch"
            patch = output["evidence"].get(result.get("record", "") + suffix)
            applied = {"execution": key, "output_sha256": digest(output)}
            original_attempts = value["state"][candidate.key + "/operation"]["attempts"]
            already_applied = current.get("last_evaluation") == applied
            if not patch or (
                not already_applied
                and (
                    current["attempts"] != original_attempts
                    or current["attempts"]
                    >= read_policy(root / "github-ai-automation/config/policy.json")[
                        "max_fix_attempts"
                    ]
                )
            ):
                raise Blocked("REPAIR_BUDGET_EXHAUSTED")
            if not already_applied:
                current["attempts"] += 1
            current["last_evaluation"] = applied
        current["issue_evidence"] = issue_evidence
        current["stage"] = "waiting_ci" if result["code"] == "CI_REQUIRED" else "evaluated"
        _, etag = store.read(candidate.key + "/operation")
        lease.assert_owned()
        store.put(candidate.key + "/operation", current, etag)
        result = {**result, "detail": public_text(result.get("detail", ""))}
        store.put(
            key,
            {
                **record,
                "status": "accepted",
                "result": result,
                "output_sha256": digest(output),
                "producer": producer.model_dump(),
            },
            revision,
        )
    finally:
        lease.release()
    scheduler.terminal(store, repo["id"], value["nonce"], run_id, attempt)
    return result


def live_trial(api, store, root, scratch, env=os.environ):
    from . import live_slot

    repo = trusted_runtime(api, env)
    key = execution_key(repo["id"], int(env["GITHUB_RUN_ID"]), int(env["GITHUB_RUN_ATTEMPT"]))
    execution, _ = store.read(key)
    if (
        not execution
        or execution["status"] != "accepted"
        or execution["result"]["code"] != "ASSESSMENT_READY"
    ):
        raise Blocked("LIVE_EXECUTION_NOT_ADMITTED")
    result = execution["result"]
    candidate = Candidate.model_validate(result["candidate"])
    requests.require_capability(store, candidate, result["request_id"], "qualify")
    if api.candidate(candidate.pr_number)[0] != candidate:
        raise Blocked("PR_SNAPSHOT_CHANGED")
    if result["scenario"] is None:
        return
    from .events import expected_ci, reconcile_ci

    policy = read_policy(root / "github-ai-automation/config/policy.json")
    global_lease = acquire(store, f"repositories/{repo['id']}/live_lock")
    try:
        lease = acquire(store, candidate.key + "/lock")
    except Exception:
        global_lease.release()
        raise
    with Heartbeat(global_lease), Heartbeat(lease):
        try:
            combined = live_slot.CombinedLease(global_lease, lease)
            reservation = live_slot.reserve(
                store,
                repo["id"],
                candidate,
                int(env["GITHUB_RUN_ID"]),
                int(env["GITHUB_RUN_ATTEMPT"]),
                combined,
            )
            ci = reconcile_ci(api, store, lease, root, candidate, result["request_id"])
            if {r.workflow for r in ci} != set(expected_ci(api, root, candidate)):
                raise Blocked("CANDIDATE_CI_REQUIRED")
            source, _ = snapshot(candidate, scratch / "candidate")
            scenario = Scenario.model_validate(result["scenario"])
            op = requests.require_capability(store, candidate, result["request_id"], "qualify")
            if op.get("adoption_parent"):
                from .adoption import validate_live_coverage

                proposal, _ = store.read(op["adoption_parent"]["record"] + "/adoption")
                coverage, _ = store.read(result["stage_key"] + "/adoption-live-coverage")
                if (
                    not proposal
                    or not coverage
                    or digest(proposal) != op["adoption_parent"]["proposal"]
                ):
                    raise Blocked("ADOPTION_LIVE_COVERAGE_INCOMPLETE")
                validate_live_coverage(proposal, scenario, coverage["result"])
            validate_scenario(scenario, api.paths(candidate))
            record = requests.require_capability(store, candidate, result["request_id"], "qualify")
            live = execute_live(
                scenario=scenario,
                candidate=candidate,
                policy=policy,
                trusted_root=root,
                context={
                    "environment": "github-ai-live-test",
                    "source": str(source),
                    "trial": digest(
                        {
                            "request": result["request_id"],
                            "candidate": candidate.model_dump(),
                            "execution": key,
                        }
                    ),
                    "remaining_seconds": record["deadline"] - int(time.time()),
                },
                lease=combined,
                store=store,
            )
            global_lease.assert_owned()
            lease.assert_owned()
            # Retain an unresolved failure before publishing the execution receipt.
            # A later manual request must not hide a trial whose finalizer crashed.
            if live.outcome in {"failed", "intervened"}:
                operation, revision = store.read(candidate.key + "/operation")
                if not operation or operation["id"] != result["request_id"]:
                    raise Blocked("REQUEST_IDENTITY_CHANGED")
                operation["live_failure"] = {
                    "candidate": candidate.model_dump(),
                    "execution": key,
                    "live": live.model_dump(),
                }
                lease.assert_owned()
                store.put(candidate.key + "/operation", operation, revision)
            previous, etag = store.read(key + "/live")
            if previous is not None and previous != live.model_dump():
                raise Blocked("LIVE_RESULT_CHANGED")
            store.put(key + "/live", live.model_dump(), etag)
            if live.cleanup_complete:
                live_slot.clean(store, repo["id"], reservation, combined)
        finally:
            lease.release()
            global_lease.release()


def finalize(api, store, root, env=os.environ):
    from .contracts import LiveResult, Waiver
    from .events import expected_ci, reconcile_ci

    repo = trusted_runtime(api, env)
    key = execution_key(repo["id"], int(env["GITHUB_RUN_ID"]), int(env["GITHUB_RUN_ATTEMPT"]))
    execution, _ = store.read(key)
    if not execution or execution["status"] != "accepted":
        raise Blocked("EVALUATION_NOT_ACCEPTED")
    result = execution["result"]
    candidate = Candidate.model_validate(result["candidate"])
    request_id = result["request_id"]
    if api.candidate(candidate.pr_number)[0] != candidate:
        raise Blocked("PR_SNAPSHOT_CHANGED")
    requests.require_capability(store, candidate, request_id, "assess")
    if result["code"] not in {"ASSESSMENT_READY", "MANUAL_MERGE"}:
        return result
    lease = acquire(store, candidate.key + "/lock")
    with Heartbeat(lease):
        try:
            current = requests.require_capability(store, candidate, request_id, "qualify")
            require_cleanup(store, candidate)
            raw_waiver, _ = store.read(candidate.key + "/waiver")
            waiver = Waiver.model_validate(raw_waiver) if raw_waiver else None
            raw_live, _ = store.read(key + "/live")
            live = LiveResult.model_validate(raw_live) if raw_live else None
            if live and result.get("scenario") is not None:
                scenario = Scenario.model_validate(result["scenario"])
                if live.scenario_sha256 != digest(scenario) or (
                    live.outcome != "unavailable"
                    and set(live.assertions)
                    != {assertion.name for assertion in scenario.assertions}
                ):
                    raise Blocked("LIVE_SCENARIO_EVIDENCE_CHANGED")
            ci = reconcile_ci(api, store, lease, root, candidate, request_id)
            from .policy import validate_review_ci

            validate_review_ci(
                candidate,
                Producer.model_validate(execution["producer"]),
                Review.model_validate(result["checks"]["review"]),
                ci,
                paths=api.paths(candidate),
                expected_ci=expected_ci(api, root, candidate),
                operation_id=request_id,
                stages=result["checks"],
            )
            if live and (
                not live.cleanup_complete
                or live.source_tree != candidate.tree_sha
                or (
                    live.outcome == "passed"
                    and (not live.assertions or not all(live.assertions.values()))
                )
            ):
                raise Blocked("LIVE_TEST_NOT_PASSED")
            if live and live.outcome in {"failed", "intervened"}:
                failure = {
                    "candidate": candidate.model_dump(),
                    "execution": key,
                    "live": live.model_dump(),
                }
                current["live_failure"] = failure
                _, etag = store.read(candidate.key + "/operation")
                store.put(candidate.key + "/operation", current, etag)
                permitted = requests.capabilities(candidate, current["operation"], env)
                code = (
                    "LIVE_REMEDIATION_REQUIRED"
                    if "repair" in permitted
                    and current["attempts"]
                    < read_policy(root / "github-ai-automation/config/policy.json")[
                        "max_fix_attempts"
                    ]
                    else "LIVE_REPAIR_UNAVAILABLE"
                )
                return {
                    **result,
                    "code": code,
                    "detail": "Live validation failed or was intervened. A causal diagnosis and a fresh passing trial are required.",
                }
            if current.get("live_failure") and (not live or live.outcome != "passed"):
                raise Blocked("LIVE_TEST_NOT_PASSED")
            from .issue_resolution import premerge

            premerge(
                api,
                candidate,
                current.get("issue_evidence"),
                inherited_from=current.get("adoption_parent", {})
                .get("candidate", {})
                .get("pr_number"),
            )
            if live and live.outcome == "unavailable" and not (waiver and waiver.active):
                return {
                    **result,
                    "code": "MANUAL_MERGE",
                    "detail": "Review and CI passed; live validation is unavailable. Maintainer qualification is required.",
                }
            if result["code"] == "MANUAL_MERGE":
                if current.get("live_failure"):
                    raise Blocked("LIVE_TEST_NOT_PASSED")
                return result
            if (
                current.get("adoption_parent")
                and live
                and live.outcome == "passed"
                and live.assertions
                and all(live.assertions.values())
            ):
                coverage, _ = store.read(result["stage_key"] + "/adoption-coverage")
                if (
                    not coverage
                    or GateAssessment.model_validate(coverage["result"]).verdict != "pass"
                ):
                    raise Blocked("ADOPTION_COVERAGE_INCOMPLETE")
                from .adoption import validate_live_coverage

                proposal, _ = store.read(current["adoption_parent"]["record"] + "/adoption")
                live_coverage, _ = store.read(result["stage_key"] + "/adoption-live-coverage")
                if not proposal or not live_coverage or result.get("scenario") is None:
                    raise Blocked("ADOPTION_LIVE_COVERAGE_INCOMPLETE")
                validate_live_coverage(
                    proposal, Scenario.model_validate(result["scenario"]), live_coverage["result"]
                )
                current["adoption_verified"] = candidate.model_dump()
                _, etag = store.read(candidate.key + "/operation")
                store.put(candidate.key + "/operation", current, etag)
            if live and live.outcome == "passed" and current.get("live_failure"):
                current["resolved_live_failure"] = {
                    "failure": current.pop("live_failure"),
                    "passed_execution": key,
                }
                _, etag = store.read(candidate.key + "/operation")
                store.put(candidate.key + "/operation", current, etag)
            if "merge" not in requests.capabilities(candidate, current["operation"], env):
                if not (waiver and waiver.active) and (
                    not live
                    or live.outcome != "passed"
                    or not live.assertions
                    or not all(live.assertions.values())
                ):
                    raise Blocked("LIVE_TEST_NOT_PASSED")
                return {**result, "code": "QUALIFIED_MERGE_DISABLED"}
            requests.require_capability(store, candidate, request_id, "merge")
            prior_admission, _ = store.read(result["record"] + "/admission")
            admission_fence = prior_admission["fence"] if prior_admission else lease.fence
            admission = qualification(
                candidate,
                Producer.model_validate(execution["producer"]),
                Review.model_validate(result["checks"]["review"]),
                live,
                ci,
                waiver,
                paths=api.paths(candidate),
                expected_ci=expected_ci(api, root, candidate),
                operation_id=request_id,
                fence=admission_fence,
                expires_at=current["deadline"],
                mode=env.get("AI_AUTOMATION_PR_REVIEW", "disabled"),
                stages=result["checks"],
            )
            if prior_admission is not None and prior_admission != admission.model_dump():
                raise Blocked("ADMISSION_INPUT_CHANGED")
            current["qualification_fence"] = admission_fence
            _, etag = store.read(candidate.key + "/operation")
            store.put(candidate.key + "/operation", current, etag)
            for suffix, value in [
                ("admission", admission.model_dump()),
                ("stages", result["checks"]),
            ]:
                path = result["record"] + "/" + suffix
                previous, revision = store.read(path)
                if previous is not None and previous != value:
                    raise Blocked("ADMISSION_ALREADY_ISSUED")
                lease.assert_owned()
                store.put(path, value, revision)
            if api.request(f"pulls/{candidate.pr_number}").get("user", {}).get("id") == 41898282:
                return {
                    **result,
                    "code": "MANUAL_MERGE",
                    "detail": "Review, CI and live validation passed. This bot-authored PR requires independent maintainer review.",
                }
            requests.advance(store, lease, candidate, request_id, "merge")
            return {**result, "code": "MERGE_ELIGIBLE", "admission": admission.model_dump()}
        finally:
            lease.release()
