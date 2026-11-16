"""Trusted orchestration; all candidate execution is delegated to the sandbox."""

from __future__ import annotations

import fnmatch
import json
import os
import threading
import time
from contextlib import suppress
from pathlib import Path

import boto3

from .bundle import package_skills
from .contracts import (
    AdoptionLiveCoverage,
    Applicability,
    Blocked,
    Diagnosis,
    GateAssessment,
    Producer,
    Review,
    Scenario,
    digest,
)
from .github import GitHub, changed_paths
from .live import require_cleanup
from .policy import (
    CONTROL_PATHS,
    policy_digest,
    read_policy,
    validate_scenario,
)
from .releases import missing_fragment_patch
from .sandbox import run_agent
from .source import changes, prepare_conflict_tree, require_resolved_conflicts, snapshot
from .state import S3Store, acquire


def configured_store(env=os.environ):
    from botocore.config import Config

    names = [
        "AI_AUTOMATION_STATE_BUCKET",
        "AI_AUTOMATION_STATE_ENDPOINT",
        "AI_AUTOMATION_STATE_ACCESS_KEY_ID",
        "AI_AUTOMATION_STATE_SECRET_ACCESS_KEY",
    ]
    if any(not env.get(n) for n in names):
        raise Blocked("STATE_NOT_CONFIGURED")
    import re

    endpoint = re.fullmatch(r"https://storage\.([a-z0-9-]+)\.nebius\.cloud", env[names[1]])
    if not endpoint:
        raise Blocked("STATE_ENDPOINT_INVALID")
    client = boto3.client(
        "s3",
        endpoint_url=env[names[1]],
        aws_access_key_id=env[names[2]],
        aws_secret_access_key=env[names[3]],
        region_name=endpoint[1],
        config=Config(connect_timeout=5, read_timeout=20, retries={"total_max_attempts": 1}),
    )
    return S3Store(client, env[names[0]])


class Heartbeat:
    def __init__(self, lease):
        self.lease, self.stop = lease, threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop.wait(20):
            try:
                self.lease.renew(120)
            except Exception:
                return  # Every effect revalidates ownership and therefore fails closed.

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(5)


def trusted_runtime(api: GitHub, env=os.environ):
    repo = api.request("")
    if (
        env.get("GITHUB_ACTIONS") != "true"
        or env.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
        or env.get("GITHUB_REPOSITORY") != api.repository
        or env.get("GITHUB_REF") != "refs/heads/" + repo["default_branch"]
        or str(repo["id"]) != env.get("AI_AUTOMATION_REPOSITORY_ID")
    ):
        raise Blocked("TRUSTED_RUNTIME_REQUIRED")
    return repo


