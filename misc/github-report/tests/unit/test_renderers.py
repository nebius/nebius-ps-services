from __future__ import annotations

from datetime import UTC, datetime

from github_report.models import (
    LocLanguageRow,
    LocReport,
    LocReportMetadata,
    RepoContributorRow,
    ReportBundle,
    ReportMetadata,
    UserContributorRow,
)
from github_report.renderers import render_loc_report, render_repo_breakdown, render_top_users
from github_report.settings import OutputFormat, SortBy, WindowKind


def build_report_bundle() -> ReportBundle:
    metadata = ReportMetadata(
        owner="acme",
        generated_at=datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC),
        since=datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC),
        until=datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC),
        repos_requested=2,
        repos_scanned=2,
        commits_scanned=17,
        include_bots=False,
        branch_scope="repository default branch only",
        sort_by=SortBy.modifications,
        window_kind=WindowKind.custom,
    )
    return ReportBundle(
        metadata=metadata,
        top_users=[
            UserContributorRow(
                "alice",
                "Alice Example (@alice)",
                num_commits=7,
                num_modifications=70,
                repo_count=2,
                repos=("acme/gosdk", "acme/pysdk"),
            ),
            UserContributorRow(
                "bob",
                "Bob Example (@bob)",
                num_commits=3,
                num_modifications=20,
                repo_count=1,
                repos=("acme/pysdk",),
            ),
        ],
        repo_rows=[
            RepoContributorRow(
                "alice",
                "Alice Example (@alice)",
                "acme/pysdk",
                num_commits=4,
                num_modifications=40,
            ),
            RepoContributorRow(
                "alice",
                "Alice Example (@alice)",
                "acme/gosdk",
                num_commits=3,
                num_modifications=30,
            ),
        ],
    )


def build_loc_report() -> LocReport:
    return LocReport(
        metadata=LocReportMetadata(
            owner="nebius",
            repo="nebius-ps-services",
            ref="main",
            path="services/nebius-cxcli",
            generated_at=datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC),
            files_counted=3,
            files_skipped=1,
            branch_scope="main archive",
        ),
        language_rows=[
            LocLanguageRow(
                language="Python",
                file_count=2,
                code_lines=80,
                comment_lines=10,
                blank_lines=20,
                total_lines=110,
            ),
            LocLanguageRow(
                language="YAML",
                file_count=1,
                code_lines=25,
                comment_lines=5,
                blank_lines=4,
                total_lines=34,
            ),
        ],
    )


def test_render_top_users_markdown_contains_expected_headers() -> None:
    output = render_top_users(build_report_bundle(), limit=2, output_format=OutputFormat.markdown)

    assert "# Top Contributors for acme" in output
    assert "- Window: `custom window (2026-03-01T00:00:00Z to 2026-03-13T12:00:00Z)`" in output
    assert "- Ranking metric: `modifications first, then commits`" in output
    assert "| rank | user_name | num_modifications | num_commits | repos |" in output
    assert "| 1 | Alice Example (@alice) | 70 | 7 | acme/gosdk, acme/pysdk |" in output


def test_render_repo_breakdown_csv_has_expected_columns() -> None:
    output = render_repo_breakdown(build_report_bundle(), limit=2, output_format=OutputFormat.csv)

    assert output.splitlines()[0] == "rank,user_name,repo_name,num_modifications,num_commits"
    assert "1,Alice Example (@alice),acme/pysdk,40,4" in output


def test_render_top_users_text_is_pretty_and_copy_paste_friendly() -> None:
    output = render_top_users(build_report_bundle(), limit=2, output_format=OutputFormat.text)

    assert "Top Contributors for acme" in output
    assert "Generated at" in output
    assert "2026-03-13T12:00:00Z" in output
    assert "Contributors" in output
    assert " 1. Alice Example (@alice)" in output
    assert "    Modifications : 70" in output
    assert "    Commits       : 7" in output
    assert "    Repos         : acme/gosdk, acme/pysdk" in output


def test_render_top_users_markdown_reflects_commit_first_ranking() -> None:
    report = build_report_bundle()
    report.metadata.sort_by = SortBy.commits

    output = render_top_users(report, limit=2, output_format=OutputFormat.markdown)

    assert "- Ranking metric: `commits first, then modifications`" in output


def test_render_top_users_markdown_reflects_relative_days_window() -> None:
    report = build_report_bundle()
    report.metadata.window_kind = WindowKind.relative_days
    report.metadata.lookback_days = 60

    output = render_top_users(report, limit=2, output_format=OutputFormat.markdown)

    assert "- Window: `last 60 days (2026-03-01T00:00:00Z to 2026-03-13T12:00:00Z)`" in output


def test_render_top_users_html_contains_table_markup() -> None:
    output = render_top_users(build_report_bundle(), limit=2, output_format=OutputFormat.html)

    assert "<!DOCTYPE html>" in output
    assert "<h1>Top Contributors for acme</h1>" in output
    assert '<th class="num">rank</th>' in output
    assert '<td class="text">Alice Example (@alice)</td>' in output
    assert '<td class="text">acme/gosdk, acme/pysdk</td>' in output


def test_render_loc_report_markdown_contains_totals_and_scope() -> None:
    output = render_loc_report(build_loc_report(), output_format=OutputFormat.markdown)

    assert "# Lines of Code for nebius/nebius-ps-services" in output
    assert "- Ref: `main`" in output
    assert "- Scope: `services/nebius-cxcli`" in output
    assert "- Code lines: `105`" in output
    assert "| language | files | code_lines | comment_lines | blank_lines | total_lines |" in output
    assert "| Python | 2 | 80 | 10 | 20 | 110 |" in output


def test_render_loc_report_csv_has_expected_columns() -> None:
    output = render_loc_report(build_loc_report(), output_format=OutputFormat.csv)

    assert output.splitlines()[0] == (
        "language,files,code_lines,comment_lines,blank_lines,total_lines"
    )
    assert "Python,2,80,10,20,110" in output
    assert "Total,3,105,15,24,144" in output
