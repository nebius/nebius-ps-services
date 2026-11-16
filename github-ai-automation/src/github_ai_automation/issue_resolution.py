"""Bounded linked-issue assessment and closure, separate from PR disposition."""

from __future__ import annotations

import json

from .contracts import Blocked, IssueAssessment, digest
from .stages import public_text
from .state import NotSubmitted, effect

QUERY = """query LinkedIssues($owner: String!, $repo: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) { pullRequest(number: $number) {
    closingIssuesReferences(first: 100, after: $cursor) {
      nodes { number databaseId repository { nameWithOwner } }
      pageInfo { hasNextPage endCursor }
    }
  } }
}"""


def linked(api, number):
    owner, repo = api.repository.split("/")
    result, cursor = [], None
    for _ in range(10):
        data = api.graphql(
            QUERY, {"owner": owner, "repo": repo, "number": number, "cursor": cursor}
        )
        page = data["repository"]["pullRequest"]["closingIssuesReferences"]
        for row in page["nodes"]:
            identity = {
                "repository": row["repository"]["nameWithOwner"],
                "number": row["number"],
                "issue_id": row["databaseId"],
            }
            if identity not in result:
                result.append(identity)
        if not page["pageInfo"]["hasNextPage"]:
            return result
        next_cursor = page["pageInfo"]["endCursor"]
        if not next_cursor or next_cursor == cursor:
            raise Blocked("ISSUE_DISCOVERY_INCOMPLETE")
        cursor = next_cursor
    raise Blocked("ISSUE_DISCOVERY_INCOMPLETE")


def observe(api, identity):
    # The built-in token grants no cross-repository issue authority.
    if identity["repository"].casefold() != api.repository.casefold():
        raise Blocked("CROSS_REPOSITORY_ISSUE_UNAVAILABLE")
    number = identity["number"]
    issue = api.request(f"issues/{number}")
    if issue.get("pull_request") or issue["id"] != identity["issue_id"]:
        raise Blocked("ISSUE_IDENTITY_CHANGED")
    comments = [
        {
            "id": c["id"],
            "body": c.get("body", ""),
            "updated_at": c.get("updated_at"),
            "actor": c["user"]["id"],
        }
        for c in api.pages(f"issues/{number}/comments")
        if c.get("user", {}).get("type") == "User"
    ]
    events = [
        {
            "id": e["id"],
            "event": e["event"],
            "actor": e.get("actor", {}).get("id"),
            "created_at": e.get("created_at"),
        }
        for e in api.pages(f"issues/{number}/events")
        if e["event"] in {"closed", "reopened"}
    ]
    scope = {
        **identity,
        "title": issue["title"],
        "body": issue.get("body") or "",
        "comments": comments,
    }
    if len(json.dumps(scope)) > 100_000:
        raise Blocked("ISSUE_CONTEXT_TOO_LARGE")
    return {
        "identity": identity,
        "scope": scope,
        "snapshot_sha256": digest(scope),
        "state": issue["state"],
        "state_reason": issue.get("state_reason"),
        "events": events,
    }


def collect(api, number):
    return [observe(api, identity) for identity in linked(api, number)]


def assess(stages, context, paths, snapshots):
    if not snapshots:
        return None
    assessment = stages.agent(
        "issues",
        IssueAssessment,
        "Use $review-github-issues as a parent-owned read-only adviser. Independently assess each exact linked issue; a bad PR never disproves a valid issue. "
        "Inspect all issue criteria against the accepted base and proposed head. resolved means every criterion is evidenced; use basis=base only if already true on accepted base. "
        "not_planned/duplicate require independent issue justification and basis=base; keep uncertain/unresolved needs open. "
        "No effects or new plan files. Copy the exact identity and snapshot digest, provide criteria and source/test evidence.\n"
        + context
        + "\n"
        + json.dumps(snapshots),
        paths,
    )
    validate(assessment, snapshots)
    return {
        "candidate": stages.candidate.model_dump(),
        "request_id": stages.identity["request_id"],
        "snapshots": snapshots,
        "assessment": assessment.model_dump(),
        "stage_key": stages.key,
    }


