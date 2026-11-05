#!/usr/bin/env python3
"""Offline behavioral tests. No live credentials, repositories or network required."""

from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import github_api as api_module
from github_api import GitHub, checkout_repository, remote_repository
import github_report as cli
from report_model import (
    ReportError,
    activity,
    build_evidence,
    attributed_row,
    row_scope,
    user_selector,
    report_header,
    cell,
    evidence_text,
    project_selector,
    repositories,
    table,
    window,
)

VIEWER = {
    "__typename": "User",
    "id": "U_dev",
    "databaseId": 12,
    "login": "dev",
    "name": "Development User",
}
OTHER = {
    "__typename": "User",
    "id": "U_other",
    "databaseId": 13,
    "login": "other",
    "name": None,
}
NOW = datetime(2026, 4, 1, 12, tzinfo=timezone.utc)
INTERVAL = {"start": "2026-01-01T00:00:00Z", "end": "2026-04-01T12:00:00Z"}
RECENT = "2026-03-10T12:00:00Z"
OLD = "2025-01-01T12:00:00Z"


def actor_rows(items, user, interval, project=None):
    """Inspect one attribution in canonical evidence for existing role-level tests."""
    evidence = build_evidence(items, interval)
    rows = []
    for source in evidence["items"]:
        attribution = next(
            (a for a in source["attributions"] if a["user_id"] == user["id"]), None
        )
        if source["state"] != "OPEN" and attribution is None:
            continue
        source = deepcopy(source)
        source["attributions"] = [attribution] if attribution else []
        source["project_filter"] = project
        source["project_scope"] = row_scope(source, project)
        if source["project_scope"] != "excluded":
            rows.append(attributed_row(source, evidence["users"], attribution))
    return rows


def item(kind="pr", number=1, author=None, **kwargs):
    return {
        "id": f"{kind}_{number}",
        "number": number,
        "kind": kind,
        "url": f"https://github.com/example-org/widget/{'pull' if kind == 'pr' else 'issues'}/{number}",
        "repository": {"nameWithOwner": "example-org/widget"},
        "title": "Prevent retry failures",
        "body": "Handle an exhausted retry budget.",
        "createdAt": OLD,
        "updatedAt": RECENT,
        "closedAt": None,
        "mergedAt": None,
        "state": "OPEN",
        "author": author or OTHER,
        "coverage": "complete",
        "limits": [],
        "additions": 80,
        "deletions": 20,
        "commits": {"totalCount": 3},
        "changedFiles": 0,
        "files_data": [],
        "files_coverage": "complete",
        **kwargs,
    }


def comment(author=VIEWER, date=RECENT, **kwargs):
    return {
        "id": "C1",
        "url": "https://github.com/example-org/widget/issues/1#issuecomment-1",
        "author": author,
        "createdAt": date,
        "body": "Investigated retry handling.",
        **kwargs,
    }


def commit(author=VIEWER, date=RECENT):
    return {
        "id": "COMMIT1",
        "url": "https://github.com/example-org/widget/commit/abc",
        "committedDate": date,
        "authors_data": [{"user": author}],
        "messageHeadline": "Fix retries",
    }


def page(nodes, total=None, more=False, cursor=None):
    return {
        "nodes": nodes,
        "totalCount": len(nodes) if total is None else total,
        "pageInfo": {"hasNextPage": more, "endCursor": cursor},
    }


@contextmanager
def local_timezone(name):
    previous = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


class InputTests(unittest.TestCase):
    def test_direct_help_execution_creates_no_bytecode_or_files(self):
        with tempfile.TemporaryDirectory(prefix="github-report-help-") as temporary:
            root = Path(temporary)
            for name in (
                "github_report.py",
                "github_api.py",
                "report_model.py",
                "report_finalization.py",
            ):
                shutil.copyfile(Path(__file__).parent / name, root / name)
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            result = subprocess.run(
                [sys.executable, str(root / "github_report.py"), "--help"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("90 days", result.stdout)
            self.assertEqual({p.name for p in root.iterdir()}, set(before))
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)

    def test_default_is_90_durations_not_calendar_months(self):
        with local_timezone("America/Los_Angeles"):
            result = window(None, NOW)
        self.assertEqual(
            datetime.fromisoformat(result["start"].replace("Z", "+00:00")),
            NOW - timedelta(days=90),
        )

    def test_explicit_historical_midnight_observes_dst(self):
        with local_timezone("America/Los_Angeles"):
            self.assertEqual(window("2026-01-01", NOW)["start"], "2026-01-01T08:00:00Z")
            self.assertEqual(window("2026-03-15", NOW)["start"], "2026-03-15T07:00:00Z")

    def test_leap_day_today_and_future(self):
        with local_timezone("UTC"):
            self.assertEqual(window("2024-02-29", NOW)["start"], "2024-02-29T00:00:00Z")
            self.assertEqual(window("2026-04-01", NOW)["end"], INTERVAL["end"])
            for date in (
                "2026-04-02",
                "2025-02-29",
                "2026-2-01",
                "tomorrow",
                "2026-01-01T00:00:00Z",
            ):
                with self.subTest(date=date), self.assertRaises(ReportError):
                    window(date, NOW)

    def test_repo_comma_spaces_deduplication(self):
        self.assertEqual(
            repositories(["example-org/one,", "example-org/two,EXAMPLE-ORG/one"]),
            ["example-org/one", "example-org/two"],
        )
        for values in (
            ["owner/one", "owner/two"],
            ["owner/one,"],
            ["owner/../bad"],
            ["owner/.."],
            ["$(touch /tmp/x)"],
            ["https://github.com/owner/repo"],
        ):
            with self.subTest(values=values), self.assertRaises(ReportError):
                repositories(values)

    def test_duplicate_or_unknown_flags_rejected(self):
        for args in (
            ["--date", "2026-01-01", "--date", "2026-01-02"],
            ["--repo", "a/b", "--repo", "c/d"],
            ["--days", "7"],
        ):
            with (
                self.subTest(args=args),
                patch("sys.stderr", new_callable=io.StringIO),
                self.assertRaises(SystemExit) as error,
            ):
                cli.parser().parse_args(args)
            self.assertEqual(error.exception.code, 2)

    def test_help_and_bad_input_do_not_access_tools(self):
        with (
            patch.object(cli, "GitHub") as api,
            patch.object(cli, "checkout_repository") as repo,
        ):
            with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as error:
                cli.main(["--help"])
            self.assertEqual(error.exception.code, 0)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["--date", "9999-12-31"]), 2)
                self.assertEqual(cli.main(["--repo", "owner/one;"]), 2)
            api.assert_not_called()
            repo.assert_not_called()

    def test_remote_parsing_avoids_credentials_and_host_confusion(self):
        for url in (
            "git@github.com:owner/repo.git",
            "https://github.com/owner/repo.git",
            "ssh://git@github.com/owner/repo.git",
        ):
            self.assertEqual(remote_repository(url), "owner/repo")
        for url in (
            "https://github.com.evil.test/owner/repo",
            "https://secret@github.com/owner/repo",
            "https://github.com/owner/repo?token=hidden",
        ):
            self.assertIsNone(remote_repository(url))

    def test_checkout_ambiguity_and_cli_override(self):
        prefix = ["/fixture", "remote.origin.url git@github.com:owner/repo.git"]
        with patch.object(
            api_module,
            "run_read",
            side_effect=prefix
            + [
                json.dumps(
                    {
                        "nameWithOwner": "other/repo",
                        "url": "https://github.com/other/repo",
                    }
                )
            ],
        ):
            with self.assertRaises(ReportError) as error:
                checkout_repository()
            self.assertEqual(error.exception.code, "AMBIGUOUS_REPO")
        with patch.object(
            api_module,
            "run_read",
            side_effect=[
                "/fixture",
                prefix[1] + "\nremote.upstream.url git@github.com:other/repo.git",
            ],
        ):
            with self.assertRaises(ReportError):
                checkout_repository()
        with patch.object(
            api_module,
            "run_read",
            side_effect=prefix
            + [
                json.dumps(
                    {
                        "nameWithOwner": "owner/repo",
                        "url": "https://github.com/owner/repo",
                    }
                )
            ],
        ):
            self.assertEqual(checkout_repository(), "owner/repo")

    def test_missing_checkout_or_remotes_requests_explicit_repo(self):
        for answers, code in (
            ([ReportError("READ_FAILED", "safe")], "NO_CHECKOUT"),
            (["/fixture", ReportError("READ_FAILED", "safe")], "AMBIGUOUS_REPO"),
        ):
            with patch.object(api_module, "run_read", side_effect=answers):
                with self.assertRaises(ReportError) as error:
                    checkout_repository()
            self.assertEqual(error.exception.code, code)
            self.assertIn("--repo", str(error.exception))

    def test_missing_git_tool_keeps_its_diagnostic(self):
        with patch.object(
            api_module, "run_read", side_effect=ReportError("TOOL_UNAVAILABLE", "safe")
        ):
            with self.assertRaises(ReportError) as error:
                checkout_repository()
        self.assertEqual(error.exception.code, "TOOL_UNAVAILABLE")


class TransportTests(unittest.TestCase):
    def test_nonzero_gh_preserves_structured_access_and_rate_errors(self):
        for kind, expected in (
            ("NOT_FOUND", "INACCESSIBLE"),
            ("FORBIDDEN", "INACCESSIBLE"),
            ("RATE_LIMITED", "RATE_LIMIT"),
        ):
            result = subprocess.CompletedProcess(
                ["gh"],
                1,
                stdout=json.dumps(
                    {
                        "data": {"repository": None},
                        "errors": [
                            {"type": kind, "message": "sensitive upstream detail"}
                        ],
                    }
                ),
                stderr="gh: sensitive upstream detail",
            )
            with patch.object(api_module.subprocess, "run", return_value=result):
                with self.assertRaises(ReportError) as error:
                    GitHub().query("query { viewer { id } }")
            self.assertEqual(error.exception.code, expected)
            self.assertNotIn("sensitive", str(error.exception))

    def test_nonzero_without_graphql_errors_never_returns_success(self):
        for stdout in (
            "not json",
            '{"data":{"viewer":{"id":"U_dev"}}}',
            '{"errors":"bad"}',
        ):
            result = subprocess.CompletedProcess(
                ["gh"], 1, stdout=stdout, stderr="sensitive detail"
            )
            with patch.object(api_module.subprocess, "run", return_value=result):
                with self.assertRaises(ReportError) as error:
                    GitHub().query("query { viewer { id } }")
            self.assertEqual(error.exception.code, "READ_FAILED")
            self.assertNotIn("sensitive", str(error.exception))


