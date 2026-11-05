"""Validate bounded host assessments, then assemble the canonical report."""

from copy import deepcopy

from report_model import (
    ReportError,
    evidence_text,
    project_selector,
    row_scope,
    table,
    attributed_row,
    user_matches,
    user_selector,
    inside,
    report_header,
)

SECTIONS = {
    "worked_prs": "Worked PRs",
    "worked_issues": "Worked Issues",
    "open_prs": "Open PRs",
    "open_issues": "Open Issues",
}


def assess(report: dict, assessments: list[dict]) -> dict:
    """Assessments may describe folders, never authoritative GitHub facts."""
    if not isinstance(assessments, list):
        raise ReportError("INVALID_ASSESSMENT", "Folder assessments must be a list.")
    rows = {row["id"]: row for row in report["evidence"]["items"]}
    contexts = report["evidence"]["contexts"]
    accepted = {}
    for value in assessments:
        if not isinstance(value, dict) or set(value) != {
            "item_id",
            "folders",
            "evidence_ids",
            "rationale",
        }:
            raise ReportError(
                "INVALID_ASSESSMENT", "Only folder assessment fields are accepted."
            )
        key = value["item_id"]
        if not isinstance(key, str) or key not in rows or key in accepted:
            raise ReportError(
                "INVALID_ASSESSMENT", "Unknown or repeated item assessment."
            )
        row = rows[key]
        context = contexts.get(row["repository"].lower(), {})
        folders, refs = value["folders"], value["evidence_ids"]
        rationale = value["rationale"]
        if (
            not isinstance(folders, list)
            or not folders
            or len(folders) > 20
            or any(
                not isinstance(folder, str) or project_selector(folder) != folder
                for folder in folders
            )
            or len(set(folders)) != len(folders)
            or not isinstance(refs, list)
            or any(not isinstance(ref, str) for ref in refs)
            or not isinstance(rationale, str)
            or not rationale.strip()
            or len(rationale) > 500
        ):
            raise ReportError("INVALID_ASSESSMENT", "Malformed folder assessment.")
        text_refs = {key + ":title", key + ":description"}
        repo_refs = context.get("evidence", {})
        if not set(refs) & text_refs or not set(refs) <= (text_refs | set(repo_refs)):
            raise ReportError(
                "INVALID_ASSESSMENT",
                "Assessment requires collected item and repository evidence.",
            )
        for folder in folders:
            if folder not in context.get("folders", []) or not any(
                ref in repo_refs and repo_refs[ref].get("folder") == folder
                for ref in refs
            ):
                raise ReportError(
                    "INVALID_ASSESSMENT",
                    "Folder lacks corroborating repository evidence.",
                )
        facts = row["folder_evidence"]
        if row["kind"] == "pr" and facts["coverage"] == "complete":
            if any(
                not any(path.startswith(folder + "/") for path in facts["paths"])
                for folder in folders
            ):
                raise ReportError(
                    "CONFLICTING_ASSESSMENT",
                    "Description cannot override complete changed-file evidence.",
                )
            continue  # Existing observed evidence already owns this scope.
        accepted[key] = {
            **deepcopy(value),
            "rationale": evidence_text(rationale, 500)["text"],
        }
    return accepted


