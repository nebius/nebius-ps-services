#!/usr/bin/env python3
"""Synthetic LOC mode and shared-selector regression checks; no network."""

import copy
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
import io
import json
import sys
import unittest
from unittest.mock import Mock, patch

sys.dont_write_bytecode = True
import github_report as cli  # noqa: E402
from github_api import GitHub  # noqa: E402
from loc_modifications_report import collect_loc, owner_selector, top_selector  # noqa: E402
from report_model import ReportError, window  # noqa: E402
from report_finalization import finalize  # noqa: E402

INTERVAL = window("2026-01-01", datetime(2026, 4, 1, tzinfo=timezone.utc))
VIEWER = {"id": "viewer", "databaseId": 5, "login": "reader"}


def pr(number, login="alpha", additions=10, deletions=2, repo="org/one", **changes):
    value = {
        "id": f"{repo}:{number}",
        "number": number,
        "url": f"https://github.com/{repo}/pull/{number}",
        "repository": {"id": repo, "nameWithOwner": repo},
        "mergedAt": "2026-03-01T00:00:00Z",
        "updatedAt": "2026-03-01T00:00:00Z",
        "author": {
            "__typename": "User",
            "id": login,
            "databaseId": 10,
            "login": login,
            "name": None,
        },
        "additions": additions,
        "deletions": deletions,
    }
    value.update(changes)
    return value


def found(nodes, limits=None):
    return {"nodes": nodes, "limits": limits or [], "total": len(nodes)}


def fake_api(*streams):
    api = Mock(spec=GitHub)
    api.viewer.return_value = VIEWER
    api.merged_prs.side_effect = streams
    return api


def connection(nodes, total=None, more=False, cursor=None):
    return {
        "nodes": nodes,
        "totalCount": len(nodes) if total is None else total,
        "pageInfo": {"hasNextPage": more, "endCursor": cursor},
    }


