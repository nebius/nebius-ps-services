"""Pure input, attribution and presentation rules for GitHub reports."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re

UTC = timezone.utc
REPO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}\Z")


class ReportError(Exception):
    """A safe, public error message (never an upstream response body)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class Once(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, self.dest) is not None:
            parser.error(f"{option_string} may be supplied only once")
        setattr(namespace, self.dest, values)


def repositories(values: list[str] | None) -> list[str]:
    if values is None:
        return []
    result = []
    for value in " ".join(values).split(","):
        value = value.strip()
        if not REPO_RE.fullmatch(value) or value.split("/")[1] in {".", ".."}:
            raise ReportError(
                "INVALID_REPO", "Use a comma-separated list of OWNER/REPO values."
            )
        if value.lower() not in {item.lower() for item in result}:
            result.append(value)
    return result


def user_selector(value: str | None) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(
            ord(char) < 32 or 127 <= ord(char) < 160 or char in "\u2028\u2029"
            for char in value
        )
    ):
        raise ReportError(
            "INVALID_USER",
            "--user requires one nonempty literal substring without control characters.",
        )
    return value.strip()


def user_matches(user: dict, selector: str | None) -> bool:
    return selector is None or any(
        selector.casefold() in (value or "").casefold()
        for value in (user["login"], user["name"])
    )


def valid_git_path(value) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 4096
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
        and "\\" not in value
        and not any(part in {"", ".", ".."} for part in value.split("/"))
    )


def project_selector(value: str | None) -> str | None:
    if value is None:
        return None
    if value.startswith("./"):
        value = value[2:]
    if value.endswith("/"):
        value = value[:-1]
    if (
        not valid_git_path(value)
        or value.startswith("~")
        or re.match(r"^[A-Za-z]:", value)
        or any(char in value for char in "*?[]{}")
    ):
        raise ReportError(
            "INVALID_PROJECT",
            "--project requires one repository-relative folder without traversal or wildcards.",
        )
    return value


def timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def window(date: str | None, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ReportError(
            "INVALID_TIME", "The collection time must include a timezone."
        )
    local_now = now.astimezone()
    if date is None:
        start = now.astimezone(UTC) - timedelta(days=90)
    else:
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                raise ValueError
            # Interpret this historical midnight using the OS timezone's DST rules,
            # not today's fixed UTC offset.
            midnight = datetime.strptime(date, "%Y-%m-%d")
            if midnight.date() > local_now.date():
                raise ValueError
            start = midnight.astimezone().astimezone(UTC)
        except (ValueError, OverflowError):
            raise ReportError(
                "INVALID_DATE",
                "--date must be a real YYYY-MM-DD date, today or earlier.",
            ) from None
    return {
        "start": iso(start),
        "end": iso(now),
        "local_start": start.astimezone().isoformat(),
        "local_end": local_now.isoformat(),
        "timezone": local_now.tzname(),
        "default_days": 90 if date is None else None,
    }


def inside(value: str | None, interval: dict) -> bool:
    parsed = timestamp(value)
    return parsed is not None and timestamp(interval["start"]) <= parsed <= timestamp(
        interval["end"]
    )


def is_user(actor: dict | None, user: dict) -> bool:
    return bool(actor and actor.get("id") and actor["id"] == user["id"])


def evidence_text(value: str | None, limit: int = 3000) -> dict:
    """Bound untrusted narrative evidence and redact common credential forms."""
    value = value or ""
    value = re.sub(
        r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
        "[redacted private key]",
        value,
        flags=re.S,
    )
    value = re.sub(
        r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)\b",
        "[redacted token]",
        value,
    )
    value = re.sub(
        r"(?i)(authorization\s*:\s*(?:bearer|token)\s+)\S+", r"\1[redacted]", value
    )
    value = re.sub(r"(?i)(https?://)[^\s/]*@", r"\1[redacted]@", value)
    return {"text": value[:limit], "truncated": len(value) > limit}


