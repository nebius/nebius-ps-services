"""One replacement PR, complete feature coverage and one-way request transfer."""

from __future__ import annotations

import base64
import fnmatch
import json
import re
from urllib.parse import quote

from . import requests
from .contracts import (
    AdoptionLiveCoverage,
    AdoptionPlan,
    Blocked,
    Candidate,
    GateAssessment,
    Producer,
    Review,
    digest,
    safe_path,
)
from .policy import CONTROL_PATHS, policy_digest
from .sandbox import run_agent
from .source import changes, export_base, require_resolved_conflicts, source_entries
from .state import acquire, effect


def prepare(stages, context, paths, root, destination, original, record, envelope, record_key):
    if record.get("adoption_parent"):
        raise Blocked("RECURSIVE_ADOPTION_DENIED")
    plan = stages.agent(
        "adoption-plan",
        AdoptionPlan,
        "Use $design to inventory EVERY behavior in the entire original diff, including tests and documentation. "
        "Map each to an ordered feature with source paths, criteria, dependencies and evidence. Use incorporated only with base proof; "
        "excluded only with explicit accepted-scope justification. Preserve contributor attribution. Do not omit awkward features.\\n"
        + context,
        paths,
    )
    manifest = plan.model_dump_json()
    approval = stages.agent(
        "adoption-plan-review",
        GateAssessment,
        "Independently compare this feature inventory to the ENTIRE original diff and accepted intent. "
        "Reject omitted, unexplained or improperly excluded behavior. No publication.\\n"
        + context
        + "\\n"
        + manifest,
        paths,
    )
    if approval.verdict != "pass":
        raise Blocked("ADOPTION_COVERAGE_UNCONFIRMED")
    base_entries = export_base(stages.candidate, destination, stages.source)
    previous, slices = base_entries, []
    for feature in plan.features:
        if feature.disposition != "adopt":
            continue
        run_agent(
            **stages.options(fix=True),
            writable=True,
            scratch=stages.scratch / feature.id,
            output_type=Review,
            prompt="Use $design and implement only this accepted feature against /source, the frozen current default plus prior features. "
            "Preserve both baseline behavior and accepted contribution. Do not publish.\\n"
            + context
            + "\\nPlan: "
            + manifest
            + "\\nFeature: "
            + feature.model_dump_json(),
        )
        patch = changes(stages.source, previous)
        require_resolved_conflicts(patch)
        if not patch:
            raise Blocked("ADOPTION_FEATURE_EMPTY")
        verification = stages.repair(
            context + "\\nFeature: " + feature.model_dump_json() + "\\nPatch: " + json.dumps(patch),
            sorted(set(paths) | {p["path"] for p in patch}),
            root,
        )
        slices.append(
            {
                "feature": feature.id,
                "patch": patch,
                "verification": {**verification, "patch_sha256": digest(patch)},
            }
        )
        previous = source_entries(stages.source)
    patch = changes(stages.source, base_entries)
    verification = stages.repair(
        context
        + "\\nVerify ALL accepted feature criteria against the final replacement, including prior slices.\\n"
        + manifest
        + "\\n"
        + json.dumps(patch),
        sorted(set(paths) | {p["path"] for p in patch}),
        root,
    )
    proposal = {
        "candidate": stages.candidate.model_dump(),
        "operation_id": record["id"],
        "producer": stages.producer.model_dump(),
        "original_context": context,
        "plan": plan.model_dump(),
        "plan_review": approval.model_dump(),
        "stage_key": stages.key,
        "slices": slices,
        "patch": patch,
        "verification": {**verification, "patch_sha256": digest(patch)},
    }
    validate(proposal, paths)
    stages.store.put(record_key + "/adoption", proposal, None)
    return {**envelope, "code": "ADOPTION_PREPARED", "record": record_key}