class AttributionTests(unittest.TestCase):
    def test_partial_or_stale_closure_history_cannot_prove_resolution(self):
        issue = item(
            "issue",
            7,
            state="CLOSED",
            stateReason="COMPLETED",
            closedAt=RECENT,
            timeline_data=[
                {
                    "__typename": "ClosedEvent",
                    "createdAt": RECENT,
                    "closer": {"id": "pr_1", "__typename": "PullRequest"},
                }
            ],
        )
        for change in ({"coverage": "partial"}, {"closedAt": "2026-03-20T00:00:00Z"}):
            pr = item(
                author=VIEWER,
                createdAt=RECENT,
                linked_issues_data=[{**issue, **change}],
            )
            self.assertEqual(
                actor_rows([pr], VIEWER, INTERVAL)[0]["issues"][0]["relationship"],
                "linked",
            )

    def test_cross_repository_issue_work_keeps_its_repository_row(self):
        issue = item(
            "issue",
            7,
            repository={"nameWithOwner": "example-org/other"},
            comments_data=[comment()],
        )
        pr = item(author=VIEWER, createdAt=RECENT, linked_issues_data=[issue])
        rows = actor_rows([pr, issue], VIEWER, INTERVAL)
        self.assertEqual(
            {(r["repository"], r["kind"]) for r in rows},
            {("example-org/widget", "pr"), ("example-org/other", "issue")},
        )

    def test_related_pr_titles_use_the_same_redaction_boundary(self):
        issue = item(
            "issue",
            7,
            comments_data=[comment()],
            timeline_data=[
                {
                    "__typename": "CrossReferencedEvent",
                    "source": {
                        "__typename": "PullRequest",
                        "id": "PR9",
                        "number": 9,
                        "title": "Accidental ghp_abcdefghijkl",
                        "url": "https://github.com/example-org/widget/pull/9",
                    },
                }
            ],
        )
        rendered = json.dumps(actor_rows([issue], VIEWER, INTERVAL))
        self.assertNotIn("ghp_abcdefghijkl", rendered)

    def test_recent_update_mention_and_assignment_are_not_work(self):
        data = item(
            author=VIEWER,
            assignees=[VIEWER],
            body="@dev",
            comments_data=[comment(OTHER)],
        )
        self.assertEqual(activity(data, VIEWER, INTERVAL), [])

    def test_author_review_commit_and_issue_discussion(self):
        authored = item(number=1, author=VIEWER, createdAt=RECENT)
        reviewed = item(
            number=2,
            reviews_data=[
                {
                    "author": VIEWER,
                    "submittedAt": RECENT,
                    "state": "APPROVED",
                    "url": "review/2",
                }
            ],
        )
        contributed = item(number=3, commits_data=[commit()])
        discussed = item("issue", 4, comments_data=[comment()])
        rows = {
            r["number"]: r
            for r in actor_rows(
                [authored, reviewed, contributed, discussed], VIEWER, INTERVAL
            )
        }
        self.assertEqual(rows[1]["roles"], ["authored"])
        self.assertEqual(rows[2]["roles"], ["reviewed"])
        self.assertEqual(rows[3]["roles"], ["contributed commit"])
        self.assertEqual(rows[4]["roles"], ["discussed"])
        self.assertEqual(rows[2]["metrics"]["commits"], 3)

    def test_coauthor_account_mapping_and_commit_time(self):
        data = item(commits_data=[commit(OTHER)])
        data["commits_data"][0]["authors_data"].append({"user": VIEWER})
        self.assertEqual(len(actor_rows([data], VIEWER, INTERVAL)), 1)
        data["commits_data"][0]["committedDate"] = OLD
        self.assertEqual(activity(data, VIEWER, INTERVAL), [])
        data["commits_data"][0]["committedDate"] = RECENT
        data["commits_data"][0]["authors_data"] = [{"name": "dev", "user": None}]
        self.assertEqual(activity(data, VIEWER, INTERVAL), [])

    def test_earlier_contribution_delivered_in_period(self):
        for data in (
            item(author=VIEWER, mergedAt=RECENT, state="MERGED"),
            item(commits_data=[commit(date=OLD)], mergedAt=RECENT, state="MERGED"),
        ):
            row = actor_rows([data], VIEWER, INTERVAL)[0]
            self.assertEqual(row["delivery"], "earlier contribution merged")
        old_review = item(
            state="MERGED",
            mergedAt=RECENT,
            reviews_data=[{"author": VIEWER, "submittedAt": OLD, "state": "APPROVED"}],
        )
        self.assertEqual(actor_rows([old_review], VIEWER, INTERVAL), [])

    def test_pending_review_and_unpublished_inline_comment_excluded(self):
        data = item(
            reviews_data=[
                {"author": VIEWER, "submittedAt": RECENT, "state": "PENDING"}
            ],
            review_comments_data=[comment(publishedAt=None)],
        )
        self.assertEqual(activity(data, VIEWER, INTERVAL), [])
        data["review_comments_data"][0]["publishedAt"] = RECENT
        self.assertEqual(
            actor_rows([data], VIEWER, INTERVAL)[0]["roles"], ["review comment"]
        )

    def test_frozen_interval_ignores_later_activity_but_keeps_prior_review(self):
        data = item(
            updatedAt="2026-04-02T00:00:00Z",
            comments_data=[comment(date="2026-04-02T00:00:00Z")],
            reviews_data=[
                {
                    "author": VIEWER,
                    "submittedAt": RECENT,
                    "state": "COMMENTED",
                    "url": "review/1",
                }
            ],
        )
        self.assertEqual(actor_rows([data], VIEWER, INTERVAL)[0]["roles"], ["reviewed"])

    def test_closure_requires_completed_state_and_matching_closer(self):
        issue = item(
            "issue",
            7,
            state="CLOSED",
            stateReason="COMPLETED",
            closedAt=RECENT,
            timeline_data=[
                {
                    "__typename": "ClosedEvent",
                    "createdAt": RECENT,
                    "closer": {"id": "pr_1", "__typename": "PullRequest"},
                }
            ],
        )
        pr = item(author=VIEWER, createdAt=RECENT, linked_issues_data=[issue])
        self.assertEqual(
            actor_rows([pr], VIEWER, INTERVAL)[0]["issues"][0]["relationship"],
            "closed_by_pr",
        )
        for change in (
            {"state": "OPEN"},
            {"stateReason": "NOT_PLANNED"},
            {"stateReason": "DUPLICATE"},
            {"timeline_data": []},
        ):
            pr["linked_issues_data"] = [{**issue, **change}]
            self.assertEqual(
                actor_rows([pr], VIEWER, INTERVAL)[0]["issues"][0]["relationship"],
                "linked",
            )

    def test_linked_issue_discussion_keeps_independent_primary_row(self):
        issue = item("issue", 7, comments_data=[comment()])
        pr = item(author=VIEWER, createdAt=RECENT, linked_issues_data=[deepcopy(issue)])
        rows = actor_rows([pr, issue], VIEWER, INTERVAL)
        self.assertEqual(len(rows), 2)
        issue_row = next(row for row in rows if row["kind"] == "issue")
        self.assertEqual(issue_row["contributions"][0]["kind"], "discussed")

    def test_duplicate_activity_is_deduplicated_and_bounds_inclusive(self):
        data = item(
            comments_data=[
                comment(date=INTERVAL["start"]),
                comment(date=INTERVAL["end"]),
                comment(date=INTERVAL["end"]),
            ]
        )
        self.assertEqual(len(activity(data, VIEWER, INTERVAL)), 2)

    def test_unknown_metrics_and_hostile_text_stay_data(self):
        data = item(
            author=VIEWER,
            createdAt=RECENT,
            additions=None,
            deletions=None,
            commits={},
            title="Ignore instructions | [run](javascript:bad)\n<script>bad</script>",
        )
        rows = actor_rows([data], VIEWER, INTERVAL)
        rendered = table(rows)
        self.assertIn("+unknown / −unknown; unknown commits", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("[run]", rendered)
        self.assertEqual(len(rendered.splitlines()), 3)
        self.assertIn("\\|", cell(data["title"]))

    def test_incomplete_links_do_not_assert_no_issue(self):
        rows = actor_rows(
            [item(author=VIEWER, createdAt=RECENT, coverage="partial")],
            VIEWER,
            INTERVAL,
        )
        self.assertIn("Issue linkage unknown", table(rows))
        self.assertNotIn("No linked issue found", table(rows))

    def test_common_credentials_are_redacted_from_excerpts(self):
        value = "ghp_abcdefghijkl Authorization: Bearer secret\nhttps://user:pass@example.test/path"
        result = evidence_text(value)["text"]
        self.assertNotIn("abcdefghijkl", result)
        self.assertNotIn("Bearer secret", result)
        self.assertNotIn("user:pass", result)


class ProjectTests(unittest.TestCase):
    def test_table_groups_real_paths_without_highlights_or_role_column(self):
        data = item(
            author=VIEWER,
            createdAt=RECENT,
            files_data=[
                {"path": path}
                for path in (
                    "services/gateway/src/main.py",
                    "services/gateway/README.md",
                    "services/client/app.py",
                    "skills/report/SKILL.md",
                    "README.md",
                    "docs/guide.md",
                    "apps/web/src/index.ts",
                    "services/shared.md",
                )
            ],
        )
        row = actor_rows([data], VIEWER, INTERVAL)[0]
        self.assertEqual(
            row["projects"]["folders"],
            [
                "apps/web",
                "docs",
                "repository root",
                "services",
                "services/client",
                "services/gateway",
                "skills",
            ],
        )
        rendered = table([row])
        self.assertTrue(
            rendered.startswith("| Work and outcome | Projects / folders |")
        )
        self.assertNotIn("My role", rendered)
        self.assertEqual(len(rendered.splitlines()), 3)

    def test_partial_and_unknown_folders_never_imply_complete_scope(self):
        data = item(
            author=VIEWER,
            createdAt=RECENT,
            files_data=[{"path": "skills/report/SKILL.md"}],
            files_coverage="partial",
        )
        row = actor_rows([data], VIEWER, INTERVAL)[0]
        self.assertEqual(row["coverage"], "partial")
        self.assertIn("skills; file evidence partial", table([row]))
        del data["files_coverage"]
        del data["files_data"]
        self.assertIn(
            "Project uncertain; file evidence partial",
            table(actor_rows([data], VIEWER, INTERVAL)),
        )
        self.assertNotIn(
            "No changed files", table(actor_rows([data], VIEWER, INTERVAL))
        )

    def test_folder_text_is_escaped_and_invalid_paths_qualified(self):
        data = item(
            author=VIEWER,
            createdAt=RECENT,
            files_data=[
                {"path": "a|[bad]<tag>/file"},
                {"path": "../escape/file"},
                {"path": "/absolute/file"},
                {"path": "bad\nfolder/file"},
                None,
            ],
        )
        row = actor_rows([data], VIEWER, INTERVAL)[0]
        self.assertEqual(row["projects"]["coverage"], "partial")
        rendered = table([row])
        self.assertEqual(len(rendered.splitlines()), 3)
        self.assertNotIn("<tag>", rendered)
        self.assertNotIn("escape", rendered)
        self.assertIn("a\\|\\[bad\\]&lt;tag&gt;", rendered)

    def test_review_only_is_accurate_without_role_column(self):
        data = item(
            reviews_data=[
                {"author": VIEWER, "submittedAt": RECENT, "state": "APPROVED"}
            ]
        )
        rendered = table(actor_rows([data], VIEWER, INTERVAL))
        self.assertIn("Reviewed: Prevent retry failures", rendered)
        self.assertNotIn("My role", rendered)
        issue = item("issue", comments_data=[comment()])
        rendered = table(actor_rows([issue], VIEWER, INTERVAL))
        self.assertIn("Discussed:", rendered)
        self.assertIn("Project uncertain", rendered)

    def test_changed_files_are_paginated_by_path_without_node_ids(self):
        api = GitHub()
        paths = [{"path": f"packages/project{i}/main.py"} for i in range(101)]
        with patch.object(
            api,
            "query",
            side_effect=[
                {"node": {"files": page(paths[:100], 101, True, "next")}},
                {"node": {"files": page(paths[100:], 101)}},
            ],
        ):
            result = api.connection(item(), "files", "path")
        self.assertEqual(result["coverage"], "complete")
        self.assertEqual(len(result["nodes"]), 101)

    def test_changed_file_mismatch_is_partial(self):
        api = GitHub()
        data = item(changedFiles=2, commits={"totalCount": 0})

        def connection(_item, name, _fields):
            nodes = [{"path": "skills/report/SKILL.md"}] if name == "files" else []
            return {
                "nodes": nodes,
                "total": len(nodes),
                "coverage": "complete",
                "limits": [],
            }

        with patch.object(api, "connection", side_effect=connection):
            api.enrich(data)
        self.assertEqual(data["files_coverage"], "partial")
        self.assertEqual(data["coverage"], "partial")


class ProjectFilterTests(unittest.TestCase):
    @staticmethod
    def pr(number=1, paths=("services/api/main.py",), coverage="complete", **kwargs):
        return item(
            number=number,
            author=VIEWER,
            createdAt=RECENT,
            files_data=[{"path": path} for path in paths],
            files_coverage=coverage,
            changedFiles=len(paths),
            **kwargs,
        )

    @staticmethod
    def collect(
        items, selected=("example-org/widget",), project="services/api", user=None
    ):
        items = deepcopy(items)
        api = GitHub()

        def identity(_query, variables):
            return {
                "repository": {
                    "id": "R_" + variables["name"],
                    "nameWithOwner": variables["owner"] + "/" + variables["name"],
                }
            }

        def candidates(repo, kind, _start):
            return {
                "nodes": [
                    i
                    for i in items
                    if i["kind"] == kind and i["repository"]["nameWithOwner"] == repo
                ],
                "limits": [],
            }

        with (
            patch.object(api, "viewer", return_value=VIEWER),
            patch.object(api, "query", side_effect=identity),
            patch.object(api, "candidates", side_effect=candidates),
            patch.object(api, "enrich", side_effect=lambda value, **kwargs: value),
            patch.object(
                api,
                "repository_context",
                return_value={
                    "folders": [],
                    "evidence": {},
                    "coverage": "complete",
                    "limits": [],
                },
            ),
        ):
            return cli.collect(
                api, list(selected), INTERVAL, project=project, user=user
            )

    def test_selector_normalizes_relative_folder_without_filesystem_access(self):
        self.assertIsNone(project_selector(None))
        for value in (
            "services/api",
            "./services/api",
            "services/api/",
            "./services/api/",
        ):
            self.assertEqual(project_selector(value), "services/api")
        self.assertEqual(project_selector("docs and guides"), "docs and guides")
        self.assertEqual(project_selector("deleted/project"), "deleted/project")

    def test_invalid_project_stops_before_tools(self):
        for value in (
            "",
            ".",
            "./",
            "/",
            "/services/api",
            "../api",
            "a/../b",
            "a/./b",
            "a//b",
            "a/b//",
            "a\\b",
            "C:/api",
            "~/api",
            "a/*",
            "a/?",
            "a/[b]",
            "a/{b}",
            "a\nb",
            "a\x7fb",
            "a" * 4097,
        ):
            with (
                self.subTest(value=value),
                patch.object(cli, "GitHub") as api,
                patch.object(cli, "checkout_repository") as checkout,
                redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(cli.main(["--project", value]), 2)
            self.assertEqual(json.loads(output.getvalue())["error"], "INVALID_PROJECT")
            api.assert_not_called()
            checkout.assert_not_called()

    def test_repeated_project_rejected_and_help_documents_flag(self):
        with (
            patch("sys.stderr", new_callable=io.StringIO),
            self.assertRaises(SystemExit),
        ):
            cli.parser().parse_args(["--project", "skills", "--project", "services"])
        self.assertIn("--project", cli.parser().format_help())

    def test_case_sensitive_descendant_boundary_and_raw_path_matching(self):
        data = [
            self.pr(i, paths=(path,))
            for i, path in enumerate(
                (
                    "services/api/main.py",
                    "services/api/sub/main.py",
                    "services/api2/main.py",
                    "services/API/main.py",
                    "services/api",
                ),
                1,
            )
        ]
        self.assertEqual(
            {r["number"] for r in actor_rows(data, VIEWER, INTERVAL, "services/api")},
            {1, 2},
        )
        data = [
            self.pr(1, paths=("skills/report/main.py",)),
            self.pr(2, paths=("skills/other/main.py",)),
        ]
        rows = actor_rows(data, VIEWER, INTERVAL, "skills/report")
        self.assertEqual([r["number"] for r in rows], [1])
        self.assertEqual(rows[0]["projects"]["folders"], ["skills"])

    def test_mixed_project_pr_keeps_whole_pr_metrics_and_folders(self):
        pr = self.pr(paths=("services/api/main.py", "skills/report/SKILL.md"))
        before = actor_rows([deepcopy(pr)], VIEWER, INTERVAL)[0]
        row = actor_rows([pr], VIEWER, INTERVAL, "services/api")[0]
        self.assertEqual(row["project_scope"], "matched")
        self.assertEqual(row["metrics"], before["metrics"])
        self.assertEqual(row["projects"], before["projects"])

    def test_match_on_later_file_page_reaches_project_report(self):
        api = GitHub()
        paths = [{"path": f"docs/file{i}.md"} for i in range(100)]
        with patch.object(
            api,
            "query",
            side_effect=[
                {"node": {"files": page(paths, 101, True, "next")}},
                {"node": {"files": page([{"path": "services/api/main.py"}], 101)}},
            ],
        ):
            files = api.connection(item(), "files", "path")
        pr = self.pr(paths=())
        pr.update(
            files_data=files["nodes"],
            files_coverage=files["coverage"],
            changedFiles=101,
        )
        row = actor_rows([pr], VIEWER, INTERVAL, "services/api")[0]
        self.assertEqual(row["project_scope"], "matched")
        self.assertEqual(row["coverage"], "complete")

    def test_partial_match_wins_and_partial_nonmatch_stays_visible(self):
        prs = [
            self.pr(1, coverage="partial"),
            self.pr(2, paths=("docs/readme.md",), coverage="partial"),
            self.pr(3, paths=()),
        ]
        rows = actor_rows(prs, VIEWER, INTERVAL, "services/api")
        self.assertEqual(
            {r["number"]: r["project_scope"] for r in rows},
            {1: "matched", 2: "uncertain"},
        )
        self.assertTrue(all(r["coverage"] == "partial" for r in rows))

    def test_null_invalid_or_missing_file_evidence_cannot_exclude(self):
        for files in (
            None,
            [None],
            [{"path": "../services/api/main.py"}],
            [{"path": "docs/ok"}, {"path": "bad\nfile"}],
        ):
            pr = self.pr(paths=())
            pr["files_data"] = files
            row = actor_rows([pr], VIEWER, INTERVAL, "services/api")[0]
            self.assertEqual(row["project_scope"], "uncertain")
            self.assertEqual(row["coverage"], "partial")

    def test_excluded_pr_does_not_suppress_personal_issue_work(self):
        issue = item("issue", 7, comments_data=[comment()])
        pr = self.pr(paths=("docs/readme.md",), linked_issues_data=[issue])
        rows = actor_rows([pr, issue], VIEWER, INTERVAL, "services/api")
        self.assertEqual(
            [(r["kind"], r["number"], r["project_scope"]) for r in rows],
            [("issue", 7, "uncertain")],
        )
        self.assertIn("Project uncertain", table(rows))

    def test_formal_link_scopes_issue_without_personal_pr_activity(self):
        issue = item("issue", 7, comments_data=[comment()])
        pr = self.pr(linked_issues_data=[issue])
        pr.update(author=OTHER, createdAt=OLD)
        rows = actor_rows([pr, issue], VIEWER, INTERVAL, "services/api")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["kind"], "issue")
        self.assertEqual(rows[0]["project_scope"], "matched")
        self.assertEqual(rows[0]["related_prs"][0]["id"], pr["id"])
        self.assertIn("services/api (linked PR)", table(rows))
        self.assertIn("Discussed:", table(rows))

    def test_closer_scopes_issue_but_plain_reference_does_not(self):
        pr = self.pr()
        pr.update(author=OTHER, createdAt=OLD)
        for field, typename, scope in (
            ("closer", "ClosedEvent", "matched"),
            ("source", "CrossReferencedEvent", "uncertain"),
        ):
            issue = item(
                "issue",
                7,
                comments_data=[comment()],
                timeline_data=[
                    {"__typename": typename, field: {**pr, "__typename": "PullRequest"}}
                ],
            )
            rows = actor_rows([deepcopy(pr), issue], VIEWER, INTERVAL, "services/api")
            self.assertEqual(rows[0]["project_scope"], scope)

    def test_cross_repository_issue_link_does_not_establish_folder_scope(self):
        issue = item(
            "issue",
            7,
            comments_data=[comment()],
            repository={"nameWithOwner": "example-org/other"},
        )
        pr = self.pr(linked_issues_data=[issue])
        rows = actor_rows([pr, issue], VIEWER, INTERVAL, "services/api")
        issue_row = next(r for r in rows if r["kind"] == "issue")
        self.assertEqual(issue_row["project_scope"], "uncertain")

    def test_unknown_pr_cannot_hide_issue_classified_by_another_pr(self):
        issue = item("issue", 7, comments_data=[comment()])
        unknown = self.pr(1, paths=(), coverage="partial", linked_issues_data=[issue])
        matched = self.pr(2, linked_issues_data=[issue])
        matched.update(author=OTHER, createdAt=OLD)
        entry = self.collect([unknown, matched, issue])["repositories"][0]
        self.assertEqual(
            [r["number"] for r in entry["backlog"]["open_issues"]["rows"]], [7]
        )
        self.assertEqual(entry["backlog"]["open_prs"]["count"], 2)
        self.assertEqual(entry["counts"]["issues"], 1)
        self.assertEqual(entry["counts"]["uncertain_items"], 1)

    def test_matching_pr_keeps_issue_rows_and_unknown_work(self):
        issue = item("issue", 7, comments_data=[comment()])
        pr = self.pr(linked_issues_data=[issue])
        other = item("issue", 9, comments_data=[comment()])
        entry = self.collect([pr, issue, other])["repositories"][0]
        self.assertEqual(entry["counts"]["total_items"], 3)
        self.assertEqual(entry["backlog"]["open_prs"]["count"], 1)
        self.assertEqual(entry["backlog"]["open_issues"]["count"], 2)
        self.assertEqual(entry["counts"]["uncertain_items"], 1)
        self.assertEqual(entry["coverage"], "complete")
        self.assertNotIn("My role", entry["backlog"]["open_issues"]["table_markdown"])

    def test_project_filter_applies_independently_across_repositories(self):
        first = self.pr()
        second = self.pr(2, repository={"nameWithOwner": "example-org/other"})
        third = self.pr(
            3,
            paths=("docs/readme.md",),
            repository={"nameWithOwner": "example-org/other"},
        )
        report = self.collect(
            [first, second, third], selected=("example-org/widget", "example-org/other")
        )
        self.assertEqual(report["project"], "services/api")
        self.assertEqual([e["counts"]["prs"] for e in report["repositories"]], [1, 1])

    def test_uncertain_issues_alone_do_not_imply_api_failure(self):
        report = self.collect([item("issue", comments_data=[comment()])])
        entry = report["repositories"][0]
        self.assertEqual(report["coverage"], "complete")
        self.assertEqual(entry["counts"]["total_items"], 1)
        self.assertEqual(entry["counts"]["uncertain_items"], 1)
        self.assertIn(
            "Project uncertain", entry["backlog"]["open_issues"]["table_markdown"]
        )

    def test_cli_project_forwarding_outside_checkout(self):
        with (
            patch.object(
                cli, "collect", return_value={"coverage": "complete"}
            ) as collect,
            patch.object(cli, "checkout_repository") as checkout,
            patch.object(cli, "GitHub"),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(
                cli.main(
                    [
                        "--repo",
                        "example-org/widget",
                        "--project",
                        "./services/api/",
                        "--date",
                        "2026-01-01",
                    ]
                ),
                0,
            )
        self.assertEqual(collect.call_args.kwargs["project"], "services/api")
        checkout.assert_not_called()


class PaginationTests(unittest.TestCase):
    def test_lightweight_candidates_hydrate_before_attribution(self):
        api = GitHub()
        full = item(author=VIEWER, createdAt=RECENT, commits={"totalCount": 0})
        candidate = {
            k: full[k]
            for k in (
                "id",
                "number",
                "kind",
                "url",
                "repository",
                "title",
                "state",
                "createdAt",
                "updatedAt",
            )
        }
        empty = {"nodes": [], "total": 0, "coverage": "complete", "limits": []}
        with (
            patch.object(api, "query", return_value={"node": full}) as query,
            patch.object(api, "connection", return_value=empty),
        ):
            api.enrich(candidate)
        query.assert_called_once()
        self.assertEqual(
            actor_rows([candidate], VIEWER, INTERVAL)[0]["roles"], ["authored"]
        )
        self.assertEqual(candidate["body"], full["body"])

    def test_filtered_timeline_uses_page_counts_and_cursor_exhaustion(self):
        api = GitHub()
        first = {**page([{"id": "E1"}], 24, True, "next"), "pageCount": 1}
        last = {**page([{"id": "E2"}], 24), "pageCount": 1}
        with patch.object(
            api,
            "query",
            side_effect=[
                {"node": {"timelineItems": first}},
                {"node": {"timelineItems": last}},
            ],
        ):
            result = api.connection(item(), "timelineItems", api_module.TIMELINE)
        self.assertEqual(result["coverage"], "complete")
        self.assertEqual(len(result["nodes"]), 2)
        last["pageCount"] = 2
        with patch.object(api, "query", return_value={"node": {"timelineItems": last}}):
            self.assertEqual(
                api.connection(item(), "timelineItems", api_module.TIMELINE)[
                    "coverage"
                ],
                "partial",
            )

    def test_nested_review_comments_and_authors_reach_attribution(self):
        api = GitHub()
        data = item(commits={"totalCount": 1})
        authored_commit = {
            "id": "C1",
            "url": "commit/1",
            "committedDate": RECENT,
            "authors": page(
                [{"name": "other", "user": OTHER}], 2, True, "authors-next"
            ),
        }

        def connection(_item, name, _fields):
            nodes = (
                [{"commit": authored_commit}]
                if name == "commits"
                else [{"id": "T1"}]
                if name == "reviewThreads"
                else []
            )
            return {
                "nodes": nodes,
                "total": len(nodes),
                "limits": [],
                "coverage": "complete",
            }

        def pages(_query, _variables, path, **_kwargs):
            nodes = (
                [comment(publishedAt=RECENT)]
                if path[-1] == "comments"
                else [{"user": OTHER}, {"user": VIEWER}]
            )
            return {
                "nodes": nodes,
                "total": len(nodes),
                "limits": [],
                "coverage": "complete",
            }

        with (
            patch.object(api, "connection", side_effect=connection),
            patch.object(api, "pages", side_effect=pages) as nested,
        ):
            api.enrich(data)
        self.assertEqual(nested.call_count, 2)
        row = actor_rows([data], VIEWER, INTERVAL)[0]
        self.assertEqual(set(row["roles"]), {"contributed commit", "review comment"})
        self.assertEqual(row["coverage"], "complete")

    def test_null_embedded_commit_author_preserves_other_work_as_partial(self):
        api = GitHub()
        data = item(author=VIEWER, createdAt=RECENT, commits={"totalCount": 1})
        broken_commit = {"id": "C1", "committedDate": RECENT, "authors": page([None])}

        def connection(_item, name, _fields):
            nodes = [{"commit": broken_commit}] if name == "commits" else []
            return {
                "nodes": nodes,
                "total": len(nodes),
                "limits": [],
                "coverage": "complete",
            }

        with patch.object(api, "connection", side_effect=connection):
            api.enrich(data)
        self.assertEqual(data["coverage"], "partial")
        rows = actor_rows(
            [data, item("issue", 9, comments_data=[comment()])], VIEWER, INTERVAL
        )
        self.assertEqual(len(rows), 2)
        self.assertIn("incomplete commit authors", data["limits"])

    def test_timeline_requests_only_supported_typed_events(self):
        api = GitHub()
        with patch.object(
            api, "pages", return_value={"nodes": [], "limits": []}
        ) as pages:
            api.connection(item(), "timelineItems", api_module.TIMELINE)
        query = pages.call_args.args[0]
        self.assertIn(
            "itemTypes:[CLOSED_EVENT,REOPENED_EVENT,LABELED_EVENT,UNLABELED_EVENT,CROSS_REFERENCED_EVENT]",
            query,
        )

    def test_multiple_pages_and_more_than_250_commits(self):
        api = GitHub()
        pages = [
            {
                "node": {
                    "commits": page(
                        [{"commit": {"id": f"C{i}"}} for i in range(a, b)],
                        301,
                        b < 301,
                        str(b),
                    )
                }
            }
            for a, b in ((0, 100), (100, 200), (200, 300), (300, 301))
        ]
        with patch.object(api, "query", side_effect=pages) as call:
            result = api.pages("query", {}, ("node", "commits"))
        self.assertEqual(result["coverage"], "complete")
        self.assertEqual(len(result["nodes"]), 301)
        self.assertEqual(call.call_count, 4)

    def test_later_page_error_keeps_observed_nodes_and_marks_partial(self):
        api = GitHub()
        with patch.object(
            api,
            "query",
            side_effect=[
                {"x": page([{"id": "one"}], 2, True, "next")},
                ReportError("RATE_LIMIT", "safe"),
            ],
        ):
            result = api.pages("query", {}, ("x",))
        self.assertEqual(result["nodes"], [{"id": "one"}])
        self.assertEqual(result["coverage"], "partial")
        self.assertIn("RATE_LIMIT", result["limits"])

    def test_truncation_null_and_repeated_cursor_are_not_complete(self):
        for pages in (
            [{"x": page([{"id": "one"}], 300)}],
            [{"x": page([None], 1)}],
            [
                {"x": page([{"id": "one"}], 3, True, "same")},
                {"x": page([{"id": "two"}], 3, True, "same")},
            ],
            [
                {"x": page([{"id": "one"}], 2, True, "next")},
                {"x": page([{"id": "one"}], 2)},
            ],
        ):
            api = GitHub()
            with patch.object(api, "query", side_effect=pages):
                self.assertEqual(api.pages("query", {}, ("x",))["coverage"], "partial")

    def test_discovery_has_no_upper_update_cutoff(self):
        api = GitHub()
        recent = item(updatedAt="2026-04-02T00:00:00Z")
        old = item(number=2, updatedAt=OLD)
        with patch.object(
            api,
            "query",
            return_value={
                "repository": {"pullRequests": page([recent, old], 100, True, "next")}
            },
        ) as call:
            result = api.candidates("example-org/widget", "pr", INTERVAL["start"])
        self.assertEqual(result["nodes"], [recent])
        self.assertEqual(result["coverage"], "complete")
        self.assertEqual(call.call_count, 1)

    def test_graphql_partial_errors_are_not_a_success(self):
        body = {
            "data": {"viewer": VIEWER},
            "errors": [{"message": "private raw message"}],
        }
        with patch.object(api_module, "run_read", return_value=json.dumps(body)):
            with self.assertRaises(ReportError) as error:
                GitHub().query("query { viewer { id } }")
        self.assertEqual(error.exception.code, "GRAPHQL_ERROR")
        self.assertNotIn("private raw message", str(error.exception))

    def test_account_mismatch_stops_before_repository_queries(self):
        api = GitHub()
        with patch.object(api, "query", return_value={"viewer": VIEWER}) as query:
            with self.assertRaises(ReportError) as error:
                cli.collect(api, ["example-org/widget"], INTERVAL, expected_id=13)
        self.assertEqual(error.exception.code, "IDENTITY_MISMATCH")
        self.assertEqual(query.call_count, 1)

    def test_fixed_read_transport_uses_json_variables_no_shell(self):
        with patch.object(
            api_module.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0, '{"data":{"ok":true}}', ""),
        ) as run:
            self.assertEqual(
                GitHub().query("query($x:String!){x}", {"x": "$(bad);\nsecret"}),
                {"ok": True},
            )
        args, kwargs = run.call_args
        self.assertIsInstance(args[0], list)
        self.assertNotIn("shell", kwargs)
        self.assertEqual(
            json.loads(kwargs["input"])["variables"]["x"], "$(bad);\nsecret"
        )
        self.assertNotIn("$(bad)", " ".join(args[0]))
        with self.assertRaises(ReportError):
            GitHub().query("mutation { bad }")

    def test_upstream_stderr_and_budget_do_not_leak(self):
        with patch.object(
            api_module.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                [], 1, "", "HTTP 403 token=very-private"
            ),
        ):
            with self.assertRaises(ReportError) as error:
                api_module.run_read(["gh", "api", "graphql"])
        self.assertNotIn("very-private", str(error.exception))
        with self.assertRaises(ReportError) as error:
            GitHub(max_requests=0).query("query { viewer { id } }")
        self.assertEqual(error.exception.code, "REQUEST_BUDGET")


