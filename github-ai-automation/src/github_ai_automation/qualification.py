"""Hosted rehearsal with no GitHub write or production continuation capability."""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path

from .bundle import package_skills
from .contracts import Blocked, Producer, digest
from .controller import trusted_runtime
from .evaluation import assess
from .github import GitHub
from .policy import policy_digest, read_policy
from .source import baseline_context, snapshot
from .stages import Stages, human_context


class ReadOnlyGitHub(GitHub):
    def request(self, path, *, method="GET", **kwargs):
        if method != "GET":
            raise Blocked("QUALIFICATION_GITHUB_WRITE_DENIED")
        return super().request(path, method=method, **kwargs)


class LocalEvidence:
    """Worker-local immutable evidence only; never a fallback for production S3."""

    def __init__(self, deadline):
        self.deadline = deadline
        self.values = {}

    def assert_owned(self):
        if time.time() >= self.deadline:
            raise Blocked("OPERATION_EXPIRED")

    def read(self, key):
        value = self.values.get(key)
        return (copy.deepcopy(value), digest(value)) if value is not None else (None, None)

    def put(self, key, value, expected):
        self.assert_owned()
        if key in self.values or expected is not None:
            raise Blocked("LOCAL_EVIDENCE_ALREADY_RECORDED")
        self.values[key] = copy.deepcopy(value)
        return digest(value)


def shadow(api, root: Path, scratch: Path, number: int, env, image_receipt):
    if not isinstance(api, ReadOnlyGitHub):
        raise Blocked("QUALIFICATION_READ_ONLY_ADAPTER_REQUIRED")
    trusted_runtime(api, env)
    from .images import inputs_hash, verify_receipt

    run = api.request(f"actions/runs/{env['GITHUB_RUN_ID']}/attempts/{env['GITHUB_RUN_ATTEMPT']}")
    jobs = api.pages(
        f"actions/runs/{env['GITHUB_RUN_ID']}/attempts/{env['GITHUB_RUN_ATTEMPT']}/jobs",
        field="jobs",
    )
    guards = [job for job in jobs if job["name"] == "image"]
    if (
        run["path"] != ".github/workflows/github-ai-qualification.yml"
        or run["head_sha"] != env["GITHUB_SHA"]
        or len(guards) != 1
        or guards[0]["status"] != "completed"
        or guards[0]["conclusion"] != "success"
    ):
        raise Blocked("SHADOW_IMAGE_GUARD_REQUIRED")
    expected = api.artifact(
        int(env["GITHUB_RUN_ID"]),
        f"shadow-image-{env['GITHUB_RUN_ID']}-{env['GITHUB_RUN_ATTEMPT']}",
        "image.json",
    )
    if expected != image_receipt:
        raise Blocked("SHADOW_IMAGE_CHANGED")
    verify_receipt(
        image_receipt,
        repository=api.repository,
        run_id=image_receipt["run_id"],
        attempt=image_receipt["attempt"],
        image_ref=env.get("AI_AUTOMATION_AGENT_IMAGE_REF", ""),
        runtime_digest=env.get("AI_AUTOMATION_AGENT_IMAGE", ""),
    )
    if (
        image_receipt["input"]["inputs_sha256"] != inputs_hash(root)
        or env.get("TF_MODEL_REVIEW") not in image_receipt["models"]
    ):
        raise Blocked("IMAGE_QUALIFICATION_STALE")
    candidate, pr = api.candidate(number)
    if pr["state"] != "open" or pr["draft"]:
        raise Blocked("PR_NOT_READY")
    paths = api.paths(candidate)
    policy = read_policy(root / "github-ai-automation/config/policy.json")
    source, _ = snapshot(candidate, scratch / "candidate")
    bundle = scratch / "skills"
    skills_hash = package_skills(root, root / "github-ai-automation/config/skills.json", bundle)
    producer = Producer(
        workflow=".github/workflows/github-ai-qualification.yml",
        workflow_sha=env["GITHUB_SHA"],
        run_id=int(env["GITHUB_RUN_ID"]),
        attempt=int(env["GITHUB_RUN_ATTEMPT"]),
        policy_sha256=policy_digest(root, policy),
        skills_sha256=skills_hash,
        runtime_image=env.get("AI_AUTOMATION_AGENT_IMAGE", ""),
        model=env.get("TF_MODEL_REVIEW", ""),
    )
    context = json.dumps(
        {
            "candidate": candidate.model_dump(),
            "changed_paths": paths,
            "diff": (scratch / "candidate/diff.txt").read_text(errors="replace"),
            "rationale_and_discussion": human_context(api, number, pr),
            "accepted_baseline": baseline_context(candidate, scratch / "candidate", paths),
        }
    )
    deadline = int(time.time()) + policy["stage_timeout_seconds"]
    evidence = LocalEvidence(deadline)

    def options():
        evidence.assert_owned()
        return {
            "image": env.get("AI_AUTOMATION_AGENT_IMAGE_REF", ""),
            "source": source,
            "bundle": bundle,
            "model": producer.model,
            "api_key": env.get("AI_AUTOMATION_TF_API_KEY", ""),
            "timeout": max(1, deadline - int(time.time())),
        }

    stages = Stages(
        evidence,
        evidence,
        candidate,
        digest({"shadow": producer.model_dump()}),
        producer,
        source,
        scratch,
        options,
        digest(context),
    )
    result = assess(stages, context, paths, source, root)
    if api.candidate(number)[0] != candidate:
        raise Blocked("PR_SNAPSHOT_CHANGED")
    # Public output contains identities and fixed codes, never raw model prose,
    # secrets, patches or a production admission/continuation envelope.
    return {
        "schema": "qualification-shadow/v1",
        "candidate": candidate.model_dump(),
        "producer": producer.model_dump(),
        "code": result["code"],
        "evidence_sha256": digest(evidence.values),
        "github_writes": False,
        "live_qualified": False,
        "protected_effects_qualified": False,
    }