def validate(proposal, paths):
    plan = AdoptionPlan.model_validate(proposal["plan"])
    approval = GateAssessment.model_validate(proposal["plan_review"])
    if (
        set(plan.reviewed_paths) != set(paths)
        or set(approval.reviewed_paths) != set(paths)
        or approval.verdict != "pass"
    ):
        raise Blocked("ADOPTION_COVERAGE_INCOMPLETE")
    if [s["feature"] for s in proposal["slices"]] != [
        f.id for f in plan.features if f.disposition == "adopt"
    ]:
        raise Blocked("ADOPTION_COVERAGE_INCOMPLETE")
    for part in [*proposal["slices"], proposal]:
        patch, proof = part["patch"], part["verification"]
        assessment = GateAssessment.model_validate(proof["assessment"])
        if (
            not patch
            or len(patch) > 100
            or proof["patch_sha256"] != digest(patch)
            or assessment.verdict != "pass"
            or not proof["checks"]
            or any(r.get("exit_code") != 0 for r in proof["checks"])
            or not set(assessment.required_checks) <= {r["id"] for r in proof["checks"]}
            or not {p["path"] for p in patch} <= set(assessment.reviewed_paths)
        ):
            raise Blocked("ADOPTION_NOT_VERIFIED")
        total = 0
        for item in patch:
            path = safe_path(item["path"])
            if any(fnmatch.fnmatchcase(path, pattern) for pattern in CONTROL_PATHS) or item[
                "mode"
            ] not in {"100644", "100755"}:
                raise Blocked("ADOPTION_PATCH_SCOPE_INVALID")
            if item["content"] is not None:
                data = base64.b64decode(item["content"], validate=True)
                total += len(data)
                if re.search(
                    rb"PRIVATE KEY|(?:ghp_|github_pat_|sk-)[A-Za-z0-9_-]{20,}|(?:tenant|project)-[a-z0-9]{16,}",
                    data,
                ):
                    raise Blocked("PATCH_SENSITIVE_CONTENT")
        if total > 512_000:
            raise Blocked("PATCH_TOO_LARGE")


def validate_live_coverage(proposal, scenario, raw):
    coverage = AdoptionLiveCoverage.model_validate(raw)
    features = {f["id"] for f in proposal["plan"]["features"] if f["disposition"] == "adopt"}
    names = {a.name for a in scenario.assertions}
    if (
        coverage.verdict != "pass"
        or set(coverage.feature_assertions) != features
        or any(
            not assertions or not set(assertions) <= names
            for assertions in coverage.feature_assertions.values()
        )
    ):
        raise Blocked("ADOPTION_LIVE_COVERAGE_INCOMPLETE")