def process(
    api,
    store,
    root: Path,
    scratch: Path,
    number: int,
    *,
    operation="process",
    request_id="",
    rationale=None,
    issue_snapshots=None,
    env=os.environ,
):
    from . import requests
    from .source import baseline_context
    from .stages import Stages, human_context, no_benefit, public_text

    trusted_runtime(api, env)
    mode = env.get("AI_AUTOMATION_PR_REVIEW", "disabled")
    if mode not in {"report", "repair", "merge"}:
        raise Blocked("AUTOMATION_DISABLED")
    candidate, pr = api.candidate(number)
    paths = api.paths(candidate)
    policy = read_policy(root / "github-ai-automation/config/policy.json")
    if not request_id:
        event = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_text())
        admitted = requests.admit_manual(api, store, candidate, pr, operation, event, env)
        if not admitted:
            return {"code": "REQUEST_ALREADY_COMPLETE"}
        request_id = admitted["id"]
        operation = admitted["operation"]
        if admitted["candidate"]["pr_number"] != number:
            number = admitted["candidate"]["pr_number"]
            candidate, pr = api.candidate(number)
            paths = api.paths(candidate)
    record = requests.active(store, candidate, request_id, operation=operation)
    lease = acquire(store, candidate.key + "/lock")
    envelope = {
        "candidate": candidate.model_dump(),
        "request_id": request_id,
        "operation": operation,
    }
    with Heartbeat(lease):
        try:
            record = requests.require_capability(store, candidate, request_id, "assess", env=env)
            if any(fnmatch.fnmatchcase(p, pattern) for p in paths for pattern in CONTROL_PATHS):
                raise Blocked("CONTROL_PLANE_REPORT_ONLY")
            if pr["state"] != "open" or pr["draft"]:
                raise Blocked("PR_NOT_READY")
            for file in api.pages(f"pulls/{number}/files"):
                if (
                    any(
                        path.startswith("release-notes/") and path.endswith(".json")
                        for path in changed_paths([file])
                    )
                    and file["status"] != "added"
                ):
                    raise Blocked("RELEASE_FRAGMENT_MUST_BE_IMMUTABLE")
            requests.reconcile_effects(api, store, lease, candidate)
            _, version = store.read(candidate.key + "/operation")
            record.update(candidate=candidate.model_dump(), qualification_fence=lease.fence)
            store.put(candidate.key + "/operation", record, version)
            require_cleanup(store, candidate)
            allowed = set(record["capabilities"]) & set(
                requests.capabilities(candidate, operation, env)
            )
            source, original = snapshot(candidate, scratch / "candidate")
            bundle = scratch / "skills"
            skills_hash = package_skills(
                root, root / "github-ai-automation/config/skills.json", bundle
            )
            producer = Producer(
                workflow=".github/workflows/github-ai-automation.yml",
                workflow_sha=env["GITHUB_SHA"],
                run_id=int(env["GITHUB_RUN_ID"]),
                attempt=int(env["GITHUB_RUN_ATTEMPT"]),
                policy_sha256=policy_digest(root, policy),
                skills_sha256=skills_hash,
                runtime_image=env.get("AI_AUTOMATION_AGENT_IMAGE", ""),
                model=env.get("TF_MODEL_REVIEW", ""),
            )
            shared = dict(
                image=env.get("AI_AUTOMATION_AGENT_IMAGE_REF", ""),
                source=source,
                bundle=bundle,
                model=producer.model,
                api_key=env.get("AI_AUTOMATION_TF_API_KEY", ""),
            )

            def stage_options(*, fix=False):
                remaining = record["deadline"] - int(time.time())
                if remaining <= 0:
                    raise Blocked("OPERATION_EXPIRED")
                return {
                    **shared,
                    "model": env.get("TF_MODEL_FIX" if fix else "TF_MODEL_REVIEW", ""),
                    "timeout": min(policy["stage_timeout_seconds"], remaining),
                }

            from .issue_resolution import assess, collect

            issue_snapshots = collect(api, number) if issue_snapshots is None else issue_snapshots
            rationale = human_context(api, number, pr) if rationale is None else rationale
            adoption_proposal = None
            if record.get("adoption_parent"):
                binding = record["adoption_parent"]
                adoption_proposal, _ = store.read(binding["record"] + "/adoption")
                if not adoption_proposal or digest(adoption_proposal) != binding["proposal"]:
                    raise Blocked("ADOPTION_COVERAGE_CHANGED")
            context = json.dumps(
                {
                    "candidate": candidate.model_dump(),
                    "changed_paths": paths,
                    "diff": (scratch / "candidate/diff.txt").read_text(errors="replace"),
                    "rationale_and_discussion": rationale,
                    "linked_issues": issue_snapshots,
                    "live_failure": record.get("live_failure"),
                    "adoption_plan": adoption_proposal["plan"] if adoption_proposal else None,
                    "accepted_baseline": baseline_context(candidate, scratch / "candidate", paths),
                }
            )
            stages = Stages(
                store,
                lease,
                candidate,
                request_id,
                producer,
                source,
                scratch,
                stage_options,
                digest(context),
            )
            record_key = (
                candidate.key
                + "/requests/"
                + request_id
                + f"/runs/{producer.run_id}_{producer.attempt}"
            )
            issue_evidence = assess(stages, context, paths, issue_snapshots)
            envelope["issue_evidence"] = issue_evidence
            if "close" in allowed:
                from .closure import proof

                closure = proof(api, candidate)
                if closure:
                    return {**envelope, "code": "CLOSURE_PREPARED", "proof": closure}
            value = stages.usefulness(context, paths)
            if value.decision != "meaningful":
                if value.decision == "no_benefit" and "close" in allowed:
                    confirm = stages.usefulness(context, paths, confirm=True)
                    if no_benefit(value, confirm, paths):
                        return {
                            **envelope,
                            "code": "CLOSURE_PREPARED",
                            "proof": {
                                "kind": "no_benefit",
                                "stage_key": stages.key,
                                "paths": paths,
                                "head": candidate.head_sha,
                                "base": candidate.base_sha,
                                "rationale_sha256": digest(rationale),
                                "assessments": [digest(value), digest(confirm)],
                            },
                            "detail": public_text(value.explanation),
                        }
                return {
                    **envelope,
                    "code": "CONTRIBUTION_UNCONFIRMED",
                    "detail": public_text(value.explanation),
                }

            failure = record.get("live_failure")
            if failure and failure["candidate"]["head_sha"] == candidate.head_sha:
                if "repair" not in allowed or record["attempts"] >= policy["max_fix_attempts"]:
                    raise Blocked("LIVE_REPAIR_UNAVAILABLE")
                diagnosis = stages.agent(
                    "diagnosis",
                    Diagnosis,
                    "Use $troubleshoot to diagnose the recorded failed live trial. Inspect source and frozen failed assertions. "
                    "Separate product, fixture and infrastructure causes. Require causal evidence; unknown is unresolved. "
                    "Never treat a test expectation change or environment workaround as a product fix.\n"
                    + context,
                    paths,
                )
                if diagnosis.classification != "proven_product":
                    return {
                        **envelope,
                        "code": "LIVE_DIAGNOSIS_BLOCKED",
                        "detail": public_text(diagnosis.cause),
                    }
                run_agent(
                    **stage_options(fix=True),
                    writable=True,
                    scratch=scratch / "live-repair",
                    output_type=Review,
                    prompt=(
                        "Use $design for the proven contract-changing remediation, then "
                        if diagnosis.contract_change
                        else ""
                    )
                    + "Use $troubleshoot to repair the causal product owner within accepted intent. Preserve the original trial and criteria. "
                    "An unaccepted scope change remains blocked. No publication.\n"
                    + context
                    + "\n"
                    + diagnosis.model_dump_json(),
                )
                patch = changes(source, original)
                verification = stages.repair(
                    context
                    + "\nCausal diagnosis: "
                    + diagnosis.model_dump_json()
                    + "\nProposed repair: "
                    + json.dumps(patch),
                    sorted(set(paths) | {p["path"] for p in patch}),
                    root,
                )
                return prepare_patch(
                    store,
                    lease,
                    record_key,
                    candidate,
                    producer,
                    record,
                    patch,
                    verification,
                    envelope,
                )

            def adopt():
                from .adoption import prepare

                if "adopt" not in allowed or record.get("adoption_parent"):
                    raise Blocked("ADOPTION_NOT_AUTHORIZED")
                if record["attempts"] >= policy["max_fix_attempts"]:
                    raise Blocked("REPAIR_BUDGET_EXHAUSTED")
                return prepare(
                    stages,
                    context,
                    paths,
                    root,
                    scratch / "candidate",
                    original,
                    record,
                    envelope,
                    record_key,
                )

            comparison = api.request(f"compare/{candidate.base_sha}...{candidate.head_sha}")
            if comparison["status"] not in {"ahead", "identical"}:
                if "repair" not in allowed:
                    if pr.get("mergeable") is False and "adopt" in allowed:
                        return adopt()
                    return {
                        **envelope,
                        "code": "CONTRIBUTOR_BASE_UPDATE_REQUIRED",
                        "detail": "The contribution is useful, but its author must update the branch against the current base and resolve conflicts.",
                    }
                if pr.get("mergeable") is None:
                    raise Blocked("MERGEABILITY_UNAVAILABLE")
                if pr["mergeable"] is not False:
                    return {**envelope, "code": "BASE_SYNC_REQUIRED"}
                if record["attempts"] >= policy["max_fix_attempts"]:
                    raise Blocked("REPAIR_BUDGET_EXHAUSTED")
                prepare_conflict_tree(candidate, scratch / "candidate", source)
                from .repairs import conflict_basis, conflict_scope

                try:
                    provisional, conflict_paths = conflict_basis(source)
                except Blocked as exc:
                    if exc.code == "CONFLICT_STRUCTURE_UNSUPPORTED":
                        return adopt()
                    raise
                if len(conflict_paths) > 5:
                    return adopt()
                run_agent(
                    **stage_options(fix=True),
                    writable=True,
                    scratch=scratch / "conflict",
                    output_type=Review,
                    prompt="Use $troubleshoot to resolve only unambiguous localized conflicts in /source. Preserve both parent intents. Do not publish.\n"
                    + context,
                )
                try:
                    conflict_scope(source, provisional, conflict_paths)
                except Blocked as exc:
                    if exc.code == "CONFLICT_REPAIR_TOO_LARGE":
                        return adopt()
                    raise
                patch = changes(source, original)
                require_resolved_conflicts(patch)
                verification = stages.repair(
                    context + "\nProposed patch: " + json.dumps(patch),
                    sorted(set(paths) | {p["path"] for p in patch}),
                    root,
                )
                return prepare_patch(
                    store,
                    lease,
                    record_key,
                    candidate,
                    producer,
                    record,
                    patch,
                    verification,
                    envelope,
                    merge_base=candidate.base_sha,
                )

            from .evaluation import review_candidate

            review = review_candidate(stages, context, paths, source)
            if review.verdict != "pass":
                if (
                    review.verdict == "changes_requested"
                    and "repair" in allowed
                    and record["attempts"] < policy["max_fix_attempts"]
                ):
                    run_agent(
                        **stage_options(fix=True),
                        writable=True,
                        scratch=scratch / "repair",
                        output_type=Review,
                        prompt="Use $troubleshoot for narrowly scoped contract-restoring fixes. Do not change policy, publish or merge.\n"
                        + context
                        + "\n"
                        + review.model_dump_json(),
                    )
                    patch = changes(source, original)
                    verification = stages.repair(
                        context + "\nProposed patch: " + json.dumps(patch),
                        sorted(set(paths) | {p["path"] for p in patch}),
                        root,
                    )
                    return prepare_patch(
                        store,
                        lease,
                        record_key,
                        candidate,
                        producer,
                        record,
                        patch,
                        verification,
                        envelope,
                    )
                return {
                    **envelope,
                    "code": "REVIEW_NOT_PASSED",
                    "detail": public_text(review.summary),
                }
            if record.get("adoption_parent"):
                binding = record["adoption_parent"]
                proposal, _ = store.read(binding["record"] + "/adoption")
                if not proposal or digest(proposal) != binding["proposal"]:
                    raise Blocked("ADOPTION_COVERAGE_CHANGED")
                coverage = stages.agent(
                    "adoption-coverage",
                    GateAssessment,
                    "Independently verify EVERY accepted original feature against this complete replacement source, including later repairs. "
                    "Use the original full diff and complete inventory; reject omissions or regressions.\n"
                    + context
                    + "\nOriginal: "
                    + proposal["original_context"]
                    + "\nPlan: "
                    + json.dumps(proposal["plan"]),
                    paths,
                )
                if coverage.verdict != "pass":
                    raise Blocked("ADOPTION_COVERAGE_INCOMPLETE")
            alignment = stages.alignment(context, paths, root)
            checks = {
                "usefulness": value.model_dump(),
                "review": review.model_dump(),
                "alignment": alignment,
            }
            if "qualify" not in allowed:
                return {
                    **envelope,
                    "code": "REVIEW_COMPLETE",
                    "detail": public_text(review.summary),
                }
            registry = json.loads(
                (root / "github-ai-automation/config/release-projects.json").read_text()
            )
            fragment = missing_fragment_patch(source, paths, registry, review, number)
            if fragment:
                if "repair" not in allowed:
                    return {
                        **envelope,
                        "code": "CONTRIBUTOR_RELEASE_FRAGMENT_REQUIRED",
                        "detail": "Add the required release fragment to this PR before requesting another pass.",
                    }
                if record["attempts"] >= policy["max_fix_attempts"]:
                    raise Blocked("REPAIR_BUDGET_EXHAUSTED")
                import base64

                for item in fragment:
                    target = source / item["path"]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(base64.b64decode(item["content"], validate=True))
                patch = changes(source, original)
                verification = stages.repair(
                    context + "\nProposed release metadata: " + json.dumps(patch),
                    sorted(set(paths) | {p["path"] for p in patch}),
                    root,
                )
                return prepare_patch(
                    store,
                    lease,
                    record_key,
                    candidate,
                    producer,
                    record,
                    patch,
                    verification,
                    envelope,
                )
            from .events import reconcile_ci

            inventory = json.loads((root / ".github/merge-policy.json").read_text())["ci_workflows"]
            expected = [
                w["file"]
                for w in inventory
                if any(fnmatch.fnmatchcase(p, q) for p in paths for q in w["paths"])
            ]
            ci = reconcile_ci(api, store, lease, root, candidate, request_id, env=env)
            if not expected or {row.workflow for row in ci} != set(expected):
                requests.advance(store, lease, candidate, request_id, "waiting_ci")
                return {
                    **envelope,
                    "code": "CI_REQUIRED",
                    "record": record_key,
                    "expected_ci": expected,
                    "producer": producer.model_dump(),
                }
            applicability = stages.agent(
                "applicability",
                Applicability,
                "Use $test-live-on-nebius to assess whether Nebius live validation can exercise the changed behavior. "
                "Use not_applicable only for a justified unrelated/non-runtime change; missing drivers or credentials do not prove inapplicability. "
                "Use uncertain when scope is unknown.\n" + context,
                paths,
            )
            if applicability.decision != "applicable":
                return {
                    **envelope,
                    "code": "MANUAL_MERGE"
                    if applicability.decision == "not_applicable" and not failure
                    else "LIVE_APPLICABILITY_UNCERTAIN",
                    "detail": public_text(applicability.reason),
                    "stage_key": stages.key,
                    "checks": checks,
                }
            waiver_data, _ = store.read(candidate.key + "/waiver")
            waived = bool(waiver_data and waiver_data["active"] and not failure)
            if not waived:
                prior = stages.read("scenario")
                if prior:
                    scenario = Scenario.model_validate(prior["result"])
                else:
                    scenario = run_agent(
                        **stage_options(),
                        scratch=scratch / "scenario",
                        output_type=Scenario,
                        prompt="Use $test-live-on-nebius to design a minimal specific regression scenario with separate fixtures, product workload and independent assertions. "
                        "No privileged driver code or cloud identifiers. Available capabilities: "
                        + json.dumps(list(policy["live_capabilities"]))
                        + "\n"
                        + context,
                    )
                    validate_scenario(scenario, paths)
                    stages.save("scenario", scenario.model_dump())
            if adoption_proposal and not waived:
                from .adoption import validate_live_coverage

                live_coverage = stages.agent(
                    "adoption-live-coverage",
                    AdoptionLiveCoverage,
                    "Independently verify this live scenario exercises EVERY adopted feature and its criteria. "
                    "Map each adopted feature ID to actual assertion names; reject unsupported coverage. "
                    "Names alone are not proof: inspect product actions, observations and expected behavior.\n"
                    + context
                    + "\nScenario: "
                    + scenario.model_dump_json(),
                    paths,
                )
                validate_live_coverage(adoption_proposal, scenario, live_coverage.model_dump())
            latest, _ = api.candidate(number)
            if latest != candidate:
                raise Blocked("PR_SNAPSHOT_CHANGED")
            # Cloud work and admission issuance belong to separate trusted jobs.
            # This worker can only return the assessment and a proposed scenario.
            return {
                **envelope,
                "code": "ASSESSMENT_READY",
                "record": record_key,
                "producer": producer.model_dump(),
                "stage_key": stages.key,
                "checks": checks,
                "scenario": scenario.model_dump() if not waived else None,
            }
        except Blocked as exc:
            return {**envelope, "code": exc.code}
        except Exception:
            return {**envelope, "code": "AUTOMATION_EXECUTION_UNAVAILABLE"}
        finally:
            with suppress(Blocked):
                lease.release()


def prepare_patch(
    store, lease, record_key, candidate, producer, record, patch, verification, envelope, **extra
):
    if not patch or any(
        fnmatch.fnmatchcase(p["path"], pattern) for p in patch for pattern in CONTROL_PATHS
    ):
        raise Blocked("PATCH_SCOPE_REJECTED")
    current, version = store.read(candidate.key + "/operation")
    lease.assert_owned()
    current["attempts"] += 1
    store.put(candidate.key + "/operation", current, version)
    store.put(
        record_key + "/patch",
        {
            "candidate": candidate.model_dump(),
            "producer": producer.model_dump(),
            "operation_id": record["id"],
            "patch": patch,
            "verification": {**verification, "patch_sha256": digest(patch)},
            **extra,
        },
        None,
    )
    return {**envelope, "code": "PATCH_PREPARED", "record": record_key}
