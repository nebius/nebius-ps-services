"""Reconcile reviewed pre-merge repairs without granting publication authority."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from urllib.parse import quote

from release_checkpoint import SHA, ReleaseError


def reconcile_successor(value, github, *, expected_merge, pr, branch, head, review_id, review_task):
    """Select an explicitly reviewed follow-up before any publication effects."""
    if (
        not isinstance(expected_merge, str)
        or not SHA.fullmatch(expected_merge)
        or not isinstance(head, str)
        or not SHA.fullmatch(head)
        or type(pr) is not int
        or pr <= 0
        or type(review_id) is not int
        or review_id <= 0
        or not isinstance(branch, str)
        or not branch
        or value["scope"]["host"] != "github.com"
        or any(value.get(k) for k in ("tag_object", "tag_payload", "run", "complete"))
    ):
        raise ReleaseError("Successor requires exact unpublished selection and review identities")
    history = value.get("successor_reconciliations", [])
    if (
        not isinstance(history, list)
        or len(history) > 100
        or any(
            not isinstance(row, dict)
            or any(not isinstance(row.get(key), dict) for key in ("previous", "next", "evidence"))
            for row in history
        )
    ):
        raise ReleaseError("Successor history requires owner inspection")
    selected = {"pr": pr, "branch": branch, "head": head, "review_id": review_id}
    previous = history[-1] if history and history[-1].get("next") == selected else None
    retry = previous is not None and value.get("merge") is None
    old = previous["previous"] if retry else value
    if (
        old.get("merge") != expected_merge
        or old.get("pr") == pr
        or not old.get("pr")
        or not old.get("head")
        or not old.get("branch")
        or (retry and any(value.get(k) != selected[k] for k in ("pr", "branch", "head")))
        or (not retry and len(history) >= 100)
    ):
        raise ReleaseError("Successor does not match the original merged checkpoint")
    for checkpoint in (old, value):
        qualification = checkpoint.get("qualification")
        if qualification is not None and (
            not isinstance(qualification, dict)
            or set(qualification) - {"profile_digest", "operations"}
            or not isinstance(qualification.get("operations"), dict)
            or qualification["operations"]
        ):
            raise ReleaseError("Settle qualification effects before selecting a successor")
    candidate = {**value, "pr": pr, "branch": branch, "head": head}
    proof = execution_proof(candidate, review_task=review_task, head=head, ancestor=expected_merge)
    owner = review_owner()

    def absence():
        if github.tag(value["tag"]) != (None, None) or github.release_exists(value["tag"]):
            raise ReleaseError("A remote tag or release prevents successor selection")

    def observe():
        repo = github.api("")
        repo_id = repo["id"]
        if (
            type(repo_id) is not int
            or repo_id <= 0
            or repo["full_name"] != value["scope"]["repo"]
            or repo["default_branch"] != value["base"]
        ):
            raise ReleaseError("Successor repository or default identity changed")
        original = github.api(f"pulls/{old['pr']}")
        successor = github.api(f"pulls/{pr}")
        if (
            original["number"] != old["pr"]
            or original["state"] != "closed"
            or original["merged"] is not True
            or not isinstance(original.get("merged_at"), str)
            or not original["merged_at"]
            or original["head"]["sha"] != old["head"]
            or original["head"]["ref"] != old["branch"]
            or original["base"]["ref"] != value["base"]
            or successor["number"] != pr
            or successor["head"]["ref"] != branch
            or any(type(item["id"]) is not int or item["id"] <= 0 for item in (original, successor))
            or any(
                type(item[side]["repo"]["id"]) is not int
                or item[side]["repo"]["id"] != repo_id
                or item[side]["repo"]["full_name"] != repo["full_name"]
                for item in (original, successor)
                for side in ("head", "base")
            )
        ):
            raise ReleaseError("Original or successor PR identity changed")
        owner.identity(successor, value["scope"]["repo"], head, value["base"])
        merged = owner.GitHub(value["scope"]["repo"]).merged_commit(
            old["pr"], old["head"], value["base"]
        )
        if merged != expected_merge:
            raise ReleaseError("Original authoritative merge result changed")
        base = successor["base"]["sha"]
        tip = github.api(f"git/ref/heads/{quote(value['base'], safe='')}")["object"]["sha"]
        if not isinstance(base, str) or not SHA.fullmatch(base) or tip != base:
            raise ReleaseError(
                "Synchronize and review the current default before selecting a successor"
            )
        for ancestor, descendant in ((expected_merge, base), (base, head)):
            comparison = github.api(f"compare/{ancestor}...{descendant}")
            if (
                comparison["status"] not in {"ahead", "identical"}
                or comparison["merge_base_commit"]["sha"] != ancestor
            ):
                raise ReleaseError("Successor must include the original merge and current default")
        allowed = json.loads(github.api("actions/variables/MERGE_OPERATOR_IDS")["value"])
        if (
            not isinstance(allowed, list)
            or not allowed
            or any(type(item) is not int or item <= 0 for item in allowed)
        ):
            raise ReleaseError("Invalid configured merge operator IDs")
        review = github.api(f"pulls/{pr}/reviews/{review_id}")
        if type(review.get("id")) is not int or review["id"] != review_id:
            raise ReleaseError("Successor review ID mismatch")
        owner.review_record(review, successor, allowed)
        return {
            "repository_id": repo_id,
            "previous_pr_id": original["id"],
            "pr_id": successor["id"],
            "base_sha": base,
            "merged_at": original["merged_at"],
            "execution_task_sha256": proof["task_sha256"],
        }

    try:
        absence()
        record = observe()
        if observe() != record:
            raise ReleaseError("Successor identity changed during reconciliation")
        if (
            execution_proof(candidate, review_task=review_task, head=head, ancestor=expected_merge)
            != proof
        ):
            raise ReleaseError("Successor execution changed during reconciliation")
        absence()
    except (owner.Blocked, owner.ApiFailure, KeyError, TypeError, ValueError) as error:
        raise ReleaseError("Authoritative successor review evidence is unavailable") from error
    if retry:
        if previous["evidence"] != record:
            raise ReleaseError("Conflicting successor retry")
        return copy.deepcopy(value)
    replaced = (
        "pr",
        "branch",
        "head",
        "merge",
        "qualification",
        "head_reconciliations",
        "merged_reconciliation",
        "observed_queue_entry",
    )
    prior_selection = {key: copy.deepcopy(value[key]) for key in replaced if key in value}
    result = copy.deepcopy(value)
    for key in replaced:
        result.pop(key, None)
    result.update(pr=pr, branch=branch, head=head, merge=None)
    result["successor_reconciliations"] = [
        *history,
        {
            "previous": prior_selection,
            "next": selected,
            "evidence": record,
        },
    ]
    return result

def review_owner():
    """Load the installed sibling owner, never code from the release checkout."""
    path = Path(__file__).resolve().parents[2] / "merge-pr/scripts/merge_gate.py"
    if not path.is_file():
        raise ReleaseError(
            "Install the canonical merge-pr sibling before reconciliation"
        )
    spec = importlib.util.spec_from_file_location("release_merge_owner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def execution_proof(value, *, review_task, head=None, ancestor=None):
    """Consume canonical private execution evidence without changing release scope."""
    if not review_task:
        raise ReleaseError("Private review execution evidence is required for repaired PR heads")
    path = Path(__file__).resolve().parents[2] / "commit/scripts/commit_transaction.py"
    if not path.is_file():
        raise ReleaseError("Install the canonical commit owner before reconciliation")
    spec = importlib.util.spec_from_file_location("release_commit_owner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        return module.verify_review_execution(
            review_task, source_root=value["scope"]["root"], branch=value["branch"],
            head=head, repository=value["scope"]["repo"], pr=value["pr"], ancestor=ancestor,
        )
    except (module.TransactionError, module._pr.PublicationError, OSError, ValueError) as error:
        raise ReleaseError("Private review execution proof is unavailable or stale") from error


def reconcile_merged(value, github, *, expected_head, head, review_task):
    """Recover an externally merged canonical repair, without replaying effects."""
    previous = value.get("merged_reconciliation")
    if (
        not isinstance(expected_head, str) or not SHA.fullmatch(expected_head)
        or not isinstance(head, str) or not SHA.fullmatch(head)
        or expected_head == head or not value.get("pr")
        or value["scope"]["host"] != "github.com"
        or any(value.get(key) for key in ("tag_object", "tag_payload", "run", "complete"))
        or (previous is None and (value["head"] != expected_head or value["merge"]))
        or (previous is not None and (
            not isinstance(previous, dict) or value["head"] != head
            or previous.get("previous_head") != expected_head
            or previous.get("head") != head or previous.get("merge") != value["merge"]
        ))
    ):
        raise ReleaseError("Merged recovery requires the original unpublished checkpoint and exact heads")

    proof = execution_proof(value, review_task=review_task, head=head, ancestor=expected_head)
    owner = review_owner()

    def observe():
        repo = github.api("")
        pr = github.api(f"pulls/{value['pr']}")
        repo_id = repo["id"]
        if (
            type(repo_id) is not int or repo_id <= 0
            or repo["full_name"] != value["scope"]["repo"]
            or repo["default_branch"] != value["base"]
            or type(pr["id"]) is not int or pr["id"] <= 0
            or type(pr["number"]) is not int or pr["number"] != value["pr"]
            or pr["state"] != "closed" or pr["merged"] is not True
            or not isinstance(pr["merged_at"], str) or not pr["merged_at"]
            or pr["head"]["ref"] != value["branch"] or pr["head"]["sha"] != head
            or pr["base"]["ref"] != value["base"]
            or any(pr[side]["repo"]["id"] != repo_id
                   or pr[side]["repo"]["full_name"] != repo["full_name"]
                   for side in ("head", "base"))
        ):
            raise ReleaseError("Merged PR or repository identity changed")
        merged = owner.GitHub(value["scope"]["repo"]).merged_commit(value["pr"], head, value["base"])
        tip = github.api(f"git/ref/heads/{quote(value['base'], safe='')}")["object"]["sha"]
        if not isinstance(tip, str) or not SHA.fullmatch(tip):
            raise ReleaseError("Remote default tip is unavailable")
        comparison = github.api(f"compare/{merged}...{tip}")
        if (comparison["status"] not in {"ahead", "identical"}
                or comparison["merge_base_commit"]["sha"] != merged):
            raise ReleaseError("Merged result is not contained in the remote default")
        return {
            "previous_head": expected_head, "head": head, "merge": merged,
            "repository_id": repo_id, "pr_id": pr["id"],
            "merged_at": pr["merged_at"], "target_tip": tip,
            "execution_task_sha256": proof["task_sha256"],
        }

    try:
        if github.tag(value["tag"]) != (None, None) or github.release_exists(value["tag"]):
            raise ReleaseError("A remote tag or release prevents merged recovery")
        record = observe()
        fresh = observe()
        # Normal default advancement is allowed; both immutable tips contain the result.
        def identity(item):
            return {key: val for key, val in item.items() if key != "target_tip"}
        if identity(record) != identity(fresh) or (previous is not None and identity(previous) != identity(record)):
            raise ReleaseError("Merged recovery identity changed during observation")
        if execution_proof(value, review_task=review_task, head=head, ancestor=expected_head) != proof:
            raise ReleaseError("Registered execution changed during merged recovery")
        if github.tag(value["tag"]) != (None, None) or github.release_exists(value["tag"]):
            raise ReleaseError("A remote tag or release appeared during merged recovery")
    except (owner.Blocked, owner.ApiFailure, KeyError, TypeError, ValueError) as exc:
        raise ReleaseError("Authoritative merged recovery evidence is unavailable") from exc
    return {**value, "head": head, "merge": record["merge"],
            "merged_reconciliation": previous if previous is not None else record}


def reconcile_pr(value, github, git, *, expected_head, head, review_id, review_task):
    """Return a new checkpoint only after exact, read-only reconciliation."""
    if (
        not expected_head
        or not head
        or not SHA.fullmatch(expected_head)
        or not SHA.fullmatch(head)
        or expected_head == head
        or type(review_id) is not int
        or review_id <= 0
        or not value["pr"]
        or value["head"] != expected_head
    ):
        raise ReleaseError(
            "Reconciliation requires the frozen old head, new head and review ID"
        )
    if any(value[k] for k in ("merge", "tag_object", "run", "complete")):
        raise ReleaseError("Cannot reconcile after merge or tag publication evidence")
    if value["scope"]["host"] != "github.com":
        raise ReleaseError(
            "The canonical Actions review owner supports github.com only"
        )
    history = value.get("head_reconciliations", [])
    if not isinstance(history, list) or len(history) >= 100:
        raise ReleaseError("Reconciliation history requires owner inspection")
    root = Path(execution_proof(value, review_task=review_task, head=head)["repo_root"])

    def checkout():
        proof = execution_proof(value, review_task=review_task, head=head, ancestor=expected_head)
        if (
            proof["repo_root"] != str(root)
            or proof["head"] != head
        ):
            raise ReleaseError(
                "Reconciliation requires the clean registered private checkout at the reviewed head"
            )

    checkout()
    if github.tag(value["tag"]) != (None, None):
        raise ReleaseError("A remote release tag prevents head reconciliation")
    owner = review_owner()
    try:
        default = github.api("")["default_branch"]
        if default != value["base"]:
            raise ReleaseError(
                "Repository default changed; reconcile the release target"
            )
        pr = github.api(f"pulls/{value['pr']}")
        owner.identity(pr, value["scope"]["repo"], head, default)
        if pr["number"] != value["pr"] or pr["head"]["ref"] != value["branch"]:
            raise ReleaseError("PR identity changed during reconciliation")
        base = pr["base"]["sha"]
        comparison = github.api(f"compare/{base}...{head}")
        if comparison["status"] not in {"ahead", "identical"}:
            raise ReleaseError("Synchronize the current default and review again")
        allowed = json.loads(
            github.api("actions/variables/MERGE_OPERATOR_IDS")["value"]
        )
        if (
            not isinstance(allowed, list)
            or not allowed
            or any(type(item) is not int or item <= 0 for item in allowed)
        ):
            raise ReleaseError("Invalid configured merge operator IDs")
        review = github.api(f"pulls/{value['pr']}/reviews/{review_id}")
        if type(review.get("id")) is not int or review["id"] != review_id:
            raise ReleaseError("Review ID mismatch")
        owner.review_record(review, pr, allowed)
        # A checkpoint is progress evidence. The broker independently repeats
        # full review, CI and protection admission before any merge effect.
        fresh = github.api(f"pulls/{value['pr']}")
        owner.identity(fresh, value["scope"]["repo"], head, default)
        if (
            fresh["number"] != value["pr"]
            or fresh["head"]["ref"] != value["branch"]
            or fresh["base"]["sha"] != base
            or github.api("")["default_branch"] != default
        ):
            raise ReleaseError("PR head/base changed during reconciliation")
        if github.tag(value["tag"]) != (None, None):
            raise ReleaseError("A remote release tag appeared during reconciliation")
        checkout()
    except (owner.Blocked, KeyError, TypeError, ValueError) as exc:
        raise ReleaseError(
            "Fresh configured-operator review of the exact head/base is required"
        ) from exc
    return {
        **value,
        "head": head,
        "head_reconciliations": [
            *history,
            {
                "previous_head": expected_head,
                "head": head,
                "base": base,
                "review_id": review_id,
            },
        ],
    }