def activity(item: dict, user: dict, interval: dict) -> list[dict]:
    result = []

    def add(kind, date, url, text=""):
        if inside(date, interval):
            result.append(
                {
                    "kind": kind,
                    "at": date,
                    "url": url,
                    "evidence": evidence_text(text, 1200),
                }
            )

    if is_user(item.get("author"), user):
        add("authored", item.get("createdAt"), item["url"])
    for comment in item.get("comments_data", []):
        if is_user(comment.get("author"), user):
            add(
                "discussed",
                comment.get("createdAt"),
                comment.get("url"),
                comment.get("body"),
            )
    for review in item.get("reviews_data", []):
        if review.get("state") != "PENDING" and is_user(review.get("author"), user):
            add(
                "reviewed",
                review.get("submittedAt"),
                review.get("url"),
                review.get("body"),
            )
    for comment in item.get("review_comments_data", []):
        if comment.get("publishedAt") and is_user(comment.get("author"), user):
            add(
                "review comment",
                comment.get("publishedAt"),
                comment.get("url"),
                comment.get("body"),
            )
    for commit in item.get("commits_data", []):
        if any(
            is_user(author.get("user"), user)
            for author in commit.get("authors_data", [])
        ):
            add(
                "contributed commit",
                commit.get("committedDate"),
                commit.get("url"),
                commit.get("messageHeadline"),
            )
    for event in item.get("timeline_data", []):
        if is_user(event.get("actor"), user) and event.get("__typename") in {
            "ClosedEvent",
            "ReopenedEvent",
            "LabeledEvent",
            "UnlabeledEvent",
        }:
            add(
                "triaged: " + event["__typename"].removesuffix("Event").lower(),
                event.get("createdAt"),
                event.get("url") or item["url"],
            )
    # The same review comment may be seen through more than one connection.
    unique = {(e["kind"], e["url"], e["at"]): e for e in result}
    return sorted(unique.values(), key=lambda e: (e["at"], e["kind"]))


def past_contributor(item: dict, user: dict, interval: dict) -> bool:
    cutoff = timestamp(interval["end"])
    if (
        is_user(item.get("author"), user)
        and (timestamp(item.get("createdAt")) or cutoff) <= cutoff
    ):
        return True
    return any(
        timestamp(commit.get("committedDate")) is not None
        and timestamp(commit["committedDate"]) <= cutoff
        and any(
            is_user(author.get("user"), user)
            for author in commit.get("authors_data", [])
        )
        for commit in item.get("commits_data", [])
    )


def number_or_unknown(value):
    return value if type(value) is int and value >= 0 else None


def issue_link(issue: dict, pr_id: str | None = None) -> dict:
    relationship = "linked"
    closures = [
        e
        for e in issue.get("timeline_data", [])
        if e.get("__typename") == "ClosedEvent"
    ]
    latest = max(closures, key=lambda e: e.get("createdAt", ""), default={})
    if (
        pr_id
        and issue.get("state") == "CLOSED"
        and issue.get("stateReason") == "COMPLETED"
        and issue.get("coverage") == "complete"
        and timestamp(latest.get("createdAt")) is not None
        and timestamp(latest.get("createdAt")) == timestamp(issue.get("closedAt"))
        and (latest.get("closer") or {}).get("id") == pr_id
    ):
        relationship = "closed_by_pr"
    return {
        "id": issue["id"],
        "number": issue["number"],
        "url": issue["url"],
        "title": evidence_text(issue.get("title"), 400)["text"],
        "state": issue.get("state"),
        "state_reason": issue.get("stateReason"),
        "closed_at": issue.get("closedAt"),
        "relationship": relationship,
        "coverage": issue.get("coverage", "partial"),
        "contributions": issue.get("contributions", []),
    }


def pr_reference(pr: dict) -> dict:
    ref = {
        key: pr.get(key)
        for key in ("id", "number", "url", "title", "state", "mergedAt")
    }
    ref["title"] = evidence_text(pr.get("title"), 400)["text"]
    return ref