def publish(api, store, lease, record_key, candidate, request_id, root):
    from .rollout import current

    op = requests.require_capability(store, candidate, request_id, "adopt")
    if current(store, candidate.repository_id).phase != "general":
        raise Blocked("ADOPTION_ROLLOUT_NOT_ADMITTED")
    proposal, _ = store.read(record_key + "/adoption")
    if (
        not proposal
        or proposal["candidate"] != candidate.model_dump()
        or proposal["operation_id"] != request_id
    ):
        raise Blocked("ADOPTION_IDENTITY_CHANGED")
    validate(proposal, api.paths(candidate))
    producer = Producer.model_validate(proposal["producer"])
    if producer.policy_sha256 != policy_digest(root):
        raise Blocked("ADOPTION_POLICY_CHANGED")
    api.producer(producer)
    if api.candidate(candidate.pr_number)[0] != candidate:
        raise Blocked("PR_SNAPSHOT_CHANGED")
    base_ref = api.request("")["default_branch"]
    branch = "ai-adoption/" + str(candidate.pr_id) + "-" + request_id[:16]
    run = api.request(f"actions/runs/{producer.run_id}")
    author = {
        "name": "github-actions[bot]",
        "email": "41898282+github-actions[bot]@users.noreply.github.com",
        "date": run["created_at"],
    }
    sha = candidate.base_sha
    tree = api.request("git/commits/" + sha)["tree"]["sha"]
    for part in proposal["slices"]:
        entries = []
        for item in part["patch"]:
            blob = (
                None
                if item["content"] is None
                else api.request(
                    "git/blobs",
                    method="POST",
                    body={"content": item["content"], "encoding": "base64"},
                )["sha"]
            )
            entries.append(
                {"path": item["path"], "mode": item["mode"], "type": "blob", "sha": blob}
            )
        tree = api.request("git/trees", method="POST", body={"base_tree": tree, "tree": entries})[
            "sha"
        ]
        sha = api.request(
            "git/commits",
            method="POST",
            body={
                "message": f"feat: adopt {part['feature']} from PR #{candidate.pr_number}",
                "tree": tree,
                "parents": [sha],
                "author": author,
                "committer": author,
            },
        )["sha"]
    aggregate = []
    for item in proposal["patch"]:
        blob = (
            None
            if item["content"] is None
            else api.request(
                "git/blobs", method="POST", body={"content": item["content"], "encoding": "base64"}
            )["sha"]
        )
        aggregate.append({"path": item["path"], "mode": item["mode"], "type": "blob", "sha": blob})
    base_tree = api.request("git/commits/" + candidate.base_sha)["tree"]["sha"]
    if (
        api.request("git/trees", method="POST", body={"base_tree": base_tree, "tree": aggregate})[
            "sha"
        ]
        != tree
    ):
        raise Blocked("ADOPTION_SLICE_TREE_MISMATCH")
    intent = {
        "candidate": candidate.model_dump(),
        "request_id": request_id,
        "branch": branch,
        "sha": sha,
        "tree": tree,
        "proposal": digest(proposal),
    }

    def guard():
        requests.require_capability(store, candidate, request_id, "adopt")
        if api.candidate(candidate.pr_number)[0] != candidate:
            raise Blocked("PR_SNAPSHOT_CHANGED")

    def branch_seen(_):
        row = api.request("git/ref/heads/" + quote(branch, safe=""), absent=True)
        if row and row["object"]["sha"] != sha:
            raise Blocked("ADOPTION_BRANCH_CHANGED")
        return {"sha": sha} if row else None

    def create_branch(_):
        guard()
        if branch_seen(None):
            raise Blocked("ADOPTION_BRANCH_ALREADY_EXISTS")
        api.request("git/refs", method="POST", body={"ref": "refs/heads/" + branch, "sha": sha})
        return branch_seen(None)

    effect(store, lease, record_key + "/adoption-branch", intent, branch_seen, create_branch)
    title = f"Adopt contribution from PR #{candidate.pr_number}"
    body = (
        f"Replacement for #{candidate.pr_number}; preserves the original contributor's accepted features.\n\n"
        + "\n".join(
            f"- {f.id}: {f.summary}" for f in AdoptionPlan.model_validate(proposal["plan"]).features
        )
        + "\n\nOriginal PR remains open until complete merged coverage is verified."
    )
    from .stages import public_text

    body = public_text(body)

    def pr_seen(_):
        rows = api.pages(
            "pulls?state=all&head=" + quote(api.repository.split("/")[0] + ":" + branch, safe="")
        )
        if len(rows) > 1:
            raise Blocked("ADOPTION_PR_AMBIGUOUS")
        if not rows:
            return None
        row = rows[0]
        if (
            row["head"]["sha"] != sha
            or row["base"]["ref"] != base_ref
            or row["user"]["id"] != 41898282
        ):
            raise Blocked("ADOPTION_PR_CHANGED")
        return {"number": row["number"], "id": row["id"]}

    def create_pr(_):
        guard()
        if pr_seen(None):
            raise Blocked("ADOPTION_PR_ALREADY_EXISTS")
        api.request(
            "pulls",
            method="POST",
            body={"title": title, "body": body, "head": branch, "base": base_ref},
        )
        found = pr_seen(None)
        if not found:
            raise Blocked("ADOPTION_PR_NOT_VERIFIED")
        return found

    child_ref = effect(store, lease, record_key + "/adoption-pr", intent, pr_seen, create_pr)
    child, _ = api.candidate(child_ref["number"])
    if child.pr_id != child_ref["id"] or child.head_sha != sha or child.tree_sha != tree:
        raise Blocked("ADOPTION_CHILD_CHANGED")
    binding = {
        "candidate": candidate.model_dump(),
        "request_id": request_id,
        "record": record_key,
        "child": child.model_dump(),
        "proposal": digest(proposal),
        "rationale_sha256": op["rationale_sha256"],
        "state_events": op["state_events"],
    }
    return transfer(store, lease, candidate, child, op, binding)