def validate_envelope(report: dict) -> None:
    """Reject contradictions in the caller-preserved collection envelope."""
    if (
        not isinstance(report, dict)
        or type(report.get("schema_version")) is not int
        or report["schema_version"] != 3
    ):
        raise ReportError(
            "INVALID_REPORT", "Only report schema version 3 is supported."
        )
    entries = report.get("repositories")
    evidence = report.get("evidence")
    if (
        not isinstance(entries, list)
        or not isinstance(evidence, dict)
        or not isinstance(evidence.get("items"), list)
        or not isinstance(evidence.get("contexts"), dict)
    ):
        raise ReportError("INVALID_REPORT", "Malformed report envelope.")
    users = evidence.get("users")
    if not isinstance(users, dict) or not isinstance(
        evidence.get("identity_limits"), dict
    ):
        raise ReportError("INVALID_REPORT", "Missing user identity evidence.")
    for key, user in users.items():
        if (
            not isinstance(user, dict)
            or set(user) != {"id", "login", "name", "name_known"}
            or not isinstance(key, str)
            or not key
            or user["id"] != key
            or not isinstance(user["login"], str)
            or not user["login"]
            or (user["name"] is not None and not isinstance(user["name"], str))
            or type(user["name_known"]) is not bool
            or (not user["name_known"] and user["name"] is not None)
        ):
            raise ReportError("INVALID_REPORT", "Invalid user identity.")
    if user_selector(report.get("user_filter")) != report.get("user_filter"):
        raise ReportError("INVALID_REPORT", "User selector must be canonical.")
    names = set()
    repository_ids = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("repository"), str):
            raise ReportError("INVALID_REPORT", "Invalid repository entry.")
        name = entry["repository"].lower()
        identity = entry.get("id")
        if name in names or (
            identity is not None
            and (not isinstance(identity, str) or identity in repository_ids)
        ):
            raise ReportError("INVALID_REPORT", "Duplicate repository identity.")
        names.add(name)
        if identity is not None:
            repository_ids.add(identity)
    item_ids = set()
    for row in evidence["items"]:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("id"), str)
            or not row["id"]
            or row["id"] in item_ids
        ):
            raise ReportError(
                "INVALID_REPORT", "Duplicate or invalid primary item identity."
            )
        item_ids.add(row["id"])
        kind, state = row.get("kind"), row.get("state")
        if kind not in {"issue", "pr"} or state not in (
            {"OPEN", "CLOSED"} if kind == "issue" else {"OPEN", "CLOSED", "MERGED"}
        ):
            raise ReportError("INVALID_REPORT", "Invalid item kind or state.")
        expected = ("open_" if state == "OPEN" else "worked_") + (
            "prs" if kind == "pr" else "issues"
        )
        if (
            row.get("section") != expected
            or not isinstance(row.get("repository"), str)
            or row["repository"].lower() not in names
        ):
            raise ReportError(
                "INVALID_REPORT",
                "Item section or repository contradicts collected facts.",
            )
        attributions = row.get("attributions")
        if not isinstance(attributions, list):
            raise ReportError("INVALID_REPORT", "Missing item attribution evidence.")
        seen_users = set()
        for attribution in attributions:
            if not isinstance(attribution, dict) or set(attribution) != {
                "user_id",
                "contributions",
                "roles",
                "delivery",
                "last_activity",
            }:
                raise ReportError("INVALID_REPORT", "Invalid attribution fields.")
            user_id = attribution["user_id"]
            if (
                not isinstance(user_id, str)
                or user_id not in users
                or user_id in seen_users
            ):
                raise ReportError(
                    "INVALID_REPORT", "Unknown or duplicate attributed user."
                )
            seen_users.add(user_id)
            events, delivery = attribution["contributions"], attribution["delivery"]
            if not isinstance(events, list) or any(
                not isinstance(event, dict)
                or not isinstance(event.get("kind"), str)
                or not inside(event.get("at"), report["window"])
                for event in events
            ):
                raise ReportError(
                    "INVALID_REPORT", "Invalid dated contribution evidence."
                )
            if delivery is not None and (
                delivery not in {"earlier contribution merged", "merged in period"}
                or kind != "pr"
                or state != "MERGED"
                or not inside(row.get("merged_at"), report["window"])
            ):
                raise ReportError("INVALID_REPORT", "Delivery contradicts item facts.")
            expected_roles = sorted(
                {event["kind"] for event in events}
                | ({delivery} if delivery else set())
            )
            if (
                not expected_roles
                or attribution["roles"] != expected_roles
                or attribution["last_activity"]
                != (events[-1]["at"] if events else None)
            ):
                raise ReportError(
                    "INVALID_REPORT", "Attribution roles or dates contradict evidence."
                )
        if state != "OPEN" and not attributions:
            raise ReportError(
                "INVALID_REPORT", "Completed item has no qualifying contributor."
            )
    if (
        report.get("project") is not None
        and project_selector(report["project"]) != report["project"]
    ):
        raise ReportError("INVALID_REPORT", "Project selector must be canonical.")


