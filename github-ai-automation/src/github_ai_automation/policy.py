"""Pure authorization decisions; every missing input denies the effect."""

from __future__ import annotations

import fnmatch
import json
import time
from pathlib import Path

from .contracts import (
    Admission,
    Blocked,
    Candidate,
    CIResult,
    LiveResult,
    Producer,
    Review,
    Scenario,
    Waiver,
    digest,
)

CONTROL_PATHS = (
    ".github/*",
    "github-ai-automation/*",
    "skills/*",
    "**/AGENTS.md",
    "AGENTS.md",
    "**/AGENTS.override.md",
    "AGENTS.override.md",
)


def read_policy(path: Path) -> dict:
    """Reject unsupported switches instead of silently ignoring owner intent."""
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        raise Blocked("POLICY_INVALID") from None
    budgets = {"max_fix_attempts", "stage_timeout_seconds", "live_timeout_seconds"}
    if (
        not isinstance(value, dict)
        or set(value) != budgets | {"schema_version", "live_capabilities"}
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or any(type(value[k]) is not int or value[k] <= 0 for k in budgets)
        or not isinstance(value["live_capabilities"], dict)
    ):
        raise Blocked("POLICY_INVALID")
    return value


def policy_digest(root, policy=None):
    policy = policy or read_policy(root / "github-ai-automation/config/policy.json")
    validation = json.loads((root / "github-ai-automation/config/validation.json").read_text())
    return digest({"policy": policy, "validation": validation})


def validate_review_ci(
    candidate, producer, review, ci, *, paths, expected_ci, operation_id, stages
):
    if not paths or any(
        fnmatch.fnmatchcase(p, pattern) for p in paths for pattern in CONTROL_PATHS
    ):
        raise Blocked("CONTROL_PLANE_OR_EMPTY_CHANGE")
    if review.verdict != "pass" or set(review.reviewed_paths) != set(paths):
        raise Blocked("REVIEW_INCOMPLETE")
    if review.compatibility != "compatible":
        raise Blocked("CONTRIBUTION_UNCONFIRMED")
    from .contracts import GateAssessment, Usefulness
    from .stages import coverage

    value = Usefulness.model_validate(stages["usefulness"])
    alignment = GateAssessment.model_validate(stages["alignment"]["assessment"])
    coverage(value, paths)
    coverage(alignment, paths)
    if (
        value.decision != "meaningful"
        or alignment.verdict != "pass"
        or stages["review"] != review.model_dump()
        or not stages["alignment"]["checks"]
        or any(check["exit_code"] != 0 for check in stages["alignment"]["checks"])
        or not set(alignment.required_checks)
        <= {check["id"] for check in stages["alignment"]["checks"]}
    ):
        raise Blocked("STAGE_EVIDENCE_INCOMPLETE")
    if (
        not expected_ci
        or len(ci) != len(expected_ci)
        or {x.workflow for x in ci} != set(expected_ci)
    ):
        raise Blocked("CI_INCOMPLETE")
    if any(
        x.conclusion != "success"
        or x.request_id != operation_id
        or x.tested_tree != candidate.tree_sha
        or x.workflow_sha != producer.workflow_sha
        for x in ci
    ):
        raise Blocked("CI_NOT_SUCCESSFUL")


def qualification(
    candidate: Candidate,
    producer: Producer,
    review: Review,
    live: LiveResult | None,
    ci: list[CIResult],
    waiver: Waiver | None,
    *,
    paths: list[str],
    expected_ci: list[str],
    operation_id: str,
    fence: int,
    expires_at: int,
    mode: str,
    stages: dict,
    now: int | None = None,
) -> Admission:
    now = int(time.time()) if now is None else now
    if mode != "merge":
        raise Blocked("MERGE_NOT_ENABLED")
    if expires_at <= now:
        raise Blocked("OPERATION_EXPIRED")
    validate_review_ci(
        candidate,
        producer,
        review,
        ci,
        paths=paths,
        expected_ci=expected_ci,
        operation_id=operation_id,
        stages=stages,
    )
    waived = waiver is not None and waiver.active
    if waiver and (
        waiver.repository_id != candidate.repository_id or waiver.pr_id != candidate.pr_id
    ):
        raise Blocked("WAIVER_IDENTITY_MISMATCH")
    if live and not live.cleanup_complete:
        raise Blocked("CLEANUP_PENDING")
    if live and (
        live.outcome in {"failed", "intervened"}
        or live.source_tree != candidate.tree_sha
        or (live.outcome == "passed" and (not live.assertions or not all(live.assertions.values())))
    ):
        raise Blocked("LIVE_TEST_NOT_PASSED")
    if not waived and (
        live is None
        or live.outcome != "passed"
        or live.source_tree != candidate.tree_sha
        or not live.assertions
        or not all(live.assertions.values())
    ):
        raise Blocked("LIVE_TEST_NOT_PASSED")
    return Admission(
        schema_version=2,
        candidate=candidate,
        producer=producer,
        operation_id=operation_id,
        fence=fence,
        review_sha256=digest(review),
        stages_sha256=digest(stages),
        live_sha256=digest(live) if live else digest({"waived": True}),
        ci=ci,
        waiver_revision=waiver.revision if waiver else 0,
        expires_at=expires_at,
        decision="merge",
    )


def validate_findings(review: Review, source: Path, changed_paths: list[str]):
    if set(review.reviewed_paths) != set(changed_paths):
        raise Blocked("REVIEW_COVERAGE_INCOMPLETE")
    for finding in review.findings:
        path = source / finding.path
        if finding.path not in changed_paths or path.is_symlink() or not path.is_file():
            raise Blocked("FINDING_LOCATION_INVALID")
        if not path.resolve().is_relative_to(source.resolve()):
            raise Blocked("FINDING_LOCATION_INVALID")
        if finding.line > len(path.read_text(errors="replace").splitlines()):
            raise Blocked("FINDING_LOCATION_INVALID")


def validate_scenario(scenario: Scenario, changed_paths: list[str]):
    if any(assertion.path not in changed_paths for assertion in scenario.assertions):
        raise Blocked("LIVE_SCENARIO_UNRELATED")


def register_waiver(
    store,
    lease,
    candidate: Candidate,
    *,
    actor_id: int,
    run_id: int,
    reason: str,
    active: bool,
    permission: str,
    actor_type: str,
):
    if permission not in {"admin", "maintain", "write"} or actor_type != "User":
        raise Blocked("WAIVER_ACTOR_UNAUTHORIZED")
    lease.assert_owned()
    key = candidate.key + "/waiver"
    current, etag = store.read(key)
    value = Waiver(
        repository_id=candidate.repository_id,
        pr_id=candidate.pr_id,
        actor_id=actor_id,
        request_run_id=run_id,
        reason=reason,
        active=active,
        revision=current["revision"] + 1 if current else 1,
    )
    store.put(key, value.model_dump(), etag)
    return value