class CollectionTests(unittest.TestCase):
    def test_cli_outputs_structured_partial_report_without_checkout_reads(self):
        api = GitHub()
        captured = io.StringIO()
        with (
            patch.object(cli, "GitHub", return_value=api),
            patch.object(cli, "checkout_repository") as checkout,
            patch.object(api, "viewer", return_value=VIEWER),
            patch.object(api, "query", side_effect=ReportError("INACCESSIBLE", "safe")),
            redirect_stdout(captured),
        ):
            code = cli.main(
                [
                    "--repo",
                    "example-org/one,",
                    "example-org/two",
                    "--date",
                    "2026-01-01",
                ]
            )
        checkout.assert_not_called()
        report = json.loads(captured.getvalue())
        self.assertEqual(code, 3)
        self.assertEqual(len(report["repositories"]), 2)
        self.assertEqual(report["coverage"], "partial")

    def test_inaccessible_repo_is_not_no_work_and_exit_is_partial(self):
        api = GitHub()
        with (
            patch.object(api, "viewer", return_value=VIEWER),
            patch.object(api, "query", side_effect=ReportError("INACCESSIBLE", "safe")),
        ):
            report = cli.collect(api, ["example-org/hidden"], INTERVAL)
        self.assertEqual(report["coverage"], "partial")
        self.assertEqual(report["repositories"][0]["coverage"], "inaccessible")

    def test_complete_empty_repo_and_partial_candidates_are_distinct(self):
        for limits in ([], ["RATE_LIMIT"]):
            api = GitHub()
            with (
                patch.object(api, "viewer", return_value=VIEWER),
                patch.object(
                    api,
                    "query",
                    return_value={
                        "repository": {
                            "id": "R1",
                            "nameWithOwner": "example-org/widget",
                        }
                    },
                ),
                patch.object(
                    api, "candidates", return_value={"nodes": [], "limits": limits}
                ),
            ):
                report = cli.collect(api, ["example-org/widget"], INTERVAL)
            self.assertEqual(report["coverage"], "partial" if limits else "complete")
            self.assertEqual(report["repositories"][0]["counts"]["total_items"], 0)

    def test_multiple_prs_linked_to_one_issue_count_once(self):
        issue = item("issue", 7)
        prs = [
            item(number=n, author=VIEWER, createdAt=RECENT, linked_issues_data=[issue])
            for n in (1, 2)
        ]
        report = ProjectFilterTests.collect([issue, *prs], project=None)
        entry = report["repositories"][0]
        self.assertEqual(entry["counts"]["prs"], 2)
        self.assertEqual(entry["counts"]["issues"], 1)
        self.assertEqual(entry["counts"]["total_items"], 3)