def transfer(store, lease, parent, child, operation, binding):
    """Parent stops executing before child dispatch; only child spends remainder."""
    child_key = child.key + "/operation"
    child_id = digest(binding)
    current, revision = store.read(parent.key + "/operation")
    if current["id"] != operation["id"] or current.get("adoption_child") not in (None, binding):
        raise Blocked("ADOPTION_PARENT_CHANGED")
    lease.assert_owned()
    current.update(stage="adoption_waiting", adoption_child=binding)
    store.put(parent.key + "/operation", current, revision)
    child_lease = acquire(store, child.key + "/lock")
    try:
        prior, version = store.read(child_key)
        if prior:
            if prior.get("adoption_parent") != binding or prior["id"] != child_id:
                raise Blocked("ADOPTION_CHILD_ALREADY_ADMITTED")
            return prior
        value = {
            **current,
            "id": child_id,
            "candidate": child.model_dump(),
            "head": child.head_sha,
            "base": child.base_sha,
            "stage": "usefulness",
            "owned_heads": [child.head_sha],
            "adoption_parent": binding,
            "capabilities": sorted((set(current["capabilities"]) - {"adopt"}) | {"repair"}),
            "issue_evidence": None,
        }
        for name in ("adoption_child", "active_execution", "last_evaluation", "live_failure"):
            value.pop(name, None)
        child_lease.assert_owned()
        store.put(child_key, value, version)
        return value
    finally:
        child_lease.release()


