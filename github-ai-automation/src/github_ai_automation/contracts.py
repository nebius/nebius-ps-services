"""Closed, provider-neutral contracts. Model output never grants authority."""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SHA = Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Positive = Annotated[int, Field(strict=True, gt=0)]


def canonical(value: object) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def safe_path(value: str) -> str:
    p = PurePosixPath(value)
    if (
        not value
        or p.is_absolute()
        or ".." in p.parts
        or str(p) != value
        or "\\" in value
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError("INVALID_REPOSITORY_PATH")
    return value


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Candidate(Contract):
    repository: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")]
    repository_id: Positive
    head_repository: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")]
    head_repository_id: Positive
    head_ref: Annotated[
        str, Field(min_length=1, max_length=255, pattern=r"^[^\x00-\x20~^:?*\[\\]+$")
    ]
    pr_id: Positive
    pr_number: Positive
    head_sha: SHA
    base_sha: SHA
    tree_sha: SHA

    @property
    def key(self) -> str:
        return f"repositories/{self.repository_id}/prs/{self.pr_id}"

    @property
    def fork(self) -> bool:
        return self.head_repository_id != self.repository_id


class Producer(Contract):
    workflow: str
    workflow_sha: SHA
    run_id: Positive
    attempt: Positive
    policy_sha256: Digest
    skills_sha256: Digest
    runtime_image: Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{64}$")]
    model: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:/-]+$")]

    @field_validator("workflow")
    @classmethod
    def workflow_path(cls, value: str) -> str:
        safe_path(value)
        if not value.startswith(".github/workflows/") or not value.endswith(".yml"):
            raise ValueError("INVALID_WORKFLOW")
        return value


class Finding(Contract):
    path: str
    line: Positive
    severity: Literal["critical", "high", "medium", "low"]
    description: Annotated[str, Field(min_length=1, max_length=4000)]
    evidence: Annotated[str, Field(min_length=1, max_length=4000)]
    recommendation: Annotated[str, Field(min_length=1, max_length=4000)]

    _path = field_validator("path")(safe_path)


class Review(Contract):
    verdict: Literal["pass", "changes_requested", "inconclusive"]
    findings: list[Finding]
    reviewed_paths: list[str]
    summary: Annotated[str, Field(min_length=1, max_length=4000)]
    compatibility: Literal["compatible", "breaking", "uncertain"]
    spec_assessment: Annotated[str, Field(min_length=1, max_length=4000)]
    release_fragments: list[Fragment]

    @field_validator("reviewed_paths")
    @classmethod
    def paths(cls, values: list[str]) -> list[str]:
        return [safe_path(v) for v in values]

    @model_validator(mode="after")
    def consistent(self):
        if self.verdict == "pass" and self.findings:
            raise ValueError("PASS_WITH_FINDINGS")
        return self


class Usefulness(Contract):
    decision: Literal["meaningful", "no_benefit", "uncertain"]
    problem: Annotated[str, Field(min_length=1, max_length=2000)]
    benefit: Annotated[str, Field(min_length=1, max_length=2000)]
    reviewed_paths: list[str]
    evidence: Annotated[list[str], Field(min_length=1, max_length=100)]
    counterevidence: Annotated[str, Field(min_length=1, max_length=4000)]
    explanation: Annotated[str, Field(min_length=1, max_length=4000)]
    _paths = field_validator("reviewed_paths")(Review.paths.__func__)


class GateAssessment(Contract):
    verdict: Literal["pass", "fail", "unavailable"]
    reviewed_paths: list[str]
    findings: list[Finding]
    summary: Annotated[str, Field(min_length=1, max_length=4000)]
    required_checks: Annotated[list[str], Field(min_length=1, max_length=100)]
    _paths = field_validator("reviewed_paths")(Review.paths.__func__)

    @model_validator(mode="after")
    def consistent(self):
        if self.verdict == "pass" and self.findings:
            raise ValueError("PASS_WITH_FINDINGS")
        return self


class Assertion(Contract):
    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")]
    path: str
    product_action: Annotated[str, Field(min_length=1, max_length=2000)]
    observation: Annotated[str, Field(min_length=1, max_length=2000)]
    expected: Annotated[str, Field(min_length=1, max_length=2000)]
    _path = field_validator("path")(safe_path)


class Scenario(Contract):
    schema_version: Literal[1]
    capability: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")]
    objective: Annotated[str, Field(min_length=1, max_length=4000)]
    assertions: Annotated[list[Assertion], Field(min_length=1, max_length=30)]
    workload: Annotated[str, Field(min_length=1, max_length=100000)]
    observer: Annotated[str, Field(min_length=1, max_length=100000)]
    negative_control: Annotated[str, Field(max_length=100000)]
    fixture_requirements: Annotated[str, Field(max_length=4000)]


class LiveResult(Contract):
    outcome: Literal["passed", "failed", "unavailable", "intervened"]
    scenario_sha256: Digest
    source_tree: SHA
    assertions: dict[str, bool]
    cleanup_complete: bool
    reason: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,79}$")]
    failure_kind: Literal["product", "fixture", "infrastructure", "unknown"] = "unknown"