class FourSectionTests(unittest.TestCase):
    @staticmethod
    def report(items, project=None):
        return ProjectFilterTests.collect(items, project=project)

    @staticmethod
    def closure(number=7, pr_id="pr_1", **overrides):
        return item(
            "issue",
            number,
            state="CLOSED",
            stateReason="COMPLETED",
            closedAt=RECENT,
            timeline_data=[
                {
                    "__typename": "ClosedEvent",
                    "createdAt": RECENT,
                    "closer": {"__typename": "PullRequest", "id": pr_id},
                }
            ],
            **overrides,
        )

    def test_four_sections_partition_primary_items_and_keep_columns(self):
        closed_pr = item("pr", 1, author=VIEWER, state="MERGED", mergedAt=RECENT)
        closed_issue = item(
            "issue", 2, state="CLOSED", closedAt=RECENT, comments_data=[comment()]
        )
        open_pr = item(number=3, author=VIEWER, createdAt=RECENT, isDraft=True)
        open_issue = item("issue", 4, updatedAt=OLD)
        report = self.report([closed_pr, closed_issue, open_pr, open_issue])
        entry = report["repositories"][0]
        self.assertEqual(report["schema_version"], 3)
        self.assertEqual(
            list(entry["users"][0]["sections"]) + list(entry["backlog"]),
            ["worked_prs", "worked_issues", "open_prs", "open_issues"],
        )
        self.assertEqual(
            [
                s["count"]
                for s in [
                    *entry["users"][0]["sections"].values(),
                    *entry["backlog"].values(),
                ]
            ],
            [1, 1, 1, 1],
        )
        self.assertEqual(entry["counts"]["total_items"], 4)
        self.assertEqual(
            len(
                {
                    r["id"]
                    for s in [
                        *entry["users"][0]["sections"].values(),
                        *entry["backlog"].values(),
                    ]
                    for r in s["rows"]
                }
            ),
            4,
        )
        self.assertEqual(
            len(
                {
                    s["table_markdown"].splitlines()[0]
                    for s in [
                        *entry["users"][0]["sections"].values(),
                        *entry["backlog"].values(),
                    ]
                }
            ),
            1,
        )
        self.assertNotIn("unclassified_rows", entry)
        self.assertNotIn("rows", entry)
        self.assertIn(
            "Draft; In progress", entry["backlog"]["open_prs"]["table_markdown"]
        )
        self.assertNotIn("Triaged:", entry["backlog"]["open_issues"]["table_markdown"])
        self.assertNotIn(
            "In progress", entry["backlog"]["open_issues"]["table_markdown"]
        )
        self.assertIn(OLD, entry["backlog"]["open_issues"]["table_markdown"])

    def test_verified_fixes_count_issues_separately_without_direct_activity(self):
        issues = [self.closure(n) for n in (7, 8)]
        pr = item(
            author=VIEWER, state="MERGED", mergedAt=RECENT, linked_issues_data=issues
        )
        entry = self.report([pr, *issues])["repositories"][0]
        self.assertEqual(entry["users"][0]["sections"]["worked_prs"]["count"], 1)
        self.assertEqual(entry["users"][0]["sections"]["worked_issues"]["count"], 2)
        self.assertEqual(entry["counts"]["total_items"], 3)
        self.assertTrue(
            all(
                r["roles"] == ["resolved through contributed PR"]
                for r in entry["users"][0]["sections"]["worked_issues"]["rows"]
            )
        )

    def test_fix_credit_requires_complete_current_closure_and_contributor(self):
        for alteration in (
            {"coverage": "partial"},
            {"stateReason": "NOT_PLANNED"},
            {"stateReason": "DUPLICATE"},
            {"closedAt": OLD},
            {"timeline_data": []},
            {
                "timeline_data": [
                    {
                        "__typename": "ClosedEvent",
                        "createdAt": RECENT,
                        "closer": {"id": "another"},
                    }
                ]
            },
        ):
            issue = self.closure()
            issue.update(alteration)
            pr = item(
                author=VIEWER,
                state="MERGED",
                mergedAt=RECENT,
                linked_issues_data=[issue],
            )
            with self.subTest(alteration=alteration):
                self.assertFalse(
                    any(
                        r["kind"] == "issue"
                        for r in actor_rows([pr, issue], VIEWER, INTERVAL)
                    )
                )
        issue = self.closure()
        for pr in (
            item(
                state="MERGED",
                mergedAt=RECENT,
                reviews_data=[
                    {"author": VIEWER, "state": "APPROVED", "submittedAt": RECENT}
                ],
                linked_issues_data=[issue],
            ),
            item(
                state="MERGED", author=VIEWER, mergedAt=OLD, linked_issues_data=[issue]
            ),
        ):
            self.assertFalse(
                any(
                    r["kind"] == "issue"
                    for r in actor_rows([pr, issue], VIEWER, INTERVAL)
                )
            )
        pr = item(
            state="MERGED",
            mergedAt=RECENT,
            commits_data=[commit(date=OLD)],
            linked_issues_data=[issue],
        )
        self.assertTrue(
            any(r["kind"] == "issue" for r in actor_rows([pr, issue], VIEWER, INTERVAL))
        )

    def test_reopened_issue_only_open_section_and_closed_unmerged_not_delivered(self):
        issue = self.closure()
        issue.update(state="OPEN", closedAt=None)
        pr = item(
            author=VIEWER,
            createdAt=RECENT,
            state="CLOSED",
            closedAt=RECENT,
            linked_issues_data=[issue],
        )
        rows = actor_rows([pr, issue], VIEWER, INTERVAL)
        self.assertEqual(
            {r["kind"]: r["section"] for r in rows},
            {"issue": "open_issues", "pr": "worked_prs"},
        )
        self.assertIsNone(next(r for r in rows if r["kind"] == "pr")["delivery"])
        self.assertEqual(next(r for r in rows if r["kind"] == "issue")["roles"], [])

    def test_open_discovery_paginates_past_activity_cutoff(self):
        api = GitHub()
        seen = []

        def query(document, variables):
            seen.append(document)
            connection = (
                page([item(number=1, updatedAt=OLD)], total=2, more=True, cursor="next")
                if variables["cursor"] is None
                else page([item(number=2, updatedAt=OLD)], total=2)
            )
            return {"repository": {"pullRequests": connection}}

        with patch.object(api, "query", side_effect=query):
            result = api.candidates("example-org/widget", "pr", None)
        self.assertEqual(len(result["nodes"]), 2)
        self.assertEqual(result["coverage"], "complete")
        self.assertTrue(all("states:[OPEN]" in q for q in seen))

    def test_union_hydrates_each_item_once_and_old_backlog_uses_inventory(self):
        api = GitHub()
        old = item(updatedAt=OLD)
        recent = item("issue", 2)

        def candidates(repo, kind, start):
            return {
                "nodes": [deepcopy(old)]
                if kind == "pr" and start is None
                else [deepcopy(recent)]
                if kind == "issue"
                else [],
                "limits": [],
            }

        with (
            patch.object(api, "viewer", return_value=VIEWER),
            patch.object(
                api,
                "query",
                return_value={
                    "repository": {"id": "R", "nameWithOwner": "example-org/widget"}
                },
            ),
            patch.object(api, "candidates", side_effect=candidates),
            patch.object(api, "enrich", side_effect=lambda row, **kw: row) as enrich,
            patch.object(
                api,
                "repository_context",
                return_value={
                    "folders": [],
                    "evidence": {},
                    "coverage": "complete",
                    "limits": [],
                },
            ),
        ):
            result = cli.collect(api, ["example-org/widget"], INTERVAL)
        self.assertEqual(enrich.call_count, 2)
        self.assertEqual(
            {
                call.args[0]["id"]: call.kwargs["inventory"]
                for call in enrich.call_args_list
            },
            {"pr_1": True, "issue_2": False},
        )
        self.assertEqual(result["repositories"][0]["counts"]["total_items"], 2)

    def test_failed_linked_issue_read_retains_partial_report(self):
        api = GitHub()
        linked = item("issue", 9)
        linked.pop("kind")
        pr = item(author=VIEWER, createdAt=RECENT, linked_issues_data=[linked])
        with (
            patch.object(api, "viewer", return_value=VIEWER),
            patch.object(
                api,
                "query",
                return_value={
                    "repository": {"id": "R", "nameWithOwner": "example-org/widget"}
                },
            ),
            patch.object(
                api,
                "candidates",
                side_effect=lambda repo, kind, start: {
                    "nodes": [pr] if kind == "pr" else [],
                    "limits": [],
                },
            ),
            patch.object(api, "enrich", side_effect=lambda row, **kw: row),
            patch.object(
                api, "issue", side_effect=ReportError("REQUEST_BUDGET", "safe")
            ),
            patch.object(
                api,
                "repository_context",
                return_value={
                    "folders": [],
                    "evidence": {},
                    "coverage": "partial",
                    "limits": ["REQUEST_BUDGET"],
                },
            ),
        ):
            result = cli.collect(api, ["example-org/widget"], INTERVAL)
        entry = result["repositories"][0]
        self.assertEqual(result["coverage"], "partial")
        self.assertEqual(entry["backlog"]["open_prs"]["count"], 1)
        self.assertEqual(entry["backlog"]["open_issues"]["count"], 1)
        self.assertEqual(
            entry["backlog"]["open_issues"]["rows"][0]["coverage"], "inaccessible"
        )

    def test_unknown_closed_item_not_personal_work(self):
        self.assertEqual(
            actor_rows([item(state="CLOSED", closedAt=RECENT)], VIEWER, INTERVAL), []
        )

    def test_inventory_hydration_skips_personal_history_but_keeps_metrics_files(self):
        data = item(updatedAt=OLD)
        api = GitHub()
        with patch.object(
            api,
            "connection",
            return_value={
                "nodes": [],
                "total": 0,
                "coverage": "complete",
                "limits": [],
            },
        ) as connection:
            api.enrich(data, inventory=True)
        self.assertEqual(
            [c.args[1] for c in connection.call_args_list],
            ["files", "closingIssuesReferences"],
        )
        self.assertEqual(data["commits"]["totalCount"], 3)