def merge_user(users: dict, user: dict, limits: list[str]) -> None:
    """Merge observed fields without letting missing reads erase known names."""
    previous = users.get(user["id"])
    if previous is None:
        users[user["id"]] = user
        return
    if previous["login"] != user["login"] or (
        previous["name_known"]
        and user["name_known"]
        and previous["name"] != user["name"]
    ):
        limits.append("User profile changed during collection.")
    users[user["id"]] = {
        **previous,
        "login": user["login"],
        "name": user["name"] if user["name_known"] else previous["name"],
        "name_known": previous["name_known"] or user["name_known"],
    }


def item_users(item: dict) -> tuple[dict, list[str]]:
    """Only mapped GitHub User identities may own work; Git names are never IDs."""
    actors = [item.get("author")]
    for field in ("comments_data", "reviews_data", "review_comments_data"):
        actors.extend(event.get("author") for event in item.get(field, []))
    actors.extend(
        author.get("user")
        for commit in item.get("commits_data", [])
        for author in commit.get("authors_data", [])
    )
    actors.extend(
        event.get("actor")
        for event in item.get("timeline_data", [])
        if event.get("__typename")
        in {"ClosedEvent", "ReopenedEvent", "LabeledEvent", "UnlabeledEvent"}
    )
    users, limits = {}, []
    for actor in actors:
        if not isinstance(actor, dict) or not isinstance(actor.get("__typename"), str):
            limits.append("Some activity has no mapped GitHub user identity.")
            continue
        if actor["__typename"] != "User":
            continue
        if not all(
            isinstance(actor.get(key), str) and actor[key] for key in ("id", "login")
        ):
            limits.append("Some activity has incomplete GitHub user identity.")
            continue
        if "name" not in actor or (
            actor["name"] is not None and not isinstance(actor["name"], str)
        ):
            limits.append(
                "Some user profile names are unavailable; user matching may be incomplete."
            )
        user = {
            "id": actor["id"],
            "login": actor["login"],
            "name": actor.get("name") if isinstance(actor.get("name"), str) else None,
            "name_known": "name" in actor
            and (actor["name"] is None or isinstance(actor["name"], str)),
        }
        merge_user(users, user, limits)
    return users, limits


def user_attribution(item: dict, user: dict, interval: dict, refs: dict) -> dict:
    events = activity(item, user, interval)
    delivered = (
        item["kind"] == "pr"
        and item.get("state") == "MERGED"
        and inside(item.get("mergedAt"), interval)
        and past_contributor(item, user, interval)
    )
    if item["kind"] == "issue":
        for pr in refs.values():
            if (
                pr.get("state") == "MERGED"
                and inside(pr.get("mergedAt"), interval)
                and inside(item.get("closedAt"), interval)
                and past_contributor(pr, user, interval)
                and issue_link(item, pr["id"])["relationship"] == "closed_by_pr"
            ):
                events.append(
                    {
                        "kind": "resolved through contributed PR",
                        "at": item["closedAt"],
                        "url": pr["url"],
                        "evidence": evidence_text("Verified completed closure"),
                    }
                )
    events.sort(key=lambda event: (event["at"], event["kind"]))
    delivery = (
        "earlier contribution merged"
        if delivered
        and not any(
            event["kind"] in {"authored", "contributed commit"} for event in events
        )
        else "merged in period"
        if delivered
        else None
    )
    return {
        "user_id": user["id"],
        "contributions": events,
        "roles": sorted(
            {event["kind"] for event in events} | ({delivery} if delivery else set())
        ),
        "delivery": delivery,
        "last_activity": events[-1]["at"] if events else None,
    }


