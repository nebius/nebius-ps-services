"""Reconcile reviewed pre-merge repairs without granting publication authority."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from urllib.parse import quote

from release_checkpoint import SHA, ReleaseError


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
