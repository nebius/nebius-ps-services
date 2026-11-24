#!/usr/bin/env python3
"""Collect read-only evidence; the invoking skill supplies the concise narrative."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import sys

# Ordinary reporting must not create cache files in the installed skill.
sys.dont_write_bytecode = True

from github_api import GitHub, checkout_repository  # noqa: E402
from report_finalization import finalize  # noqa: E402
from report_model import (  # noqa: E402
    Once,
    ReportError,
    UTC,
    build_evidence,
    iso,
    project_selector,
    repositories,
    timestamp,
    row_scope,
    window,
    user_selector,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Read contributor activity and shared backlog, or rank merged-PR authors by LOC Modifications, using existing gh authentication.",
        allow_abbrev=False,
    )
    result.add_argument(
        "--user",
        action=Once,
        metavar="TEXT",
        help="Literal case-insensitive substring of login or public profile name. Default: all human contributors. Shared backlog is unchanged.",
    )
    result.add_argument(
        "--date",
        action=Once,
        help="Start at local midnight YYYY-MM-DD; today or earlier. Default: rolling 90 days.",
    )
    result.add_argument(
        "--project",
        action=Once,
        help="Activity mode only: one repository-relative folder; retain uncertain membership inline.",
    )
    result.add_argument(
        "--repo",
        nargs="+",
        action=Once,
        help="One comma-separated OWNER/REPO list; optional spaces. Default: current checkout.",
    )
    result.add_argument(
        "--loc-modifications-report",
        action=Once,
        nargs=0,
        help="Rank merged-PR authors by overall LOC Modifications (additions plus deletions).",
    )
    result.add_argument(
        "--owner",
        action=Once,
        metavar="OWNER",
        help="All visible repositories owned by one organization or user; excludes --repo.",
    )
    result.add_argument(
        "--top",
        action=Once,
        metavar="K",
        help="Show at most K contributors after aggregation/filtering; LOC mode only. Default: all.",
    )
    # Internal bridge for the invoking agent; not a public skill flag.
    result.add_argument("--expected-user-id", type=int, help=argparse.SUPPRESS)
    return result


def collect(
    api: GitHub,
    selected: list[str],
    interval: dict,
    expected_id=None,
    *,
    project=None,
    user=None,
) -> dict:
    user = user_selector(user)
    viewer = api.viewer(expected_id)
    report = {
        "schema_version": 3,
        "viewer": viewer,
        "user_filter": user_selector(user),
        "window": interval,
        "repositories": [],
        "coverage": "complete",
        "limitations": [
            "Activity dates use the frozen interval; Open Backlog includes every currently observed open item regardless of age or actor.",
            "Only currently observable history is available; deleted comments and rewritten commits may be missing.",
            "Commit activity uses committedDate, not push time. States and whole-PR metrics are current observations, not an atomic historical snapshot.",
            "Repository totals count distinct selected completed items plus the shared backlog. User counts overlap and must not be summed; they are not productivity scores.",
            "In progress names recorded human contributors and role/date in the interval, not continuous implementation. The shared backlog is independent of --user.",
            "Folder inference never repairs incomplete file/tree evidence. Uncertain project items remain inline and are counted separately.",
        ],
    }
    if project is not None:
        report["project"] = project
    items = {}
    for repo in selected:
        entry = {
            "repository": repo,
            "collection_coverage": "complete",
            "collection_limits": [],
            "streams": {},
        }
        report["repositories"].append(entry)
        try:
            owner, name = repo.split("/")
            identity = api.query(
                "query($owner:String!, $name:String!) { repository(owner:$owner,name:$name) { id nameWithOwner url } }",
                {"owner": owner, "name": name},
            ).get("repository")
            if not identity:
                raise ReportError(
                    "INACCESSIBLE",
                    "Repository is inaccessible with existing authentication.",
                )
            if identity["nameWithOwner"].lower() != repo.lower():
                raise ReportError(
                    "REPO_IDENTITY_MISMATCH", "Supply the current repository identity."
                )
            entry.update(id=identity["id"], repository=identity["nameWithOwner"])
            candidates_by_id = {}
            # Discover all streams before expensive activity hydration.
            for kind in ("issue", "pr"):
                for stream, start in (("open", None), ("activity", interval["start"])):
                    found = api.candidates(repo, kind, start)
                    entry["streams"][stream + "_" + kind] = {
                        "coverage": "partial" if found["limits"] else "complete",
                        "limits": found["limits"],
                    }
                    entry["collection_limits"].extend(
                        stream + " " + kind + ": " + limit for limit in found["limits"]
                    )
                    for candidate in found["nodes"]:
                        key = candidate["id"]
                        existing = candidates_by_id.get(key)
                        if existing and existing.get("state") != candidate.get("state"):
                            entry["collection_limits"].append(
                                "Item state changed between discovery streams."
                            )
                        candidate["kind"] = kind
                        candidates_by_id[key] = candidate
            for candidate in candidates_by_id.values():
                state_before = candidate.get("state")
                updated = timestamp(candidate.get("updatedAt"))
                inventory = (
                    updated is not None
                    and updated < timestamp(interval["start"])
                    and state_before == "OPEN"
                )
                try:
                    api.enrich(candidate, inventory=inventory)
                    # A formerly old item may have new activity by detail observation.
                    if inventory and (
                        timestamp(candidate.get("updatedAt"))
                        or timestamp(interval["end"])
                    ) >= timestamp(interval["start"]):
                        api.enrich(candidate)
                    if candidate.get("state") != state_before:
                        candidate.setdefault("limits", []).append(
                            "Item state changed during collection."
                        )
                        candidate["coverage"] = "partial"
                except (ReportError, KeyError, ValueError, TypeError) as exc:
                    candidate["coverage"] = "partial"
                    candidate.setdefault("limits", []).append(
                        exc.code
                        if isinstance(exc, ReportError)
                        else "invalid item evidence"
                    )
                if candidate.get("coverage") != "complete":
                    entry["collection_limits"].append(
                        f"{candidate['kind']} #{candidate['number']}: incomplete evidence"
                    )
                items[candidate["id"]] = candidate
        except ReportError as exc:
            entry["collection_coverage"] = (
                "inaccessible" if exc.code == "INACCESSIBLE" else "unavailable"
            )
            entry["collection_limits"].append(exc.code)
        if entry["collection_limits"] and entry["collection_coverage"] == "complete":
            entry["collection_coverage"] = "partial"

    issue_cache = {key: item for key, item in items.items() if item["kind"] == "issue"}
    prs = {key: item for key, item in items.items() if item["kind"] == "pr"}
    for issue in issue_cache.values():
        for event in issue.get("timeline_data", []):
            closer = event.get("closer") or {}
            if closer.get("__typename") == "PullRequest" and closer.get("id") in prs:
                pr = prs[closer["id"]]
                if issue["id"] not in {
                    i["id"] for i in pr.get("linked_issues_data", [])
                }:
                    pr.setdefault("linked_issues_data", []).append(issue)
    for pr in prs.values():
        linked = []
        for issue in pr.get("linked_issues_data", []):
            if issue["id"] not in issue_cache:
                try:
                    issue_cache[issue["id"]] = api.issue(issue["id"])
                except (ReportError, KeyError, ValueError, TypeError):
                    issue_cache[issue["id"]] = {
                        **issue,
                        "kind": "issue",
                        "coverage": "inaccessible",
                    }
            detail = issue_cache[issue["id"]]
            if detail.get("coverage") != "complete":
                pr["coverage"] = "partial"
                pr.setdefault("limits", []).append("incomplete linked issue evidence")
            linked.append(detail)
        pr["linked_issues_data"] = linked
    # Relationship reads happen after discovery/enrichment. Their failures must
    # remain visible even when --user excludes every affected parent PR.
    for entry in report["repositories"]:
        for pr in prs.values():
            if pr["repository"]["nameWithOwner"].lower() == entry[
                "repository"
            ].lower() and "incomplete linked issue evidence" in pr.get("limits", []):
                entry["collection_limits"].append(
                    f"pr #{pr['number']}: incomplete linked issue evidence"
                )
                if entry["collection_coverage"] == "complete":
                    entry["collection_coverage"] = "partial"
    report["evidence"] = build_evidence(list(items.values()), interval)
    rows = report["evidence"]["items"]
    for entry in report["repositories"]:
        repo = entry["repository"]
        unresolved = [
            row
            for row in rows
            if row["repository"].lower() == repo.lower()
            and row_scope(row, project) == "uncertain"
        ]
        context = (
            api.repository_context(repo, unresolved, project)
            if unresolved
            else {"folders": [], "evidence": {}, "coverage": "complete", "limits": []}
        )
        report["evidence"]["contexts"][repo.lower()] = context
        entry["classification_coverage"] = context["coverage"]
        entry["classification_limits"] = context["limits"]
    report["collected_at"] = iso(datetime.now(UTC))
    return finalize(report)


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        # Help exits before importing mode-specific resources or reading tools.
        from loc_modifications_report import collect_loc, owner_selector, top_selector

        interval = window(args.date)
        selected = repositories(args.repo)
        project = project_selector(args.project)
        user = user_selector(args.user)
        owner = owner_selector(args.owner)
        top = top_selector(args.top)
        loc_mode = args.loc_modifications_report is not None
        if (
            (owner and selected)
            or (top is not None and not loc_mode)
            or (loc_mode and project)
        ):
            raise ReportError(
                "INVALID_COMBINATION",
                "--owner excludes --repo; --top requires --loc-modifications-report; LOC mode does not accept --project.",
            )
        if args.expected_user_id is not None and args.expected_user_id <= 0:
            raise ReportError(
                "INVALID_IDENTITY",
                "Expected user identity must be a positive GitHub database ID.",
            )
        if not selected and not owner:
            selected = [checkout_repository()]
        api = GitHub()
        inventory = None
        expected_id = args.expected_user_id
        if owner:
            expected_id = api.viewer(expected_id)["databaseId"]
            inventory = api.owner_repositories(owner)
            selected = [item["nameWithOwner"] for item in inventory["nodes"]]
        if loc_mode:
            report = collect_loc(
                api,
                selected,
                interval,
                expected_id,
                user=user,
                top=top,
                inventory=inventory,
            )
        else:
            report = collect(
                api, selected, interval, expected_id, project=project, user=user
            )
            if inventory is not None:
                report["owner_inventory"] = inventory
                report["limitations"].append(
                    "Owner scope includes only accessible owned repositories, including forks and archives; hidden repositories are unknown."
                )
                if inventory["limits"]:
                    report["coverage"] = "partial"
                    report["limitations"].extend(
                        "Owner inventory: " + limit for limit in inventory["limits"]
                    )
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report["coverage"] == "complete" else 3
    except ReportError as exc:
        print(
            json.dumps(
                {"coverage": "unavailable", "error": exc.code, "message": str(exc)}
            )
        )
        return 2
    except (KeyError, ValueError, TypeError, AttributeError):
        print(
            json.dumps(
                {
                    "coverage": "unavailable",
                    "error": "INVALID_RESPONSE",
                    "message": "Evidence could not be interpreted safely; no complete report is available.",
                }
            )
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