def attributed_row(source: dict, users: dict, attribution: dict | None = None) -> dict:
    """Project shared facts with only the selected subject's contribution evidence."""
    row = deepcopy(source)
    selected = [attribution] if attribution is not None else row["attributions"]
    row["attributions"] = deepcopy(selected)
    row["contributions"] = sorted(
        [
            {
                **event,
                "user_id": entry["user_id"],
                "user_login": users[entry["user_id"]]["login"],
            }
            for entry in selected
            for event in entry["contributions"]
        ],
        key=lambda event: (event["at"], event["kind"], event["user_id"]),
    )
    row["roles"] = sorted({role for entry in selected for role in entry["roles"]})
    row["last_activity"] = (
        row["contributions"][-1]["at"] if row["contributions"] else None
    )
    row["delivery"] = attribution["delivery"] if attribution is not None else None
    row["subject"] = (
        deepcopy(users[attribution["user_id"]]) if attribution is not None else None
    )
    return row


def build_evidence(items: list[dict], interval: dict) -> dict:
    """Collect each item once and keep user attribution independent of item facts."""
    unique = {i["id"]: i for i in items}
    prs = {key: i for key, i in unique.items() if i["kind"] == "pr"}
    issues = {key: i for key, i in unique.items() if i["kind"] == "issue"}
    # Narrow linked issue reads can establish delivery, but never expand repo scope.
    repositories_in_scope = {i["repository"]["nameWithOwner"].lower() for i in items}
    for pr in prs.values():
        for issue in pr.get("linked_issues_data", []):
            if issue["repository"]["nameWithOwner"].lower() in repositories_in_scope:
                issues.setdefault(issue["id"], {**issue, "kind": "issue"})
    linked_prs = {}
    for pr in prs.values():
        for issue in pr.get("linked_issues_data", []):
            if (
                issue["repository"]["nameWithOwner"].lower()
                == pr["repository"]["nameWithOwner"].lower()
            ):
                linked_prs.setdefault(issue["id"], {})[pr["id"]] = pr
    for issue in issues.values():
        for event in issue.get("timeline_data", []):
            closer = event.get("closer") or {}
            pr = prs.get(closer.get("id"))
            if (
                pr
                and issue["repository"]["nameWithOwner"].lower()
                == pr["repository"]["nameWithOwner"].lower()
            ):
                linked_prs.setdefault(issue["id"], {})[pr["id"]] = pr
    users = {}
    identities = {}
    identity_limits = {}
    for item in [*prs.values(), *issues.values()]:
        found, limits = item_users(item)
        identities[item["id"]] = set(found)
        repo = item["repository"]["nameWithOwner"].lower()
        for user in found.values():
            merge_user(users, user, limits)
        identity_limits.setdefault(repo, []).extend(limits)
    rows = []
    for item in [*prs.values(), *issues.values()]:
        refs = linked_prs.get(item["id"], {})
        eligible = set(identities[item["id"]])
        if item["kind"] == "issue":
            for pr in refs.values():
                eligible.update(identities[pr["id"]])
        attributions = []
        for user_id in sorted(eligible):
            contribution = user_attribution(item, users[user_id], interval, refs)
            if contribution["roles"]:
                attributions.append(contribution)
        is_open = item.get("state") == "OPEN"
        if item.get("state") not in {"OPEN", "CLOSED", "MERGED"} or not (
            is_open or attributions
        ):
            continue
        row = base_row(item)
        row["attributions"] = attributions
        row["section"] = ("open_" if is_open else "worked_") + (
            "prs" if item["kind"] == "pr" else "issues"
        )
        if item["kind"] == "pr":
            row["projects"] = project_folders(item)
            paths, coverage = file_evidence(item)
            row["folder_evidence"] = {
                "paths": paths,
                "coverage": coverage,
                "basis": "changed files",
            }
            if coverage != "complete":
                row["coverage"] = "partial"
                row["limits"] = [*row["limits"], "incomplete changed-file evidence"]
            row["metrics"] = {
                "scope": "whole PR at observation time",
                "additions": number_or_unknown(item.get("additions")),
                "deletions": number_or_unknown(item.get("deletions")),
                "commits": number_or_unknown(
                    (item.get("commits") or {}).get("totalCount")
                ),
            }
            row["issues"] = [
                issue_link(issues.get(i["id"], i), item["id"])
                for i in item.get("linked_issues_data", [])
            ]
        else:
            row["issues"] = [issue_link(item)]
            row["related_prs"] = [pr_reference(pr) for pr in refs.values()]
            paths = sorted(
                {path for pr in refs.values() for path in file_evidence(pr)[0]}
            )
            # Links can prove positive scope, not an exhaustive issue scope.
            row["folder_evidence"] = {
                "paths": paths,
                "coverage": "unknown",
                "basis": "linked PR",
            }
            row["projects"] = project_folders(
                {
                    "files_data": [{"path": path} for path in paths],
                    "files_coverage": "unknown",
                }
            )
        rows.append(row)
    return {
        "items": sorted(
            rows,
            key=lambda row: (row["repository"].lower(), row["kind"], -row["number"]),
        ),
        "users": users,
        "identity_limits": {
            repo: sorted(set(limits)) for repo, limits in identity_limits.items()
        },
        "contexts": {},
    }


