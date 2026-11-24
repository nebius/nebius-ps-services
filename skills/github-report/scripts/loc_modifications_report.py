"""Read-only whole-PR change-volume aggregation, separate from activity credit."""

from __future__ import annotations

import re

from report_model import (
    ReportError,
    cell,
    inside,
    repositories,
    timestamp,
    user_matches,
)

LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")


def owner_selector(value):
    if value is None:
        return None
    if not isinstance(value, str) or not LOGIN.fullmatch(value):
        raise ReportError(
            "INVALID_OWNER", "--owner requires one GitHub organization or user login."
        )
    return value


def top_selector(value):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]*", value):
        raise ReportError("INVALID_TOP", "--top requires a positive integer.")
    try:
        return int(value)
    except ValueError:
        raise ReportError(
            "INVALID_TOP", "--top integer is too large to parse safely."
        ) from None


def render(report):
    scope = report["scope"]
    interval = report["window"]
    title = "LOC Modifications report"
    if report["coverage"] != "complete":
        title += " — provisional ranking among observed accounts"
    lines = [
        title,
        "",
        scope,
        f"Date: {interval['local_start']} through {interval['local_end']}",
        "",
        "| Rank | Contributor | LOC Modifications | Merged PRs |",
        "| ---: | --- | ---: | ---: |",
    ]
    for row in report["rows"]:
        label = (
            f"{row['name']} (@{row['login']})" if row["name"] else "@" + row["login"]
        )
        amount = f"{row['loc_modifications']:,}"
        if not row["metrics_complete"]:
            amount = "≥ " + amount if row["known_metric_prs"] else "unknown"
        lines.append(
            f"| {row['rank']} | [{cell(label)}](https://github.com/{row['login']}) | {amount} | {row['merged_prs']:,} |"
        )
    if not report["rows"]:
        lines += [
            "",
            "No matching contributors observed"
            + ("; coverage incomplete." if report["coverage"] != "complete" else "."),
        ]
    lines += [
        "",
        f"Coverage: {report['repository_count']} visible repositories; {report['merged_prs_observed']} merged PRs observed; {report['excluded_bot_prs']} API-bot PRs excluded.",
    ]
    lines += [
        "",
        "LOC Modifications = whole-PR additions + deletions, attributed to PR authors. A replaced line counts twice. Includes documentation, generated files, data, imports and repeated changes; not unique lines, individual line ownership or productivity.",
        "API User accounts are not independently verified humans. Hidden repositories and unavailable/deleted history are outside observable coverage.",
    ]
    if report["top"] is not None:
        lines.append(
            f"Showing {len(report['rows'])} of {report['matching_accounts']} matching observed accounts (top {report['top']})."
        )
    if report["cutoff_tie"]:
        lines.append(
            "The cutoff splits an equal-LOC tie; equal ranks are retained and login/stable ID determines displayed rows."
        )
    if report["limits"]:
        lines.append("Limits: " + "; ".join(cell(x) for x in report["limits"]) + ".")
    return "\n".join(lines)