class InputTests(unittest.TestCase):
    def test_owner_failure_survives_real_activity_finalization(self):
        api = fake_api()
        api.owner_repositories.return_value = {
            "owner": {"login": "org"},
            "nodes": [],
            "limits": ["TIMEOUT"],
            "coverage": "partial",
        }
        with (
            patch.object(cli, "GitHub", return_value=api),
            redirect_stdout(io.StringIO()) as out,
        ):
            self.assertEqual(cli.main(["--owner", "org"]), 3)
        report = json.loads(out.getvalue())
        self.assertEqual(finalize(report, [])["coverage"], "partial")
        report["owner_inventory"]["limits"] = []
        self.assertEqual(finalize(report, [])["coverage"], "partial")
        report["owner_inventory"]["coverage"] = "complete"
        self.assertEqual(finalize(report, [])["coverage"], "complete")
        report["owner_inventory"] = None
        with self.assertRaises(ReportError):
            finalize(report, [])

    def test_selectors(self):
        self.assertEqual(owner_selector("Example-Org"), "Example-Org")
        self.assertEqual(top_selector("3"), 3)
        self.assertIsNone(top_selector(None))
        for invalid in (
            "",
            "-org",
            "org-",
            "org/repo",
            "x\n",
            "$(id)",
            "https://github.com/org",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ReportError):
                owner_selector(invalid)
        for invalid in ("0", "-1", "1.2", "all", "01", "1\n"):
            with self.subTest(invalid=invalid), self.assertRaises(ReportError):
                top_selector(invalid)

    def test_invalid_combinations_fail_before_tools(self):
        cases = [
            ["--owner", "org", "--repo", "org/one"],
            ["--top", "3"],
            ["--loc-modifications-report", "--project", "src"],
            ["--loc-modifications-report", "--top", "0"],
            ["--owner", "bad/owner"],
        ]
        with (
            patch.object(cli, "GitHub") as api,
            patch.object(cli, "checkout_repository") as checkout,
        ):
            for args in cases:
                with self.subTest(args=args), redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(args), 2)
            api.assert_not_called()
            checkout.assert_not_called()

    def test_repeated_flags_and_abbreviations(self):
        for args in (
            ["--loc-modifications-report"] * 2,
            ["--owner", "a", "--owner", "b"],
            ["--top", "2", "--top", "3"],
            ["--loc"],
        ):
            with (
                self.subTest(args=args),
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                cli.parser().parse_args(args)

    def test_default_and_explicit_repo_keep_normal_dispatch(self):
        for args, expected in (
            ([], ["org/current"]),
            (["--repo", "org/explicit"], ["org/explicit"]),
        ):
            with (
                patch.object(cli, "GitHub"),
                patch.object(cli, "checkout_repository", return_value="org/current"),
                patch.object(
                    cli, "collect", return_value={"coverage": "complete"}
                ) as collect,
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(cli.main(args), 0)
                self.assertEqual(collect.call_args.args[1], expected)

    def test_owner_normal_mode_keeps_finalization_and_partial_exit(self):
        api = fake_api()
        api.owner_repositories.return_value = {
            "owner": {"login": "org"},
            "nodes": [{"nameWithOwner": "org/one"}],
            "limits": ["TIMEOUT"],
        }
        with (
            patch.object(cli, "GitHub", return_value=api),
            patch.object(cli, "checkout_repository") as checkout,
            patch.object(
                cli, "collect", return_value={"coverage": "complete", "limitations": []}
            ) as collect,
            redirect_stdout(io.StringIO()) as out,
        ):
            self.assertEqual(cli.main(["--owner", "org", "--project", "src"]), 3)
        checkout.assert_not_called()
        self.assertEqual(collect.call_args.args[3], 5)
        self.assertEqual(collect.call_args.kwargs["project"], "src")
        self.assertEqual(json.loads(out.getvalue())["coverage"], "partial")

    def test_loc_cli_dispatch_and_no_checkout_for_empty_owner(self):
        api = fake_api()
        api.owner_repositories.return_value = {
            "owner": {"login": "org"},
            "nodes": [],
            "limits": [],
        }
        with (
            patch.object(cli, "GitHub", return_value=api),
            patch.object(cli, "checkout_repository") as checkout,
            redirect_stdout(io.StringIO()) as out,
        ):
            self.assertEqual(
                cli.main(
                    ["--loc-modifications-report", "--owner", "org", "--top", "3"]
                ),
                0,
            )
        checkout.assert_not_called()
        self.assertEqual(json.loads(out.getvalue())["mode"], "loc-modifications-report")
        api.viewer.assert_called_with(5)


class AggregationTests(unittest.TestCase):
    def test_repository_identity_is_bound_in_both_directions_and_to_inventory(self):
        original, replaced = pr(1), pr(2)
        replaced["repository"]["id"] = "replacement"
        report = collect_loc(
            fake_api(found([original, replaced])), ["org/one"], INTERVAL
        )
        self.assertEqual(report["coverage"], "partial")
        self.assertEqual(report["rows"][0]["merged_prs"], 1)
        inventory = {
            "owner": {"login": "org"},
            "nodes": [{"nameWithOwner": "org/one", "id": "original"}],
            "limits": [],
        }
        report = collect_loc(
            fake_api(found([replaced])), ["org/one"], INTERVAL, inventory=inventory
        )
        self.assertEqual(report["rows"], [])
        self.assertEqual(report["coverage"], "partial")

    def test_across_repositories_before_top_and_single_metric_column(self):
        api = fake_api(
            found([pr(1, "alpha", 50, 20), pr(2, "beta", 60, 20)]),
            found([pr(1, "alpha", 30, 0, repo="org/two")]),
        )
        report = collect_loc(api, ["org/one", "org/two"], INTERVAL, top=1)
        self.assertEqual(report["rows"][0]["loc_modifications"], 100)
        self.assertEqual(report["rows"][0]["merged_prs"], 2)
        self.assertEqual(report["matching_accounts"], 2)
        self.assertIn("| LOC Modifications |", report["markdown"])
        self.assertNotIn("| Additions |", report["markdown"])

    def test_zero_diff_is_real_and_bots_do_not_rank(self):
        bot = pr(2, additions=9999, author={"__typename": "Bot", "login": "automation"})
        report = collect_loc(
            fake_api(found([pr(1, additions=0, deletions=0), bot])),
            ["org/one"],
            INTERVAL,
        )
        self.assertEqual(report["rows"][0]["loc_modifications"], 0)
        self.assertEqual(report["rows"][0]["merged_prs"], 1)
        self.assertTrue(report["rows"][0]["metrics_complete"])
        self.assertEqual(report["excluded_bot_prs"], 1)

    def test_missing_metrics_lower_bound_and_wholly_unknown(self):
        report = collect_loc(
            fake_api(
                found([pr(1), pr(2, additions=None), pr(3, "beta", additions=True)])
            ),
            ["org/one"],
            INTERVAL,
        )
        self.assertEqual(report["coverage"], "partial")
        self.assertIn("≥ 12", report["markdown"])
        self.assertIn("unknown", report["markdown"])
        self.assertIn("provisional ranking among observed accounts", report["markdown"])

    def test_user_filter_before_top_and_equal_ranks(self):
        a, b, c = pr(1, "alpha"), pr(2, "beta"), pr(3, "charlie", 500)
        a["author"]["name"] = b["author"]["name"] = "Shared Example"
        report = collect_loc(
            fake_api(found([c, b, a])), ["org/one"], INTERVAL, user="shared", top=1
        )
        self.assertEqual(report["rows"][0]["login"], "alpha")
        self.assertTrue(report["cutoff_tie"])
        all_rows = collect_loc(fake_api(found([b, a])), ["org/one"], INTERVAL)["rows"]
        self.assertEqual([x["rank"] for x in all_rows], [1, 1])

    def test_null_and_missing_names_are_distinct(self):
        a, b = pr(1), pr(2, "beta")
        del b["author"]["name"]
        report = collect_loc(
            fake_api(found([a, b])), ["org/one"], INTERVAL, user="missing"
        )
        self.assertEqual(report["rows"], [])
        self.assertEqual(report["coverage"], "partial")
        self.assertEqual(
            collect_loc(fake_api(found([a])), ["org/one"], INTERVAL)["coverage"],
            "complete",
        )

    def test_duplicate_and_unknown_authors_cannot_disappear(self):
        a = pr(1)
        report = collect_loc(
            fake_api(found([a, copy.deepcopy(a), pr(2, author=None)])),
            ["org/one"],
            INTERVAL,
        )
        self.assertEqual(report["rows"][0]["merged_prs"], 1)
        self.assertEqual(report["unknown_author_prs"], 1)
        self.assertEqual(report["coverage"], "partial")

    def test_exact_interval_not_creation_or_update_time(self):
        old = pr(1, mergedAt="2025-12-31T00:00:00Z", updatedAt="2026-03-01T00:00:00Z")
        end = pr(2, mergedAt=INTERVAL["end"])
        future = pr(3, mergedAt="2026-04-02T00:00:00Z")
        report = collect_loc(fake_api(found([old, end, future])), ["org/one"], INTERVAL)
        self.assertEqual(report["merged_prs_observed"], 1)

    def test_scope_and_inaccessible_repository_qualify_ranking(self):
        api = fake_api(
            found([pr(1, repo="other/repo")]), ReportError("INACCESSIBLE", "safe")
        )
        report = collect_loc(api, ["org/one", "org/two"], INTERVAL)
        self.assertEqual(report["rows"], [])
        self.assertEqual(report["coverage"], "partial")

    def test_markdown_name_is_escaped_and_urls_are_constructed(self):
        value = pr(1)
        value["author"]["name"] = "<script>|[bad](https://evil.test)"
        report = collect_loc(fake_api(found([value])), ["org/one"], INTERVAL)
        self.assertNotIn("<script>", report["markdown"])
        self.assertIn("https://github.com/alpha", report["markdown"])


class ApiTests(unittest.TestCase):
    def test_owner_enumeration_pages_owned_forks_archives_and_private(self):
        def node(n):
            return {
                "id": str(n),
                "nameWithOwner": f"org/r{n}",
                "owner": {"id": "O"},
                "isFork": True,
                "isArchived": True,
                "isPrivate": True,
            }

        api = GitHub()
        api.query = Mock(
            side_effect=[
                {
                    "repositoryOwner": {
                        "id": "O",
                        "login": "org",
                        "__typename": "Organization",
                    }
                },
                {
                    "repositoryOwner": {
                        "repositories": connection([node(1)], 2, True, "next")
                    }
                },
                {"repositoryOwner": {"repositories": connection([node(2)], 2)}},
            ]
        )
        result = api.owner_repositories("org")
        self.assertEqual(len(result["nodes"]), 2)
        self.assertEqual(result["coverage"], "complete")
        self.assertIn("ownerAffiliations:[OWNER]", api.query.call_args.args[0])
        self.assertNotIn("isFork:", api.query.call_args.args[0])

    def test_unknown_owner_is_not_empty(self):
        api = GitHub()
        api.query = Mock(return_value={"repositoryOwner": None})
        with self.assertRaises(ReportError):
            api.owner_repositories("org")

    def test_user_owner_rejects_affiliated_repo_and_preserves_partial_inventory(self):
        api = GitHub()
        api.query = Mock(
            side_effect=[
                {
                    "repositoryOwner": {
                        "id": "U",
                        "login": "person",
                        "__typename": "User",
                    }
                },
                {
                    "repositoryOwner": {
                        "repositories": connection(
                            [
                                {
                                    "id": "R",
                                    "nameWithOwner": "person/repo",
                                    "owner": {"id": "OTHER"},
                                },
                                None,
                            ],
                            2,
                            True,
                            "next",
                        )
                    }
                },
                ReportError("TIMEOUT", "safe"),
            ]
        )
        result = api.owner_repositories("person")
        self.assertEqual(result["nodes"], [])
        self.assertEqual(result["coverage"], "partial")

    def test_moving_pr_is_reconciled_and_flagged(self):
        old, moved = pr(1), pr(2, updatedAt="2026-04-01T01:00:00Z")
        api = GitHub()
        api.query = Mock(
            side_effect=[
                {"repository": {"pullRequests": connection([old], 1)}},
                {"repository": {"pullRequests": connection([moved, old], 2)}},
            ]
        )
        result = api.merged_prs("org/one", INTERVAL["start"], INTERVAL["end"])
        self.assertEqual(len(result["nodes"]), 2)
        self.assertEqual(result["coverage"], "partial")

    def test_old_merge_recent_update_does_not_stop_discovery(self):
        old = pr(1, mergedAt="2025-12-01T00:00:00Z", updatedAt="2026-03-15T00:00:00Z")
        api = GitHub()
        data = {"repository": {"pullRequests": connection([old, pr(2)], 2)}}
        api.query = Mock(side_effect=[data, data])
        result = api.merged_prs("org/one", INTERVAL["start"], INTERVAL["end"])
        self.assertEqual(len(result["nodes"]), 2)
        self.assertEqual(result["coverage"], "complete")

    def test_bad_order_null_nodes_and_repeated_cursor_are_partial(self):
        cases = [
            [pr(1), pr(2, updatedAt="2026-03-02T00:00:00Z")],
            [None],
            [pr(1, updatedAt="2026-02-01T00:00:00Z")],
        ]
        for nodes in cases:
            api = GitHub()
            api.query = Mock(
                return_value={"repository": {"pullRequests": connection(nodes)}}
            )
            with self.subTest(nodes=nodes):
                self.assertEqual(
                    api.merged_prs("org/one", INTERVAL["start"], INTERVAL["end"])[
                        "coverage"
                    ],
                    "partial",
                )
        api = GitHub()
        api.query = Mock(
            return_value={
                "repository": {"pullRequests": connection([pr(1)], 3, True, "same")}
            }
        )
        self.assertEqual(
            api.merged_prs("org/one", INTERVAL["start"], INTERVAL["end"])["coverage"],
            "partial",
        )

    def test_rate_limit_does_not_retry(self):
        api = GitHub()
        api.query = Mock(side_effect=ReportError("RATE_LIMIT", "safe"))
        self.assertEqual(
            api.merged_prs("org/one", INTERVAL["start"], INTERVAL["end"])["coverage"],
            "partial",
        )
        self.assertEqual(api.query.call_count, 1)


if __name__ == "__main__":
    unittest.main()