def row_scope(row: dict, project: str | None) -> str:
    facts = row["folder_evidence"]
    if project:
        if any(path.startswith(project + "/") for path in facts["paths"]):
            return "matched"
        return (
            "excluded"
            if row["kind"] == "pr" and facts["coverage"] == "complete"
            else "uncertain"
        )
    return (
        "matched"
        if facts["paths"] or (row["kind"] == "pr" and facts["coverage"] == "complete")
        else "uncertain"
    )


def base_row(item: dict) -> dict:
    return {
        "id": item["id"],
        "kind": item["kind"],
        "repository": item["repository"]["nameWithOwner"],
        "number": item["number"],
        "url": item["url"],
        "title": evidence_text(item.get("title"), 400)["text"],
        "description": evidence_text(item.get("body")),
        "state": item.get("state"),
        "state_reason": item.get("stateReason"),
        "draft": item.get("isDraft", False),
        "created_at": item.get("createdAt"),
        "updated_at": item.get("updatedAt"),
        "merged_at": item.get("mergedAt"),
        "closed_at": item.get("closedAt"),
        "coverage": item.get("coverage", "partial"),
        "limits": item.get("limits", []),
        "observed_at": item.get("observed_at"),
    }


def file_evidence(item: dict) -> tuple[list[str], str]:
    """Validate before allowing missing matches to prove exclusion."""
    paths = []
    coverage = item.get("files_coverage", "unknown")
    files = item.get("files_data")
    if not isinstance(files, list):
        return [], "unknown"
    for file in files:
        path = file.get("path") if isinstance(file, dict) else None
        if not valid_git_path(path):
            coverage = "partial"
            continue
        paths.append(path)
    return paths, coverage


def project_folders(item: dict) -> dict:
    """Path groups describe the whole observed PR, never individual ownership."""
    containers = {"services", "apps", "packages", "projects", "components", "modules"}
    names = set()
    paths, coverage = file_evidence(item)
    for path in paths:
        parts = path.split("/")
        group = (
            "repository root"
            if len(parts) == 1
            else "/".join(parts[:2])
            if len(parts) > 2 and parts[0] in containers
            else parts[0]
        )
        names.add(evidence_text(group, 4096)["text"])
    return {
        "folders": sorted(names, key=lambda name: (name.casefold(), name)),
        "coverage": coverage,
        "scope": "whole PR at observation time",
    }


def project_cell(row: dict) -> str:
    projects = row["projects"]
    names = "; ".join(cell(name) for name in projects["folders"])
    assessment = row.get("folder_assessment")
    if assessment:
        inferred = "; ".join(cell(name) for name in assessment["folders"])
        names = "; ".join(
            part for part in (names, inferred + " (inferred from description)") if part
        )
    elif names and row["kind"] == "issue":
        names += " (linked PR)"
    if row.get("project_scope") == "uncertain":
        names = "; ".join(part for part in (names, "Project uncertain") if part)
    if row["kind"] == "pr" and projects["coverage"] != "complete":
        names += "; file evidence partial"
    return names or "No changed files"