def merged_replacement(api, store, binding, child, pr):
    from .lifecycle import publish as publish_status

    parent = Candidate.model_validate(binding["candidate"])
    operation, _ = store.read(parent.key + "/operation")
    if (
        not operation
        or operation["id"] != binding["request_id"]
        or operation.get("adoption_child") != binding
    ):
        raise Blocked("ADOPTION_PARENT_CHANGED")
    proposal, _ = store.read(binding["record"] + "/adoption")
    if (
        not proposal
        or digest(proposal) != binding["proposal"]
        or child.pr_id != binding["child"]["pr_id"]
    ):
        raise Blocked("ADOPTION_COVERAGE_CHANGED")
    validate(proposal, proposal["plan"]["reviewed_paths"])
    # Full child review/CI/live qualification binds its final tree, including later repairs.
    child_op, _ = store.read(child.key + "/operation")
    if (
        not child_op
        or child_op.get("adoption_parent") != binding
        or child_op.get("candidate") != child.model_dump()
    ):
        raise Blocked("ADOPTION_CHILD_CHANGED")
    if not child_op.get("adoption_verified") or child_op["adoption_verified"] != child.model_dump():
        raise Blocked("ADOPTION_MERGED_COVERAGE_UNVERIFIED")
    result_sha = pr.get("merge_commit_sha")
    if (
        not pr.get("merged")
        or api.request("git/commits/" + result_sha)["tree"]["sha"] != child.tree_sha
    ):
        raise Blocked("ADOPTION_MERGED_TREE_CHANGED")
    if (
        api.request("git/ref/heads/" + api.request("")["default_branch"])["object"]["sha"]
        != result_sha
    ):
        raise Blocked("ADOPTION_CURRENT_BASE_REASSESSMENT_REQUIRED")
    raw = api.request(f"pulls/{parent.pr_number}")
    if raw["id"] != parent.pr_id or raw["head"]["sha"] != parent.head_sha:
        raise Blocked("ADOPTION_ORIGINAL_CHANGED")
    close_key = binding["record"] + "/adoption-close"
    pending_close, _ = store.read(close_key)
    if raw["state"] == "closed" and not pending_close:
        return
    from .stages import human_context

    if digest(human_context(api, parent.pr_number, raw)) != binding["rationale_sha256"]:
        raise Blocked("ADOPTION_ORIGINAL_DISCUSSION_CHANGED")
    watermark = max((e["id"] for e in binding["state_events"]), default=0)
    events = [
        e
        for e in api.pages(f"issues/{parent.pr_number}/events")
        if e.get("event") in {"closed", "reopened"}
    ]
    if (not pending_close and events != binding["state_events"]) or any(
        e["id"] > watermark and e["event"] == "reopened" for e in events
    ):
        raise Blocked("ADOPTION_ORIGINAL_STATE_CHANGED")
    if "close" not in set(operation["capabilities"]) & set(
        requests.capabilities(parent, operation["operation"])
    ):
        raise Blocked("ADOPTION_CLOSURE_NOT_AUTHORIZED")
    from .controller import Heartbeat

    lease = acquire(store, parent.key + "/lock")
    heartbeat = Heartbeat(lease)
    heartbeat.__enter__()
    try:
        intent = {"parent": parent.model_dump(), "child": child.model_dump(), "result": result_sha}

        def fresh():
            lease.assert_owned()
            current, _ = store.read(parent.key + "/operation")
            if "close" not in set(operation["capabilities"]) & set(
                requests.capabilities(parent, operation["operation"])
            ):
                raise Blocked("ADOPTION_CLOSURE_NOT_AUTHORIZED")
            requests.pr_scope(
                store, parent, generation=operation["rollout_generation"], capability="close"
            )
            if (
                not current
                or current["id"] != binding["request_id"]
                or current.get("adoption_child") != binding
            ):
                raise Blocked("ADOPTION_PARENT_CHANGED")
            row = api.request(f"pulls/{parent.pr_number}")
            if row["id"] != parent.pr_id or row["head"]["sha"] != parent.head_sha:
                raise Blocked("ADOPTION_ORIGINAL_CHANGED")
            if digest(human_context(api, parent.pr_number, row)) != binding["rationale_sha256"]:
                raise Blocked("ADOPTION_ORIGINAL_DISCUSSION_CHANGED")
            events = api.pages(f"issues/{parent.pr_number}/events")
            if any(e["id"] > watermark and e.get("event") == "reopened" for e in events):
                raise Blocked("ADOPTION_ORIGINAL_STATE_CHANGED")
            if (
                api.request("git/ref/heads/" + api.request("")["default_branch"])["object"]["sha"]
                != result_sha
            ):
                raise Blocked("ADOPTION_CURRENT_BASE_REASSESSMENT_REQUIRED")
            return row

        def observed(_):
            row = fresh()
            return {"closed": True} if row["state"] == "closed" else None

        if not pending_close:
            fresh()
            lease.assert_owned()
            api.comment(
                parent.pr_number,
                "ADOPTION_COMPLETE",
                detail=f"Accepted features were incorporated and verified in merged PR #{child.pr_number}; closing this original PR.",
            )
            fresh()

        def close(_):
            # Re-read immediately before the close; no contributor branch mutation.
            if observed(None):
                raise Blocked("ADOPTION_ORIGINAL_STATE_CHANGED")
            lease.assert_owned()
            api.request(f"pulls/{parent.pr_number}", method="PATCH", body={"state": "closed"})
            if not observed(None):
                raise Blocked("ADOPTION_CLOSE_NOT_VERIFIED")
            return {"closed": True}

        effect(store, lease, close_key, intent, observed, close)
        requests.finish(store, lease, parent, binding["request_id"], "ADOPTION_COMPLETE")
        publish_status(
            api, parent, "ADOPTION_COMPLETE", expected_state="closed", guard=lease.assert_owned
        )
    finally:
        heartbeat.__exit__(None, None, None)
        lease.release()


