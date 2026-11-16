"""Revalidated objective or independently corroborated contribution closure."""

from . import requests
from .contracts import Blocked, Producer, Usefulness, digest
from .state import effect


def proof(api, candidate):
    target = api.request("")["default_branch"]
    comparison = api.request(f"compare/{candidate.base_sha}...{candidate.head_sha}")
    if comparison["status"] in {"behind", "identical"}:
        return {
            "kind": "fully_incorporated",
            "head": candidate.head_sha,
            "base": candidate.base_sha,
        }
    # Full tree and base equality, same repository, and an older open PR.
    for other in api.pages("pulls?state=open"):
        if (
            other["id"] >= candidate.pr_id
            or other["base"]["sha"] != candidate.base_sha
            or other["base"]["ref"] != target
            or other["base"]["repo"]["id"] != candidate.repository_id
            or not other["head"].get("repo")
            or other["head"]["repo"]["id"] != candidate.repository_id
        ):
            continue
        commit = api.request(f"git/commits/{other['head']['sha']}")
        if commit["tree"]["sha"] == candidate.tree_sha:
            return {
                "kind": "identical_duplicate",
                "other_pr": other["number"],
                "other_id": other["id"],
                "other_head": other["head"]["sha"],
                "head": candidate.head_sha,
                "base": candidate.base_sha,
            }
    return None


def validate_proof(api, store, candidate, expected, request_id):
    if expected["kind"] != "no_benefit":
        return proof(api, candidate) == expected
    prefix = candidate.key + "/requests/" + request_id + "/stages/"
    if not expected["stage_key"].startswith(prefix):
        return False
    from .stages import human_context

    current_pr = api.request(f"pulls/{candidate.pr_number}")
    rationale = human_context(api, candidate.pr_number, current_pr)
    if digest(rationale) != expected.get("rationale_sha256") or any(
        r["state"] in {"APPROVED", "CHANGES_REQUESTED"} for r in rationale["reviews"]
    ):
        return False
    paths = api.paths(candidate)
    if (
        paths != expected["paths"]
        or expected["head"] != candidate.head_sha
        or expected["base"] != candidate.base_sha
    ):
        return False
    results = []
    identities = []
    for name in ("value", "value-confirm"):
        entry, _ = store.read(expected["stage_key"] + "/" + name)
        if (
            not entry
            or entry["identity"]["candidate"] != candidate.model_dump()
            or entry["identity"]["request_id"] != request_id
        ):
            return False
        api.producer(Producer.model_validate(entry["producer"]))
        identities.append(entry["identity"])
        results.append(Usefulness.model_validate(entry["result"]))
    from .stages import no_benefit

    return (
        identities[0] == identities[1]
        and [digest(r) for r in results] == expected["assessments"]
        and no_benefit(*results, paths)
    )


def state_events(api, number):
    return [
        e
        for e in api.pages(f"issues/{number}/events")
        if e["event"] in {"closed", "reopened", "merged"}
    ]


def close(api, store, lease, candidate, expected, request_id):
    requests.require_capability(store, candidate, request_id, "close")
    current, pr = api.candidate(candidate.pr_number)
    if (
        current != candidate
        or pr["merged"]
        or pr["state"] != "open"
        or not validate_proof(api, store, current, expected, request_id)
    ):
        raise Blocked("CLOSURE_PROOF_CHANGED")
    detail = (
        "Two independent complete assessments found no concrete benefit in the current change. "
        "A small change is welcome when it improves correctness, clarity, tests or another specific outcome. "
        "Explain the benefit or revise the contribution, then request a new review."
        if expected["kind"] == "no_benefit"
        else "The full change is already incorporated or duplicates an older open PR with the identical tree and base."
    )
    if expected["kind"] == "no_benefit":
        from .stages import public_text

        assessment, _ = store.read(expected["stage_key"] + "/value")
        detail = public_text(assessment["result"]["explanation"]) + "\n\n" + detail
    api.comment(
        candidate.pr_number,
        "NO_MEANINGFUL_BENEFIT" if expected["kind"] == "no_benefit" else "PR_REDUNDANT",
        detail=detail,
    )
    events = state_events(api, candidate.pr_number)
    before = max((e["id"] for e in events), default=0)
    intent = {"proof": expected, "candidate": candidate.model_dump(), "before_event": before}

    def observe(value):
        # A closed state alone cannot attribute the effect to this request.
        return None

    def execute(value):
        requests.require_capability(store, candidate, request_id, "close")
        current, pr = api.candidate(candidate.pr_number)
        if (
            current != candidate
            or pr["state"] != "open"
            or not validate_proof(api, store, current, expected, request_id)
        ):
            raise Blocked("CLOSURE_PROOF_CHANGED")
        lease.assert_owned()
        response = api.request(
            f"pulls/{candidate.pr_number}", method="PATCH", body={"state": "closed"}
        )
        current, pr = api.candidate(candidate.pr_number)
        after = [
            e for e in state_events(api, candidate.pr_number) if e["id"] > value["before_event"]
        ]
        owned = (
            response.get("state") == "closed"
            and len(after) == 1
            and after[0]["event"] == "closed"
            and after[0].get("actor", {}).get("id") == 41898282
            and after[0].get("created_at") == response.get("closed_at")
        )
        if current != candidate or not validate_proof(api, store, current, expected, request_id):
            # Never undo a later human close/reopen. Ambiguous ownership is a
            # reconciliation stop rather than permission to change their state.
            if owned and pr["state"] == "closed" and not pr["merged"]:
                latest = state_events(api, candidate.pr_number)
                if latest and latest[-1]["id"] == after[0]["id"]:
                    api.request(
                        f"pulls/{candidate.pr_number}", method="PATCH", body={"state": "open"}
                    )
                    _, reopened = api.candidate(candidate.pr_number)
                    if reopened["state"] == "open" and not reopened["merged"]:
                        return {"closed": False, "compensated": True, "event_id": after[0]["id"]}
                    raise Blocked("CLOSURE_OUTCOME_RECONCILIATION_REQUIRED")
            raise Blocked("CLOSURE_OUTCOME_RECONCILIATION_REQUIRED")
        if not owned:
            raise Blocked("CLOSURE_OUTCOME_RECONCILIATION_REQUIRED")
        return {"closed": True, "event_id": after[0]["id"]}

    result = effect(
        store,
        lease,
        candidate.key + "/requests/" + request_id + "/close/" + digest(candidate),
        intent,
        observe,
        execute,
    )
    if result.get("compensated"):
        raise Blocked("CLOSURE_HEAD_CHANGED_REOPENED")
    return result