def section(title: str, rows: list[dict], coverage: str) -> dict:
    return {
        "title": title,
        "rows": rows,
        "count": len(rows),
        "uncertain_count": sum(row["project_scope"] == "uncertain" for row in rows),
        "coverage": coverage
        if coverage != "complete"
        else "partial"
        if any(row["coverage"] != "complete" for row in rows)
        else "complete",
        "table_markdown": table(rows),
    }


def finalize(report: dict, assessments: list[dict] | None = None) -> dict:
    validate_envelope(report)
    result = deepcopy(report)
    accepted = assess(result, [] if assessments is None else assessments)
    project = result.get("project")
    users = result["evidence"]["users"]
    for entry in result["repositories"]:
        backlog = {name: [] for name in ("open_prs", "open_issues")}
        groups, unique = {}, {}
        limitations = list(entry.get("collection_limits", []))
        limitations.extend(
            result["evidence"]["identity_limits"].get(entry["repository"].lower(), [])
        )
        base_coverage = entry.get("collection_coverage", "complete")
        if limitations and base_coverage == "complete":
            base_coverage = "partial"
        for source in result["evidence"]["items"]:
            if source["repository"].lower() != entry["repository"].lower():
                continue
            row = deepcopy(source)
            row["project_filter"] = project
            row["project_scope"] = row_scope(row, project)
            assessment = accepted.get(row["id"])
            if assessment:
                row["folder_assessment"] = assessment
                if row["project_scope"] == "uncertain":
                    folders = assessment["folders"]
                    row["project_scope"] = (
                        "matched"
                        if not project
                        or any(
                            f == project or f.startswith(project + "/") for f in folders
                        )
                        else "uncertain"
                        if any(project.startswith(f + "/") for f in folders)
                        else "excluded"
                    )
            if row["project_scope"] == "excluded":
                continue
            is_open = row["state"] == "OPEN"
            if is_open:
                backlog[row["section"]].append(attributed_row(row, users))
                unique[row["id"]] = row
            for attribution in row["attributions"]:
                user_id = attribution["user_id"]
                if not user_matches(users[user_id], result.get("user_filter")):
                    continue
                group = groups.setdefault(
                    user_id,
                    {"user": users[user_id], "worked_prs": [], "worked_issues": []},
                )
                if not is_open:
                    group[row["section"]].append(
                        attributed_row(row, users, attribution)
                    )
                    unique[row["id"]] = row
        entry["users"] = []
        for group in sorted(
            groups.values(),
            key=lambda group: (group["user"]["login"].casefold(), group["user"]["id"]),
        ):
            entry["users"].append(
                {
                    "user": group["user"],
                    "sections": {
                        name: section(SECTIONS[name], group[name], base_coverage)
                        for name in ("worked_prs", "worked_issues")
                    },
                    "completed_items": len(group["worked_prs"])
                    + len(group["worked_issues"]),
                }
            )
        entry["backlog"] = {
            name: section(SECTIONS[name], rows, base_coverage)
            for name, rows in backlog.items()
        }
        all_rows = list(unique.values())
        if any(row["coverage"] != "complete" for row in all_rows):
            limitations.append("Some reported items have incomplete evidence.")
        entry["counts"] = {
            "total_items": len(all_rows),
            "prs": sum(row["kind"] == "pr" for row in all_rows),
            "issues": sum(row["kind"] == "issue" for row in all_rows),
            "uncertain_items": sum(
                row["project_scope"] == "uncertain" for row in all_rows
            ),
            "users": len(groups),
        }
        entry["limits"] = sorted(set(limitations))
        entry["coverage"] = (
            base_coverage
            if base_coverage != "complete"
            else "partial"
            if limitations
            else "complete"
        )
        entry["empty_users_message"] = (
            None
            if groups
            else "No matching contributors"
            if entry["coverage"] == "complete"
            else "No matching contributors observed; coverage incomplete"
        )
        entry["header_markdown"] = report_header(
            entry["repository"], result["window"], project, result.get("user_filter")
        )
    result["coverage"] = (
        "partial"
        if any(e["coverage"] != "complete" for e in result["repositories"])
        else "complete"
    )
    result["assessment_count"] = len(accepted)
    return result