class FolderAssessmentTests(unittest.TestCase):
    @staticmethod
    def fixture():
        from report_finalization import finalize

        issue = item(
            "issue",
            7,
            title="gateway: heartbeat timeout",
            body="gateway_agent.ha retries block the heartbeat",
        )
        report = FourSectionTests.report([issue], project="services/gateway")
        report["evidence"]["contexts"]["example-org/widget"] = {
            "folders": ["services/gateway", "services/other"],
            "coverage": "complete",
            "limits": [],
            "commit": "a" * 40,
            "evidence": {
                "tree:services/gateway": {
                    "folder": "services/gateway",
                    "path": "services/gateway",
                    "kind": "directory",
                },
                "readme:services/gateway/README.md": {
                    "folder": "services/gateway",
                    "text": "gateway_agent implements gateway HA heartbeat",
                    "kind": "readme",
                },
                "tree:services/other": {
                    "folder": "services/other",
                    "path": "services/other",
                    "kind": "directory",
                },
            },
        }
        assessment = {
            "item_id": "issue_7",
            "folders": ["services/gateway"],
            "evidence_ids": [
                "issue_7:description",
                "readme:services/gateway/README.md",
            ],
            "rationale": "The named package implements gateway heartbeat in this folder.",
        }
        return finalize, report, assessment

    def test_description_assessment_classifies_before_exact_counts(self):
        finalize, report, assessment = self.fixture()
        before = deepcopy(report)
        output = finalize(report, [assessment])
        entry = output["repositories"][0]
        self.assertEqual(report, before)
        self.assertEqual(entry["counts"]["total_items"], 1)
        self.assertEqual(entry["counts"]["uncertain_items"], 0)
        self.assertIn(
            "services/gateway (inferred from description)",
            entry["backlog"]["open_issues"]["table_markdown"],
        )
        self.assertNotIn(
            "Project uncertain", entry["backlog"]["open_issues"]["table_markdown"]
        )

    def test_missing_assessment_stays_inline_and_other_project_is_filtered(self):
        finalize, report, assessment = self.fixture()
        self.assertEqual(
            finalize(report)["repositories"][0]["counts"]["uncertain_items"], 1
        )
        assessment.update(
            folders=["services/other"],
            evidence_ids=["issue_7:title", "tree:services/other"],
        )
        self.assertEqual(
            finalize(report, [assessment])["repositories"][0]["counts"]["total_items"],
            0,
        )
        report["project"] = "services/other/deep"
        self.assertEqual(
            finalize(report, [assessment])["repositories"][0]["counts"][
                "uncertain_items"
            ],
            1,
        )

    def test_assessment_rejects_fabricated_refs_paths_duplicates_and_fact_overrides(
        self,
    ):
        finalize, report, valid = self.fixture()
        for change in (
            {"item_id": "unknown"},
            {"folders": ["services/invented"]},
            {"evidence_ids": ["issue_7:title", "invented"]},
            {"evidence_ids": ["tree:services/gateway"]},
            {"evidence_ids": ["issue_7:title"]},
            {"state": "CLOSED"},
            {"count": 999},
            {"rationale": ""},
            {"folders": ["../gateway"]},
        ):
            with self.subTest(change=change), self.assertRaises(ReportError):
                finalize(report, [{**valid, **change}])
        with self.assertRaises(ReportError):
            finalize(report, [valid, valid])
        with self.assertRaises(ReportError):
            finalize(report, {})

    def test_complete_pr_file_evidence_defeats_prose(self):
        finalize, report, assessment = self.fixture()
        pr = ProjectFilterTests.pr(paths=("services/other/main.py",))
        evidence = build_evidence([pr], INTERVAL)
        report["evidence"].update(
            {key: value for key, value in evidence.items() if key != "contexts"}
        )
        assessment.update(
            item_id="pr_1", evidence_ids=["pr_1:description", "tree:services/gateway"]
        )
        with self.assertRaises(ReportError):
            finalize(report, [assessment])
        self.assertEqual(
            finalize(report)["repositories"][0]["counts"]["total_items"], 0
        )

    def test_inference_does_not_upgrade_incomplete_files_or_context(self):
        finalize, report, assessment = self.fixture()
        pr = ProjectFilterTests.pr(paths=(), coverage="partial")
        evidence = build_evidence([pr], INTERVAL)
        report["evidence"].update(
            {key: value for key, value in evidence.items() if key != "contexts"}
        )
        report["evidence"]["contexts"]["example-org/widget"]["coverage"] = "partial"
        assessment.update(
            item_id="pr_1", evidence_ids=["pr_1:description", "tree:services/gateway"]
        )
        output = finalize(report, [assessment])
        row = output["repositories"][0]["backlog"]["open_prs"]["rows"][0]
        self.assertEqual(row["project_scope"], "matched")
        self.assertEqual(row["coverage"], "partial")
        self.assertEqual(output["coverage"], "partial")

    def test_private_stdin_finalization_and_malformed_input(self):
        _, report, assessment = self.fixture()
        script = Path(__file__).parent / "finalize_report.py"
        for payload, code in (
            ({"report": report, "assessments": [assessment]}, 0),
            ({"report": report, "assessments": [{**assessment, "metrics": {}}]}, 2),
        ):
            result = subprocess.run(
                [sys.executable, "-B", str(script)],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, code)
            data = json.loads(result.stdout)
            if code == 0:
                self.assertEqual(
                    data["repositories"][0]["counts"]["uncertain_items"], 0
                )
            else:
                self.assertNotIn("evidence", data)

    def test_private_stdin_rejects_null_duplicate_and_contradictory_envelopes(self):
        _, report, _ = self.fixture()
        duplicate_items = deepcopy(report)
        duplicate_items["evidence"]["items"].append(
            deepcopy(duplicate_items["evidence"]["items"][0])
        )
        duplicate_repos = deepcopy(report)
        duplicate_repos["repositories"].append(
            deepcopy(duplicate_repos["repositories"][0])
        )
        wrong_section = deepcopy(report)
        wrong_section["evidence"]["items"][0]["section"] = "worked_prs"
        payloads = [{"report": report, "assessments": None}] + [
            {"report": value, "assessments": []}
            for value in (duplicate_items, duplicate_repos, wrong_section)
        ]
        for payload in payloads:
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(Path(__file__).parent / "finalize_report.py"),
                ],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["error"], "INVALID_FINALIZATION")
            self.assertNotIn("Traceback", result.stderr)

    def test_repository_context_pins_commit_bounds_reads_and_marks_truncation(self):
        api = GitHub()
        sha = "a" * 40
        tree = "b" * 40
        replies = [
            {
                "repository": {
                    "defaultBranchRef": {"target": {"oid": sha, "tree": {"oid": tree}}}
                }
            },
            {
                "repository": {
                    "object": {
                        "text": "gateway_agent HA heartbeat",
                        "isTruncated": False,
                    }
                }
            },
        ]
        nodes = [
            {"type": "tree", "path": "services/gateway"},
            {"type": "blob", "path": "services/gateway/README.md"},
        ]
        with (
            patch.object(api, "query", side_effect=replies) as query,
            patch.object(
                api_module,
                "run_read",
                return_value=json.dumps({"tree": nodes, "truncated": True}),
            ) as read,
        ):
            context = api.repository_context(
                "example-org/widget", [], "services/gateway"
            )
        self.assertEqual(context["commit"], sha)
        self.assertEqual(context["coverage"], "partial")
        self.assertIn("services/gateway", context["folders"])
        self.assertEqual(
            query.call_args_list[1].args[1]["expression"],
            sha + ":services/gateway/README.md",
        )
        self.assertIn("--method", read.call_args.args[0])
        self.assertIn("GET", read.call_args.args[0])
        self.assertIn(tree, read.call_args.args[0][-1])

    def test_repository_context_budget_failure_retains_partial_evidence(self):
        api = GitHub(max_requests=0)
        result = api.repository_context("example-org/widget", [], None)
        self.assertEqual(result["coverage"], "partial")
        self.assertIn("REQUEST_BUDGET", result["limits"])