def validate(assessment, snapshots):
    expected = {(s["identity"]["repository"], s["identity"]["issue_id"]): s for s in snapshots}
    if len(expected) != len(snapshots) or len(assessment.issues) != len(expected):
        raise Blocked("ISSUE_COVERAGE_INCOMPLETE")
    seen = set()
    for row in assessment.issues:
        key = row.repository, row.issue_id
        snapshot = expected.get(key)
        if (
            key in seen
            or not snapshot
            or row.number != snapshot["identity"]["number"]
            or row.snapshot_sha256 != snapshot["snapshot_sha256"]
        ):
            raise Blocked("ISSUE_ASSESSMENT_IDENTITY_CHANGED")
        if row.disposition in {"not_planned", "duplicate"} and row.basis != "base":
            raise Blocked("ISSUE_REJECTION_REQUIRES_INDEPENDENT_BASIS")
        seen.add(key)


def premerge(api, candidate, evidence, *, inherited_from=None):
    identities = linked(api, candidate.pr_number)
    if inherited_from is not None:
        identities = list(
            {
                (i["repository"], i["issue_id"]): i
                for i in [*identities, *linked(api, inherited_from)]
            }.values()
        )
    if not evidence:
        if identities:
            raise Blocked("ISSUE_RESOLUTION_MISSING")
        return
    if evidence["candidate"] != candidate.model_dump():
        raise Blocked("ISSUE_CANDIDATE_CHANGED")
    assessment = IssueAssessment.model_validate(evidence["assessment"])
    validate(assessment, evidence["snapshots"])
    if sorted(identities, key=lambda x: (x["repository"], x["number"])) != sorted(
        [s["identity"] for s in evidence["snapshots"]], key=lambda x: (x["repository"], x["number"])
    ):
        raise Blocked("ISSUE_LINKS_CHANGED")
    for row in assessment.issues:
        current = observe(
            api, {"repository": row.repository, "number": row.number, "issue_id": row.issue_id}
        )
        frozen = next(
            s
            for s in evidence["snapshots"]
            if s["identity"]["issue_id"] == row.issue_id
            and s["identity"]["repository"] == row.repository
        )
        if (
            current["snapshot_sha256"] != row.snapshot_sha256
            or current["events"] != frozen["events"]
            or row.disposition != "resolved"
        ):
            raise Blocked("ISSUE_RESOLUTION_INCOMPLETE")