def collect_loc(
    api, selected, interval, expected_id=None, *, user=None, top=None, inventory=None
):
    viewer = api.viewer(expected_id)
    limits = list(inventory["limits"]) if inventory is not None else []
    users, seen_prs, seen_repos, entries = {}, set(), {}, []
    repository_ids = {}
    if inventory is not None:
        for node in inventory["nodes"]:
            repository_ids[node["nameWithOwner"].casefold()] = node["id"]
    observed, bots, unknown_authors = 0, 0, 0
    for repo in selected:
        entry = {"repository": repo, "limits": [], "merged_prs": 0}
        entries.append(entry)
        try:
            found = api.merged_prs(repo, interval["start"], interval["end"])
            entry["limits"].extend(found["limits"])
            for pr in found["nodes"]:
                key = pr.get("id")
                identity = pr.get("repository") or {}
                try:
                    canonical = repositories([identity["nameWithOwner"]])[0]
                    if (
                        canonical.casefold() != repo.casefold()
                        or not isinstance(identity.get("id"), str)
                        or not identity["id"]
                        or not isinstance(key, str)
                        or not key
                    ):
                        raise ValueError
                    previous = seen_repos.setdefault(
                        identity["id"], canonical.casefold()
                    )
                    expected = repository_ids.setdefault(
                        canonical.casefold(), identity["id"]
                    )
                    if previous != canonical.casefold() or expected != identity["id"]:
                        raise ValueError
                except (ReportError, KeyError, ValueError, TypeError, AttributeError):
                    entry["limits"].append("invalid repository or PR identity")
                    continue
                if key in seen_prs:
                    entry["limits"].append(
                        "duplicate PR identity across selected scope"
                    )
                    continue
                seen_prs.add(key)
                if timestamp(pr.get("mergedAt")) is None:
                    entry["limits"].append("unknown merge timestamp")
                    continue
                if not inside(pr["mergedAt"], interval):
                    continue
                observed += 1
                entry["merged_prs"] += 1
                actor = pr.get("author")
                if isinstance(actor, dict) and actor.get("__typename") == "Bot":
                    bots += 1
                    continue
                if (
                    not isinstance(actor, dict)
                    or actor.get("__typename") != "User"
                    or not isinstance(actor.get("id"), str)
                    or not actor["id"]
                    or not isinstance(actor.get("login"), str)
                    or not LOGIN.fullmatch(actor["login"])
                ):
                    unknown_authors += 1
                    entry["limits"].append("missing or unavailable author identity")
                    continue
                name = actor.get("name")
                if "name" not in actor or (
                    name is not None and not isinstance(name, str)
                ):
                    entry["limits"].append(
                        "author profile name unavailable; substring matching may be incomplete"
                    )
                    name = None
                row = users.setdefault(
                    actor["id"],
                    {
                        "id": actor["id"],
                        "login": actor["login"],
                        "name": name,
                        "loc_modifications": 0,
                        "merged_prs": 0,
                        "known_metric_prs": 0,
                        "metrics_complete": True,
                        "repositories": set(),
                    },
                )
                if row["login"] != actor["login"] or (
                    row["name"] is not None and name is not None and row["name"] != name
                ):
                    entry["limits"].append("author profile changed during collection")
                if name is not None:
                    row["name"] = name
                row["merged_prs"] += 1
                row["repositories"].add(canonical)
                values = (pr.get("additions"), pr.get("deletions"))
                if all(type(v) is int and v >= 0 for v in values):
                    row["loc_modifications"] += sum(values)
                    row["known_metric_prs"] += 1
                else:
                    row["metrics_complete"] = False
                    entry["limits"].append(
                        "unknown PR metrics; observed LOC is a lower bound"
                    )
        except ReportError as exc:
            entry["limits"].append(exc.code)
        except (KeyError, ValueError, TypeError, AttributeError):
            entry["limits"].append("invalid merged-PR evidence")
        entry["limits"] = sorted(set(entry["limits"]))
        entry["coverage"] = "partial" if entry["limits"] else "complete"
        limits.extend(repo + ": " + value for value in entry["limits"])
    candidates = [row for row in users.values() if user_matches(row, user)]
    candidates.sort(
        key=lambda row: (-row["loc_modifications"], row["login"].casefold(), row["id"])
    )
    last_amount, rank = None, 0
    for index, row in enumerate(candidates, 1):
        if row["loc_modifications"] != last_amount:
            rank = index
        row["rank"] = rank
        last_amount = row["loc_modifications"]
        row["repositories"] = sorted(row["repositories"])
    cutoff_tie = bool(
        top
        and len(candidates) > top
        and candidates[top - 1]["loc_modifications"]
        == candidates[top]["loc_modifications"]
    )
    scope = (
        "Owner: " + inventory["owner"]["login"] + " (visible owned repositories)"
        if inventory is not None
        else "Repositories: " + ", ".join(selected)
    )
    report = {
        "schema_version": 1,
        "mode": "loc-modifications-report",
        "viewer": viewer,
        "scope": scope,
        "window": interval,
        "user_filter": user,
        "top": top,
        "coverage": "partial" if limits else "complete",
        "limits": sorted(set(limits)),
        "repository_count": len(selected),
        "repositories": entries,
        "merged_prs_observed": observed,
        "excluded_bot_prs": bots,
        "unknown_author_prs": unknown_authors,
        "matching_accounts": len(candidates),
        "rows": candidates[:top],
        "cutoff_tie": cutoff_tie,
    }
    report["markdown"] = render(report)
    return report