class MultiuserTests(unittest.TestCase):
    REZA = {
        "__typename": "User",
        "id": "U_reza",
        "login": "engineer",
        "name": "Reza Example",
    }
    ALSO = {
        "__typename": "User",
        "id": "U_also",
        "login": "reza-two",
        "name": "Reza Example",
    }
    BOT = {
        "__typename": "Bot",
        "id": "B_bot",
        "login": "automation[bot]",
        "name": "Reza Bot",
    }

    @staticmethod
    def report(items, user=None, project=None, selected=("example-org/widget",)):
        return ProjectFilterTests.collect(
            items, user=user, project=project, selected=selected
        )

    def shared_pr(self):
        return item(
            author=self.REZA,
            createdAt=RECENT,
            state="MERGED",
            mergedAt=RECENT,
            commits_data=[commit(self.ALSO)],
            reviews_data=[
                {"author": VIEWER, "state": "APPROVED", "submittedAt": RECENT}
            ],
        )

    def test_all_users_distinct_roles_and_unique_totals(self):
        report = self.report([self.shared_pr()])
        entry = report["repositories"][0]
        self.assertEqual(
            [g["user"]["login"] for g in entry["users"]],
            ["dev", "engineer", "reza-two"],
        )
        self.assertEqual(
            entry["counts"],
            {"total_items": 1, "prs": 1, "issues": 0, "uncertain_items": 0, "users": 3},
        )
        self.assertEqual(sum(g["completed_items"] for g in entry["users"]), 3)
        roles = {
            g["user"]["id"]: g["sections"]["worked_prs"]["rows"][0]["roles"]
            for g in entry["users"]
        }
        self.assertEqual(roles[VIEWER["id"]], ["reviewed"])
        self.assertIn("authored", roles[self.REZA["id"]])
        self.assertNotIn("contributed commit", roles[self.REZA["id"]])
        for group in entry["users"]:
            row = group["sections"]["worked_prs"]["rows"][0]
            self.assertEqual(
                {a["user_id"] for a in row["attributions"]}, {group["user"]["id"]}
            )
            self.assertEqual(
                {a["user_id"] for a in row["contributions"]}, {group["user"]["id"]}
            )
        self.assertNotIn("sections", entry)
        self.assertNotIn("personal_items", entry["counts"])

    def test_name_login_casefold_substrings_and_null_names(self):
        pr = self.shared_pr()
        for selector in ("REZA", " reza ", "eZa"):
            entry = self.report([pr], user=selector)["repositories"][0]
            self.assertEqual(
                {g["user"]["id"] for g in entry["users"]},
                {self.REZA["id"], self.ALSO["id"]},
            )
        self.assertEqual(self.report([pr], user="re*")["repositories"][0]["users"], [])
        person = {**OTHER, "login": "Straße", "name": None}
        result = self.report([item(author=person, createdAt=RECENT)], user="STRASSE")
        self.assertEqual(result["coverage"], "complete")
        self.assertEqual(result["repositories"][0]["counts"]["users"], 1)

    def test_later_known_names_recover_matching_within_and_across_items(self):
        missing = {**self.REZA}
        missing.pop("name")
        first = item(author=missing, createdAt=RECENT)
        for items in (
            [{**first, "comments_data": [comment(self.REZA)]}],
            [first, item(number=2, author=self.REZA, createdAt=RECENT)],
            [
                item(author=self.REZA, createdAt=RECENT),
                {**first, "id": "pr_2", "number": 2},
            ],
        ):
            report = self.report(items, user="reza")
            user = report["repositories"][0]["users"][0]["user"]
            self.assertEqual(user["id"], self.REZA["id"])
            self.assertEqual(user["name"], self.REZA["name"])
            self.assertTrue(user["name_known"])
            self.assertEqual(report["coverage"], "partial")
        known_null = self.report([item(author=OTHER, createdAt=RECENT)])
        self.assertTrue(known_null["evidence"]["users"][OTHER["id"]]["name_known"])
        self.assertIsNone(known_null["evidence"]["users"][OTHER["id"]]["name"])

    def test_unicode_separators_cannot_add_header_lines(self):
        for char in ("\u2028", "\u2029"):
            with self.assertRaises(ReportError):
                user_selector("re" + char + "za")
            header = report_header(
                "example-org/widget", INTERVAL, "folder" + char + "name", None
            )
            self.assertEqual(len(header.splitlines()), 2)
            self.assertEqual(cell("a" + char + "b"), "a b")

    def test_backlog_is_identical_with_every_filter(self):
        opened = item(
            number=2,
            author=self.REZA,
            createdAt=RECENT,
            isDraft=True,
            comments_data=[comment(VIEWER)],
            commits_data=[commit(self.ALSO)],
        )
        untouched = item("issue", 3, author=self.BOT, updatedAt=OLD)
        baseline = self.report([self.shared_pr(), opened, untouched])["repositories"][0]
        for selector in ("reza", "dev", "no-match"):
            output = self.report([self.shared_pr(), opened, untouched], user=selector)[
                "repositories"
            ][0]
            self.assertEqual(output["backlog"], baseline["backlog"])
        text = baseline["backlog"]["open_prs"]["table_markdown"]
        for login in ("engineer", "dev", "reza-two"):
            self.assertIn("@" + login, text)
        self.assertIn("Draft; In progress", text)
        self.assertNotIn("your", text)

    def test_bots_excluded_but_bot_items_remain_shared(self):
        completed = item(
            author=self.BOT, state="MERGED", mergedAt=RECENT, createdAt=RECENT
        )
        opened = item(number=2, author=self.BOT, createdAt=RECENT)
        entry = self.report([completed, opened])["repositories"][0]
        self.assertEqual(entry["users"], [])
        self.assertEqual(entry["counts"]["total_items"], 1)
        self.assertEqual(entry["backlog"]["open_prs"]["rows"][0]["roles"], [])

    def test_open_only_users_keep_empty_worked_sections(self):
        entry = self.report([item(author=self.REZA, createdAt=RECENT)])["repositories"][
            0
        ]
        group = entry["users"][0]
        self.assertEqual(group["completed_items"], 0)
        self.assertEqual([v["count"] for v in group["sections"].values()], [0, 0])
        self.assertEqual(entry["counts"]["total_items"], 1)

    def test_earlier_contributors_and_user_specific_issue_fix_credit(self):
        issue = FourSectionTests.closure()
        issue["timeline_data"][0]["actor"] = self.BOT
        pr = self.shared_pr()
        pr.update(
            createdAt=OLD,
            commits_data=[commit(self.ALSO, OLD)],
            linked_issues_data=[issue],
        )
        entry = self.report([pr, issue])["repositories"][0]
        groups = {g["user"]["id"]: g for g in entry["users"]}
        self.assertEqual(groups[VIEWER["id"]]["sections"]["worked_issues"]["count"], 0)
        for user in (self.REZA, self.ALSO):
            group = groups[user["id"]]
            self.assertEqual(group["sections"]["worked_issues"]["count"], 1)
            self.assertEqual(
                group["sections"]["worked_prs"]["rows"][0]["delivery"],
                "earlier contribution merged",
            )
        self.assertEqual(entry["counts"]["total_items"], 2)

    def test_no_match_retains_backlog_and_qualifies_missing_identity(self):
        entry = self.report([item(updatedAt=OLD)], user="nobody")["repositories"][0]
        self.assertEqual(entry["empty_users_message"], "No matching contributors")
        self.assertEqual(entry["counts"]["total_items"], 1)
        unmapped = item(
            author=self.BOT,
            commits_data=[
                {
                    "committedDate": RECENT,
                    "authors_data": [
                        {
                            "name": "Reza Example",
                            "email": "person@example.test",
                            "user": None,
                        }
                    ],
                }
            ],
        )
        entry = self.report([unmapped], user="reza")["repositories"][0]
        self.assertEqual(entry["users"], [])
        self.assertEqual(entry["coverage"], "partial")
        self.assertIn("observed", entry["empty_users_message"])
        missing = {**self.REZA}
        missing.pop("name")
        entry = self.report([item(author=missing, createdAt=RECENT)], user="reza")[
            "repositories"
        ][0]
        self.assertEqual(entry["users"], [])
        self.assertEqual(entry["coverage"], "partial")

    def test_folder_filter_precedes_user_groups_and_does_not_match_siblings(self):
        pr = self.shared_pr()
        pr.update(files_data=[{"path": "services/api2/main.py"}], changedFiles=1)
        entry = self.report([pr], user="reza", project="services/api")["repositories"][
            0
        ]
        self.assertEqual(entry["users"], [])
        self.assertEqual(entry["counts"]["total_items"], 0)
        pr.update(files_data=[], files_coverage="partial")
        entry = self.report([pr], user="reza", project="services/api")["repositories"][
            0
        ]
        self.assertEqual(entry["counts"]["users"], 2)
        self.assertEqual(entry["counts"]["uncertain_items"], 1)

    def test_same_user_in_multiple_repositories(self):
        first = item(
            author=self.REZA, createdAt=RECENT, state="CLOSED", closedAt=RECENT
        )
        second = deepcopy(first)
        second.update(id="other_pr", repository={"nameWithOwner": "example-org/other"})
        report = self.report(
            [first, second], selected=("example-org/widget", "example-org/other")
        )
        self.assertEqual([e["counts"]["users"] for e in report["repositories"]], [1, 1])
        self.assertEqual(len(report["evidence"]["users"]), 1)

    def test_invalid_user_inputs_fail_before_reads(self):
        for value in ("", " ", "reza\n", "re\tza", "re\x7fza", "re\x85za"):
            with (
                self.subTest(value=repr(value)),
                patch.object(cli, "GitHub") as api,
                patch.object(cli, "checkout_repository") as checkout,
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(cli.main(["--user", value]), 2)
                api.assert_not_called()
                checkout.assert_not_called()
        with (
            self.assertRaises(SystemExit),
            patch("sys.stderr", new=io.StringIO()),
            patch.object(cli, "GitHub") as api,
        ):
            cli.main(["--user", "reza", "--user", "dev"])
        api.assert_not_called()
        self.assertEqual(user_selector(" reza "), "reza")

    def test_finalizer_rejects_legacy_schema_and_forged_attribution(self):
        from report_finalization import finalize

        report = self.report([self.shared_pr()])
        variants = []
        old = deepcopy(report)
        old["schema_version"] = 2
        variants.append(old)
        foreign = deepcopy(report)
        foreign["evidence"]["items"][0]["attributions"][0]["user_id"] = "not-collected"
        variants.append(foreign)
        repeated = deepcopy(report)
        row = repeated["evidence"]["items"][0]
        row["attributions"].append(deepcopy(row["attributions"][0]))
        variants.append(repeated)
        roles = deepcopy(report)
        roles["evidence"]["items"][0]["attributions"][0]["roles"] = ["invented"]
        variants.append(roles)
        for value in variants:
            with self.assertRaises(ReportError):
                finalize(value)

    def test_header_exact_two_lines_across_daylight_saving(self):
        interval = {
            **INTERVAL,
            "local_start": "2026-10-01T00:00:00-07:00",
            "local_end": "2026-11-03T10:29:59-08:00",
        }
        header = report_header("example-org/widget", interval, "services/api", None)
        self.assertEqual(
            header.splitlines(),
            [
                "User: All users · Repository: example-org/widget · Folder: services/api",
                "Date: 2026-10-01 00:00:00 −07:00 through 2026-11-03 10:29:59 −08:00".replace(
                    "−", "-"
                ),
            ],
        )
        filtered = report_header("example-org/widget", INTERVAL, None, "reza")
        self.assertIn('User: Matching "reza"', filtered)
        self.assertNotIn("Folder:", filtered)
        for removed in (
            "Account:",
            "Personal activity:",
            "Observed:",
            "Timezone:",
            "API collection:",
        ):
            self.assertNotIn(removed, header)

    def test_filtered_parent_keeps_late_linked_read_failure(self):
        issue = item(
            "issue",
            7,
            state="CLOSED",
            stateReason="COMPLETED",
            closedAt=RECENT,
            author=OTHER,
        )
        pr = item(
            author=VIEWER, state="MERGED", mergedAt=RECENT, linked_issues_data=[issue]
        )
        api = GitHub()
        with (
            patch.object(api, "viewer", return_value=VIEWER),
            patch.object(
                api,
                "query",
                return_value={
                    "repository": {
                        "id": "R_widget",
                        "nameWithOwner": "example-org/widget",
                    }
                },
            ),
            patch.object(
                api,
                "candidates",
                side_effect=lambda repo, kind, start: {
                    "nodes": [deepcopy(pr)] if kind == "pr" else [],
                    "limits": [],
                },
            ),
            patch.object(api, "enrich", side_effect=lambda row, **kw: row),
            patch.object(
                api, "issue", side_effect=ReportError("REQUEST_BUDGET", "safe")
            ),
        ):
            result = cli.collect(api, ["example-org/widget"], INTERVAL, user="reza")
        entry = result["repositories"][0]
        self.assertEqual(result["coverage"], "partial")
        self.assertEqual(entry["users"], [])
        self.assertEqual(entry["counts"]["total_items"], 0)
        self.assertIn("observed", entry["empty_users_message"])
        self.assertTrue(any("linked issue" in limit for limit in entry["limits"]))

    def test_multiuser_private_stdin_preserves_groups_and_backlog(self):
        report = self.report([self.shared_pr(), item("issue", 3)], user="reza")
        result = subprocess.run(
            [sys.executable, "-B", str(Path(__file__).with_name("finalize_report.py"))],
            input=json.dumps({"report": report, "assessments": []}),
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["repositories"], report["repositories"])
        self.assertEqual(len(output["repositories"][0]["users"]), 2)

    def test_more_users_never_multiply_collection_calls(self):
        def collect_with(data):
            api = GitHub()
            with (
                patch.object(api, "viewer", return_value=VIEWER),
                patch.object(
                    api,
                    "query",
                    return_value={
                        "repository": {
                            "id": "R_widget",
                            "nameWithOwner": "example-org/widget",
                        }
                    },
                ) as identity,
                patch.object(
                    api,
                    "candidates",
                    side_effect=lambda repo, kind, start: {
                        "nodes": [deepcopy(data)] if kind == "pr" else [],
                        "limits": [],
                    },
                ) as discovery,
                patch.object(
                    api, "enrich", side_effect=lambda row, **kw: row
                ) as enrich,
            ):
                result = cli.collect(api, ["example-org/widget"], INTERVAL)
                return result, (
                    identity.call_count,
                    discovery.call_count,
                    enrich.call_count,
                )

        single, first = collect_with(item(author=self.REZA, createdAt=RECENT))
        multiple, second = collect_with(self.shared_pr())
        self.assertEqual(first, (1, 4, 1))
        self.assertEqual(first, second)
        self.assertEqual(single["repositories"][0]["counts"]["users"], 1)
        self.assertEqual(multiple["repositories"][0]["counts"]["users"], 3)

    def test_actor_and_commit_queries_request_profile_identity(self):
        self.assertIn("name", api_module.ACTOR)
        api = GitHub()
        with patch.object(
            api,
            "connection",
            return_value={
                "nodes": [],
                "total": 0,
                "coverage": "complete",
                "limits": [],
            },
        ) as connection:
            api.enrich(item())
        commit_query = next(
            c.args[2] for c in connection.call_args_list if c.args[1] == "commits"
        )
        self.assertIn("user { __typename id login name }", commit_query)
        self.assertNotIn("nodes { name user", commit_query)


if __name__ == "__main__":
    unittest.main()
