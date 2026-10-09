"""Reconcile reviewed pre-merge repairs without granting publication authority."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

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


def reconcile_pr(value, github, git, *, expected_head, head, review_id):
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
    root = Path(value["scope"]["root"])

    def checkout():
        if (
            git(root, "branch", "--show-current") != value["branch"]
            or git(root, "rev-parse", "HEAD") != head
            or git(root, "status", "--porcelain")
        ):
            raise ReleaseError(
                "Reconciliation requires the clean active checkout at the reviewed head"
            )

    checkout()
    git(root, "merge-base", "--is-ancestor", expected_head, head)
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