def renew(api, store, candidate, operation, env, *, now):
    """A fresh human process request renews the same campaign, never another PR."""
    import copy

    from .live import require_cleanup
    from .rollout import current as rollout

    selected, _ = store.read(candidate.key + "/operation")
    if not selected or operation != "process":
        raise Blocked("REPLACEMENT_INHERITED_REQUEST_REQUIRED")
    binding = selected.get("adoption_parent") or selected.get("adoption_child")
    if not binding:
        raise Blocked("REPLACEMENT_INHERITED_REQUEST_REQUIRED")
    parent = Candidate.model_validate(binding["candidate"])
    child, pr = api.candidate(binding["child"]["pr_number"])
    if pr["state"] != "open" or pr["draft"] or child.pr_id != binding["child"]["pr_id"]:
        raise Blocked("ADOPTION_CHILD_NOT_READY")
    renewal_key = (
        parent.key
        + "/adoption-renewals/"
        + digest({"run": env["GITHUB_RUN_ID"], "parent": parent.pr_id})
    )
    parent_lease = acquire(store, parent.key + "/lock")
    try:
        child_lease = acquire(store, child.key + "/lock")
        try:
            journal, _ = store.read(renewal_key)
            old_parent, pv = store.read(parent.key + "/operation")
            old_child, cv = store.read(child.key + "/operation")
            if not journal:
                if old_child and old_child["status"] == "active" and old_child["deadline"] > now:
                    return old_child
                if not old_child or old_parent.get("adoption_child") != old_child.get(
                    "adoption_parent"
                ):
                    raise Blocked("ADOPTION_AUTHORITY_CHANGED")
                if rollout(store, parent.repository_id).phase != "general":
                    raise Blocked("ADOPTION_ROLLOUT_NOT_ADMITTED")
                for target in (parent, child):
                    require_cleanup(store, target)
                    if requests.unsettled(store, target):
                        raise Blocked("PR_EFFECT_RECONCILIATION_REQUIRED")
                original = api.request(f"pulls/{parent.pr_number}")
                if original["head"]["sha"] != parent.head_sha or original["state"] != "open":
                    raise Blocked("ADOPTION_ORIGINAL_CHANGED")
                parent_id = digest({"parent": parent.pr_id, "manual_run": env["GITHUB_RUN_ID"]})
                updated = {**binding, "request_id": parent_id}
                child_id = digest(updated)
                generation = rollout(store, parent.repository_id).generation
                caps = set(old_parent["capabilities"]) & set(
                    requests.capabilities(parent, "process", env)
                )
                parent_value = {
                    **old_parent,
                    "id": parent_id,
                    "origin": "manual",
                    "status": "active",
                    "outcome": None,
                    "deadline": now + 10800,
                    "attempts": 0,
                    "rollout_generation": generation,
                    "capabilities": sorted(caps),
                    "adoption_child": updated,
                    "stage": "adoption_waiting",
                }
                child_caps = (set(old_child["capabilities"]) & caps) - {"adopt"}
                if "adopt" in caps:
                    child_caps.add("repair")
                child_value = {
                    **old_child,
                    "id": child_id,
                    "origin": "manual",
                    "status": "active",
                    "outcome": None,
                    "deadline": parent_value["deadline"],
                    "attempts": 0,
                    "rollout_generation": generation,
                    "capabilities": sorted(child_caps),
                    "adoption_parent": updated,
                    "stage": "usefulness",
                    "candidate": child.model_dump(),
                    "head": child.head_sha,
                    "base": child.base_sha,
                    "issue_evidence": None,
                }
                for record in (parent_value, child_value):
                    for field in ("active_execution", "last_evaluation", "adoption_verified"):
                        record.pop(field, None)
                journal = {"before": [old_parent, old_child], "after": [parent_value, child_value]}
                store.put(renewal_key, journal, None)
            for target, lease, before, after in zip(
                (parent, child),
                (parent_lease, child_lease),
                journal["before"],
                journal["after"],
                strict=True,
            ):
                retained, version = store.read(target.key + "/operation")
                if retained == after:
                    continue
                if retained != before:
                    raise Blocked("ADOPTION_RENEWAL_CHANGED")
                history = target.key + "/requests/" + before["id"]
                saved, _ = store.read(history)
                if saved is None:
                    store.put(history, before, None)
                elif saved != before:
                    raise Blocked("REQUEST_HISTORY_CHANGED")
                lease.assert_owned()
                store.put(target.key + "/operation", copy.deepcopy(after), version)
            return journal["after"][1]
        finally:
            child_lease.release()
    finally:
        parent_lease.release()