class LifecycleAuthority(Contract):
    origin: Literal["manual", "opened"]
    operation: Literal["review", "process"]
    request_id: Digest
    rollout_generation: Positive


class Applicability(Contract):
    decision: Literal["applicable", "not_applicable", "uncertain"]
    reviewed_paths: list[str]
    reason: Annotated[str, Field(min_length=1, max_length=2000)]
    _paths = field_validator("reviewed_paths")(Review.paths.__func__)


class Diagnosis(Contract):
    classification: Literal["proven_product", "fixture", "infrastructure", "unresolved"]
    reviewed_paths: list[str]
    cause: Annotated[str, Field(min_length=1, max_length=4000)]
    evidence: Annotated[list[str], Field(min_length=1, max_length=30)]
    contract_change: bool
    _paths = field_validator("reviewed_paths")(Review.paths.__func__)


class IssueDecision(Contract):
    repository: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")]
    number: Positive
    issue_id: Positive
    snapshot_sha256: Digest
    disposition: Literal["resolved", "unresolved", "not_planned", "duplicate", "uncertain"]
    basis: Literal["base", "head"]
    rationale: Annotated[str, Field(min_length=1, max_length=2000)]
    criteria: Annotated[list[str], Field(min_length=1, max_length=30)]
    evidence: Annotated[list[str], Field(min_length=1, max_length=30)]
    duplicate_issue_id: Positive | None = None

    @model_validator(mode="after")
    def duplicate_identity(self):
        if (self.disposition == "duplicate") != (self.duplicate_issue_id is not None):
            raise ValueError("ISSUE_DUPLICATE_IDENTITY_REQUIRED")
        return self


class IssueAssessment(Contract):
    reviewed_paths: list[str]
    issues: Annotated[list[IssueDecision], Field(max_length=100)]
    _paths = field_validator("reviewed_paths")(Review.paths.__func__)


class AdoptionFeature(Contract):
    id: Annotated[str, Field(pattern=r"^feature-[1-9][0-9]{0,2}$")]
    summary: Annotated[str, Field(min_length=1, max_length=1000)]
    paths: Annotated[list[str], Field(min_length=1, max_length=100)]
    depends_on: list[str]
    disposition: Literal["adopt", "incorporated", "excluded"]
    criteria: Annotated[list[str], Field(min_length=1, max_length=30)]
    evidence: Annotated[str, Field(min_length=1, max_length=4000)]
    _paths = field_validator("paths")(Review.paths.__func__)


class AdoptionPlan(Contract):
    reviewed_paths: list[str]
    features: Annotated[list[AdoptionFeature], Field(min_length=1, max_length=100)]
    _paths = field_validator("reviewed_paths")(Review.paths.__func__)

    @model_validator(mode="after")
    def complete_inventory(self):
        seen = set()
        for feature in self.features:
            if feature.id in seen or not set(feature.depends_on) <= seen:
                raise ValueError("ADOPTION_FEATURE_ORDER_INVALID")
            seen.add(feature.id)
        if set(self.reviewed_paths) != {p for f in self.features for p in f.paths}:
            raise ValueError("ADOPTION_COVERAGE_INCOMPLETE")
        if not any(f.disposition == "adopt" for f in self.features):
            raise ValueError("ADOPTION_HAS_NO_FEATURES")
        return self


class AdoptionLiveCoverage(Contract):
    verdict: Literal["pass", "fail", "unavailable"]
    reviewed_paths: list[str]
    feature_assertions: dict[str, list[str]]
    summary: Annotated[str, Field(min_length=1, max_length=4000)]
    _paths = field_validator("reviewed_paths")(Review.paths.__func__)


class Waiver(Contract):
    repository_id: Positive
    pr_id: Positive
    actor_id: Positive
    request_run_id: Positive
    reason: Annotated[str, Field(min_length=1, max_length=1000)]
    active: bool
    revision: Positive


class CIResult(Contract):
    request_id: Digest
    workflow: str
    run_id: Positive
    attempt: Positive
    workflow_sha: SHA
    tested_tree: SHA
    conclusion: Literal["success", "failure", "pending", "cancelled"]


class Admission(Contract):
    schema_version: Literal[2]
    candidate: Candidate
    producer: Producer
    operation_id: str
    fence: Positive
    review_sha256: Digest
    stages_sha256: Digest
    live_sha256: Digest
    ci: list[CIResult]
    waiver_revision: int = Field(ge=0)
    expires_at: int = Field(gt=0)
    decision: Literal["merge"]


class Fragment(Contract):
    project: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]*$")]
    bump: Literal["patch", "minor", "major"]
    summary: Annotated[str, Field(min_length=1, max_length=4000)]


class Blocked(RuntimeError):
    """Only fixed public codes cross a trust boundary; never provider error text."""

    def __init__(self, code: str):
        if not code or not all(c.isupper() or c.isdigit() or c == "_" for c in code):
            code = "OPERATION_BLOCKED"
        self.code = code
        super().__init__(code)


Review.model_rebuild()