def work_cell(row: dict) -> str:
    """Keep the scaffold honest without a separate role column."""
    roles = set(row["roles"])
    if "authored" in roles:
        prefix = "Authored: "
    elif "contributed commit" in roles:
        prefix = "Contributed commits: "
    elif "resolved through contributed PR" in roles:
        prefix = "Resolved through contributed PR: "
    elif "earlier contribution merged" in roles:
        prefix = "Earlier contribution merged: "
    elif roles & {"reviewed", "review comment"}:
        prefix = "Reviewed: "
    elif "discussed" in roles:
        prefix = "Discussed: "
    elif roles:
        prefix = "Triaged: "
    else:
        prefix = ""
    return cell(prefix + row["title"])


def cell(value) -> str:
    value = str(value if value is not None else "unknown")
    return (
        re.sub(r"[\r\n\t\u2028\u2029]+", " ", value)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def link(number, url) -> str:
    # Only canonical GitHub issue/PR URLs can become active Markdown links.
    if isinstance(url, str) and re.fullmatch(
        r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/(?:issues|pull)/[0-9]+",
        url,
    ):
        return f"[#{number}]({url})"
    return cell(f"#{number} (link unavailable)")


def table(rows: list[dict]) -> str:
    lines = [
        "| Work and outcome | Projects / folders | Issue(s) | PR | Status and date | PR size |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        issues = "; ".join(
            link(i["number"], i["url"]) + " — " + cell(i["title"])
            for i in row["issues"]
        ) or (
            "No linked issue found"
            if row["coverage"] == "complete"
            else "Issue linkage unknown"
        )
        pr = (
            link(row["number"], row["url"])
            if row["kind"] == "pr"
            else "; ".join(
                link(p["number"], p["url"]) for p in row.get("related_prs", [])
            )
            or "—"
        )
        if row["state"] == "OPEN":
            status = "Draft" if row.get("draft") else "Open"
            if row["contributions"]:
                latest = {}
                for event in row["contributions"]:
                    latest[event["user_id"]] = event
                status += "; In progress — " + "; ".join(
                    "@"
                    + recent["user_login"]
                    + " "
                    + {
                        "authored": "item opened",
                        "reviewed": "review",
                        "discussed": "comment",
                        "contributed commit": "commit",
                    }.get(recent["kind"], recent["kind"])
                    + " on "
                    + recent["at"]
                    for recent in sorted(
                        latest.values(),
                        key=lambda event: (
                            event["user_login"].casefold(),
                            event["user_id"],
                        ),
                    )
                )
            else:
                status += "; updated " + str(row.get("updated_at") or "unknown")
        else:
            status = row["state"] + (
                " (" + row["state_reason"] + ")" if row.get("state_reason") else ""
            )
            status += "; " + (
                "merged " + str(row["merged_at"])
                if row.get("merged_at")
                else "closed " + str(row.get("closed_at") or "unknown")
            )
            if row.get("last_activity"):
                status += "; activity " + row["last_activity"]
        metrics = row.get("metrics")
        size = (
            f"+{cell(metrics['additions'])} / −{cell(metrics['deletions'])}; {cell(metrics['commits'])} commits"
            if metrics
            else "—"
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    work_cell(row),
                    project_cell(row),
                    issues,
                    pr,
                    cell(status),
                    size,
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def report_header(
    repository: str, interval: dict, project: str | None, user: str | None
) -> str:
    def local_endpoint(key):
        value = timestamp(interval.get("local_" + key) or interval[key])
        # timestamp normalizes to UTC; retain the frozen local offset when available.
        if interval.get("local_" + key):
            value = datetime.fromisoformat(
                interval["local_" + key].replace("Z", "+00:00")
            )
        formatted = value.isoformat(sep=" ", timespec="seconds")
        return formatted[:-6] + " " + formatted[-6:]

    label = "All users" if user is None else 'Matching "' + cell(user) + '"'
    first = "User: " + label + " · Repository: " + cell(repository)
    if project:
        first += " · Folder: " + cell(project)
    return (
        first
        + "\nDate: "
        + local_endpoint("start")
        + " through "
        + local_endpoint("end")
    )
