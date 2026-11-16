"""Trusted rollout admission. A global mode is a ceiling, never a canary permit."""

from __future__ import annotations

import time
from typing import Literal

from pydantic import Field

from .contracts import SHA, Blocked, Candidate, Contract, Digest, Positive, digest
from .state import acquire

Capability = Literal["assess", "comment", "label", "close", "repair", "qualify", "merge"]


class PRPermit(Contract):
    candidate: Candidate
    capabilities: list[Capability] = Field(min_length=1)
    expires_at: Positive


class ReleasePermit(Contract):
    project: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    source_sha: SHA
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    expires_at: Positive
    manifest_sha256: Digest | None = None
    # A new, explicit publication permit can authorize a previously prepared tag.
    preparation_generation: Positive | None = None


class Rollout(Contract):
    schema_version: Literal[1] = 1
    repository_id: Positive
    generation: Positive
    phase: Literal["qualification", "general"]
    prs: list[PRPermit] = Field(default_factory=list, max_length=10)
    releases: list[ReleasePermit] = Field(default_factory=list, max_length=10)


def key(repository_id):
    return f"repositories/{repository_id}/rollout/current"


def current(store, repository_id):
    value, _ = store.read(key(repository_id))
    if value is None:
        raise Blocked("ROLLOUT_NOT_CONFIGURED")
    try:
        result = Rollout.model_validate(value)
    except ValueError:
        raise Blocked("ROLLOUT_INVALID") from None
    if result.repository_id != repository_id:
        raise Blocked("ROLLOUT_REPOSITORY_MISMATCH")
    return result


def configure(api, store, value, expected_generation, event, env, *, now=None):
    """Only an authenticated default-branch human dispatch can replace rollout scope."""
    from .controller import trusted_runtime

    repo = trusted_runtime(api, env)
    actor = event.get("sender", {})
    if (
        actor.get("type") != "User"
        or str(actor.get("id")) != env.get("GITHUB_ACTOR_ID")
        or api.permission(actor.get("login", "")) not in {"maintain", "admin"}
    ):
        raise Blocked("ROLLOUT_ACTOR_UNAUTHORIZED")
    try:
        proposed = Rollout.model_validate(value)
    except ValueError:
        raise Blocked("ROLLOUT_INVALID") from None
    if proposed.repository_id != repo["id"] or proposed.generation != expected_generation + 1:
        raise Blocked("ROLLOUT_GENERATION_MISMATCH")
    now = int(time.time()) if now is None else now
    for permit in [*proposed.prs, *proposed.releases]:
        if not now < permit.expires_at <= now + 86400:
            raise Blocked("ROLLOUT_EXPIRY_INVALID")
    if len({p.candidate.pr_id for p in proposed.prs}) != len(proposed.prs) or len(
        {(p.project, p.source_sha) for p in proposed.releases}
    ) != len(proposed.releases):
        raise Blocked("ROLLOUT_PERMIT_DUPLICATE")
    for permit in proposed.prs:
        observed, pr = api.candidate(permit.candidate.pr_number)
        if observed != permit.candidate or pr["state"] != "open" or pr["draft"]:
            raise Blocked("ROLLOUT_CANDIDATE_CHANGED")
    if proposed.phase == "general" and (proposed.prs or proposed.releases):
        raise Blocked("ROLLOUT_GENERAL_HAS_PERMITS")
    lease = acquire(store, key(repo["id"]) + "_lock")
    try:
        previous, revision = store.read(key(repo["id"]))
        generation = previous["generation"] if previous else 0
        if generation != expected_generation:
            raise Blocked("ROLLOUT_GENERATION_MISMATCH")
        lease.assert_owned()
        store.put(key(repo["id"]), proposed.model_dump(mode="json"), revision)
        return proposed
    finally:
        lease.release()


def pr_scope(store, candidate, *, generation=None, capability=None, automatic=False, now=None):
    scope = current(store, candidate.repository_id)
    if generation is not None and generation != scope.generation:
        raise Blocked("ROLLOUT_GENERATION_CHANGED")
    if scope.phase == "general":
        return scope.generation
    if automatic:
        raise Blocked("ROLLOUT_AUTOMATIC_INTAKE_DISABLED")
    now = int(time.time()) if now is None else now
    matches = [p for p in scope.prs if p.candidate == candidate and p.expires_at > now]
    if len(matches) != 1:
        raise Blocked("ROLLOUT_CANDIDATE_NOT_ADMITTED")
    if capability is not None and capability not in matches[0].capabilities:
        raise Blocked("ROLLOUT_CAPABILITY_DENIED")
    return scope.generation


def release_scope(
    store, repository_id, project, source, *, version=None, generation=None, manifest=None, now=None
):
    scope = current(store, repository_id)
    if scope.phase == "general":
        if generation is not None and generation != scope.generation:
            raise Blocked("ROLLOUT_GENERATION_CHANGED")
        return scope.generation
    now = int(time.time()) if now is None else now
    matches = [
        p
        for p in scope.releases
        if p.project == project
        and p.source_sha == source
        and p.expires_at > now
        and (version is None or p.version == version)
    ]
    if len(matches) != 1:
        raise Blocked("ROLLOUT_RELEASE_NOT_ADMITTED")
    permit = matches[0]
    if manifest is not None:
        if permit.manifest_sha256 != digest(manifest):
            raise Blocked("ROLLOUT_RELEASE_MANIFEST_NOT_ADMITTED")
        if generation != permit.preparation_generation:
            raise Blocked("ROLLOUT_PREPARATION_CHANGED")
    elif generation is not None and generation != scope.generation:
        raise Blocked("ROLLOUT_GENERATION_CHANGED")
    return scope.generation