def complete_issues(api, store, lease, completion_key, candidate, pr, evidence, *, authorize):
    def before_submission(*capabilities):
        try:
            authorize(*capabilities)
        except Blocked as exc:
            # The permission check precedes the HTTP call, so this is a known
            # non-submission, not an ambiguous remote outcome.
            raise NotSubmitted() from exc

    if evidence["candidate"] != candidate.model_dump():
        raise Blocked("ISSUE_CANDIDATE_CHANGED")
    assessment = IssueAssessment.model_validate(evidence["assessment"])
    validate(assessment, evidence["snapshots"])
    result = []
    frozen_by_id = {
        (s["identity"]["repository"], s["identity"]["issue_id"]): s for s in evidence["snapshots"]
    }
    for row in assessment.issues:
        frozen = frozen_by_id[row.repository, row.issue_id]
        identity = frozen["identity"]
        current = observe(api, identity)
        outcome = "open"
        key = completion_key + "/issues/" + str(row.issue_id)
        closure, _ = store.read(key)
        pending_close = closure and closure.get("status") == "pending"
        if current["state"] == "closed" and not pending_close:
            result.append({**identity, "outcome": "observed_closed"})
            continue
        watermark = max((e["id"] for e in frozen["events"]), default=0)
        if current["snapshot_sha256"] != row.snapshot_sha256 or any(
            e["id"] > watermark and e["event"] == "reopened" for e in current["events"]
        ):
            result.append({**identity, "outcome": "reassessment_required"})
            continue
        eligible = row.disposition in {"resolved", "not_planned", "duplicate"} and (
            row.basis == "base" or bool(pr.get("merged"))
        )
        if not eligible:
            result.append({**identity, "outcome": outcome})
            continue
        # Head evidence only becomes resolution after exact incorporation. A later
        # target change requires a new semantic assessment, including reverts.
        expected_base = pr.get("merge_commit_sha") if pr.get("merged") else candidate.base_sha
        default = api.request("")["default_branch"]
        tip = api.request("git/ref/heads/" + default)["object"]["sha"]
        if current["state"] != "closed" and (
            not expected_base
            or tip != expected_base
            or (
                pr.get("merged")
                and api.request("git/commits/" + expected_base)["tree"]["sha"] != candidate.tree_sha
            )
        ):
            result.append({**identity, "outcome": "reassessment_required"})
            continue
        if row.disposition == "duplicate" and current["state"] != "closed":
            matches = [
                i
                for i in api.pages("issues?state=all")
                if i.get("id") == row.duplicate_issue_id and not i.get("pull_request")
            ]
            if len(matches) != 1 or matches[0]["id"] == row.issue_id:
                raise Blocked("ISSUE_DUPLICATE_UNVERIFIED")
        reason = {"resolved": "completed", "not_planned": "not_planned", "duplicate": "duplicate"}[
            row.disposition
        ]
        body = (
            "Issue assessment after PR #"
            + str(candidate.pr_number)
            + ": "
            + public_text(row.rationale)
            + "\nResolution: "
            + reason
            + "."
        )
        intent = {
            "identity": identity,
            "scope": row.snapshot_sha256,
            "reason": reason,
            "watermark": watermark,
            "body": body,
        }

        def fresh(identity=identity, row=row, watermark=watermark):
            lease.assert_owned()
            now = observe(api, identity)
            if now["snapshot_sha256"] != row.snapshot_sha256 or any(
                e["id"] > watermark and e["event"] == "reopened" for e in now["events"]
            ):
                raise Blocked("ISSUE_SCOPE_OR_STATE_CHANGED")
            return now

        def observed(_, fresh=fresh, reason=reason):
            now = fresh()
            return (
                {"closed": True}
                if now["state"] == "closed" and now["state_reason"] == reason
                else None
            )

        def close(
            _,
            fresh=fresh,
            observed=observed,
            default=default,
            expected_base=expected_base,
            reason=reason,
            row=row,
        ):
            now = fresh()
            if now["state"] != "open":
                raise Blocked("ISSUE_STATE_CHANGED")
            fresh()
            if api.request("git/ref/heads/" + default)["object"]["sha"] != expected_base:
                raise Blocked("ISSUE_BASE_CHANGED")
            update = {"state": "closed", "state_reason": reason}
            if row.duplicate_issue_id:
                update["duplicate_issue_id"] = row.duplicate_issue_id
            before_submission("close")
            lease.assert_owned()
            api.request(f"issues/{row.number}", method="PATCH", body=update)
            verified = observed(None)
            if not verified:
                raise Blocked("ISSUE_CLOSURE_NOT_VERIFIED")
            return verified

        def comment_seen(_, row=row, body=body):
            matches = [
                c
                for c in api.pages(f"issues/{row.number}/comments")
                if c.get("user", {}).get("id") == 41898282 and c.get("body") == body
            ]
            return {"id": matches[-1]["id"]} if matches else None

        def comment(_, fresh=fresh, comment_seen=comment_seen, row=row, body=body):
            fresh()
            before_submission("comment", "close")
            lease.assert_owned()
            api.request(f"issues/{row.number}/comments", method="POST", body={"body": body})
            found = comment_seen(None)
            if not found:
                raise Blocked("ISSUE_COMMENT_NOT_VERIFIED")
            return found

        if current["state"] != "closed":
            authorize("comment", "close")
        effect(store, lease, key + "/comment", intent, comment_seen, comment)
        if current["state"] != "closed":
            authorize("close")
        effect(store, lease, key, intent, observed, close)
        result.append({**identity, "outcome": "closed", "reason": reason})
    return result
