from __future__ import annotations

import re
from contextlib import nullcontext
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from github_report.cli import app
from github_report.models import (
    LocLanguageRow,
    LocReport,
    LocReportMetadata,
    RepoContributorRow,
    ReportBundle,
    ReportMetadata,
    RepositoryRef,
    UserContributorRow,
)
from github_report.settings import SortBy, WindowKind

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _normalize_help_output(text: str) -> str:
    return " ".join(ANSI_ESCAPE_RE.sub("", text).split())


def sample_report() -> ReportBundle:
    return ReportBundle(
        metadata=ReportMetadata(
            owner="acme",
            generated_at=datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC),
            since=datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC),
            until=datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC),
            repos_requested=1,
            repos_scanned=1,
            commits_scanned=12,
            include_bots=False,
            branch_scope="repository default branch only",
            sort_by=SortBy.modifications,
            window_kind=WindowKind.relative_days,
            lookback_days=30,
        ),
        top_users=[
            UserContributorRow(
                "alice",
                "Alice Example (@alice)",
                num_commits=5,
                num_modifications=15,
                repo_count=1,
                repos=("acme/pysdk",),
            )
        ],
        repo_rows=[
            RepoContributorRow(
                "alice",
                "Alice Example (@alice)",
                "acme/pysdk",
                num_commits=5,
                num_modifications=15,
            )
        ],
    )


def sample_loc_report() -> LocReport:
    return LocReport(
        metadata=LocReportMetadata(
            owner="nebius",
            repo="nebius-ps-services",
            ref="main",
            path="services/nebius-cxcli",
            generated_at=datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC),
            files_counted=2,
            files_skipped=1,
            branch_scope="main archive",
        ),
        language_rows=[
            LocLanguageRow(
                language="Python",
                file_count=2,
                code_lines=42,
                comment_lines=3,
                blank_lines=7,
                total_lines=52,
            )
        ],
    )


def test_top_users_command_renders_csv() -> None:
    runner = CliRunner()
    with patch(
        "github_report.cli.service.build_report", new=AsyncMock(return_value=sample_report())
    ):
        result = runner.invoke(
            app,
            ["top-users", "--owner", "acme", "--since", "2026-03-01", "--format", "csv"],
        )

    assert result.exit_code == 0
    assert "rank,user_name,num_modifications,num_commits,repos" in result.stdout
    assert "1,Alice Example (@alice),15,5,acme/pysdk" in result.stdout


def test_top_users_per_repo_renders_repo_breakdown_csv() -> None:
    runner = CliRunner()
    with patch(
        "github_report.cli.service.build_report", new=AsyncMock(return_value=sample_report())
    ):
        result = runner.invoke(
            app,
            [
                "top-users",
                "--owner",
                "acme",
                "--per-repo",
                "--since",
                "2026-03-01",
                "--format",
                "csv",
            ],
        )

    assert result.exit_code == 0
    assert "rank,user_name,repo_name,num_modifications,num_commits" in result.stdout
    assert "1,Alice Example (@alice),acme/pysdk,15,5" in result.stdout


def test_root_owner_option_flows_into_subcommands() -> None:
    runner = CliRunner()
    mock_build_report = AsyncMock(return_value=sample_report())
    with patch("github_report.cli.service.build_report", new=mock_build_report):
        result = runner.invoke(
            app,
            ["--owner", "acme", "top-users", "--since", "2026-03-01", "--format", "csv"],
        )

    assert result.exit_code == 0
    assert mock_build_report.await_args.args[0].owner == "acme"


def test_top_users_requires_owner() -> None:
    result = CliRunner().invoke(app, ["top-users", "--since", "2026-03-01", "--format", "csv"])

    assert result.exit_code == 2
    assert "Missing required option '--owner'" in result.stderr


def test_top_users_command_passes_excluded_repositories() -> None:
    runner = CliRunner()
    mock_build_report = AsyncMock(return_value=sample_report())
    with patch("github_report.cli.service.build_report", new=mock_build_report):
        result = runner.invoke(
            app,
            [
                "top-users",
                "--owner",
                "acme",
                "--since",
                "2026-03-01",
                "--exclude",
                "csa-soperator-deployments, acme/api",
                "--format",
                "csv",
            ],
        )

    assert result.exit_code == 0
    assert mock_build_report.await_args.args[0].exclude_repos == (
        "acme/csa-soperator-deployments",
        "acme/api",
    )


def test_top_users_command_enters_spinner_context() -> None:
    runner = CliRunner()
    with (
        patch("github_report.cli._status_context", return_value=nullcontext()) as mock_status,
        patch(
            "github_report.cli.service.build_report", new=AsyncMock(return_value=sample_report())
        ),
    ):
        result = runner.invoke(
            app,
            ["top-users", "--owner", "acme", "--since", "2026-03-01", "--format", "csv"],
        )

    assert result.exit_code == 0
    assert mock_status.call_count == 1
    assert (
        mock_status.call_args.args[0]
        == "Collecting GitHub activity for acme (all accessible repos)..."
    )


def test_top_users_spinner_mentions_excluded_repositories() -> None:
    runner = CliRunner()
    with (
        patch("github_report.cli._status_context", return_value=nullcontext()) as mock_status,
        patch(
            "github_report.cli.service.build_report", new=AsyncMock(return_value=sample_report())
        ),
    ):
        result = runner.invoke(
            app,
            [
                "top-users",
                "--owner",
                "acme",
                "--since",
                "2026-03-01",
                "--exclude",
                "csa-soperator-deployments",
                "--format",
                "csv",
            ],
        )

    assert result.exit_code == 0
    assert (
        mock_status.call_args.args[0]
        == "Collecting GitHub activity for acme (all accessible repos, excluding 1 repo)..."
    )


def test_top_users_output_csv_infers_format_from_extension(tmp_path) -> None:
    runner = CliRunner()
    output_path = tmp_path / "report.csv"
    with patch(
        "github_report.cli.service.build_report", new=AsyncMock(return_value=sample_report())
    ):
        result = runner.invoke(
            app,
            ["top-users", "--owner", "acme", "--since", "2026-03-01", "--output", str(output_path)],
        )

    assert result.exit_code == 0
    assert "Wrote" in result.stderr
    assert output_path.name in result.stderr
    assert result.stdout == ""
    assert output_path.read_text(encoding="utf-8").startswith(
        "rank,user_name,num_modifications,num_commits,repos"
    )


def test_list_repos_output_csv_infers_format_from_extension(tmp_path) -> None:
    runner = CliRunner()
    output_path = tmp_path / "repos.csv"
    repositories = [
        RepositoryRef(
            name="pysdk",
            full_name="acme/pysdk",
            default_branch="main",
            is_archived=False,
        ),
    ]
    with patch("github_report.cli.service.list_repositories", return_value=repositories):
        result = runner.invoke(app, ["list-repos", "--owner", "acme", "--output", str(output_path)])

    assert result.exit_code == 0
    assert output_path.read_text(encoding="utf-8").startswith(
        "repo_name,visibility,default_branch,archived"
    )


def test_top_users_output_html_infers_format_from_extension(tmp_path) -> None:
    runner = CliRunner()
    output_path = tmp_path / "report.html"
    with patch(
        "github_report.cli.service.build_report", new=AsyncMock(return_value=sample_report())
    ):
        result = runner.invoke(
            app,
            ["top-users", "--owner", "acme", "--since", "2026-03-01", "--output", str(output_path)],
        )

    assert result.exit_code == 0
    html = output_path.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html
    assert "<h1>Top Contributors for acme</h1>" in html


def test_top_users_output_text_infers_format_from_extension(tmp_path) -> None:
    runner = CliRunner()
    output_path = tmp_path / "report.txt"
    with patch(
        "github_report.cli.service.build_report", new=AsyncMock(return_value=sample_report())
    ):
        result = runner.invoke(
            app,
            ["top-users", "--owner", "acme", "--since", "2026-03-01", "--output", str(output_path)],
        )

    assert result.exit_code == 0
    text_output = output_path.read_text(encoding="utf-8")
    assert "Top Contributors for acme" in text_output
    assert "Contributors" in text_output
    assert " 1. Alice Example (@alice)" in text_output
    assert "    Modifications : 15" in text_output


def test_top_users_output_overwrites_existing_file(tmp_path) -> None:
    runner = CliRunner()
    output_path = tmp_path / "report.txt"
    output_path.write_text("stale content\n", encoding="utf-8")
    with patch(
        "github_report.cli.service.build_report", new=AsyncMock(return_value=sample_report())
    ):
        result = runner.invoke(
            app,
            ["top-users", "--owner", "acme", "--since", "2026-03-01", "--output", str(output_path)],
        )

    assert result.exit_code == 0
    text_output = output_path.read_text(encoding="utf-8")
    assert "stale content" not in text_output
    assert text_output.startswith("Top Contributors for acme")


def test_top_users_explicit_format_overrides_output_extension(tmp_path) -> None:
    runner = CliRunner()
    output_path = tmp_path / "report.txt"
    with patch(
        "github_report.cli.service.build_report", new=AsyncMock(return_value=sample_report())
    ):
        result = runner.invoke(
            app,
            [
                "top-users",
                "--owner",
                "acme",
                "--since",
                "2026-03-01",
                "--format",
                "markdown",
                "--output",
                str(output_path),
            ],
        )

    assert result.exit_code == 0
    markdown_output = output_path.read_text(encoding="utf-8")
    assert markdown_output.startswith("# Top Contributors for acme")
    assert "| rank | user_name | num_modifications | num_commits | repos |" in markdown_output


def test_list_repos_output_text_infers_format_from_extension(tmp_path) -> None:
    runner = CliRunner()
    output_path = tmp_path / "repos.txt"
    repositories = [
        RepositoryRef(
            name="pysdk",
            full_name="acme/pysdk",
            default_branch="main",
            is_archived=False,
        ),
    ]
    with patch("github_report.cli.service.list_repositories", return_value=repositories):
        result = runner.invoke(app, ["list-repos", "--owner", "acme", "--output", str(output_path)])

    assert result.exit_code == 0
    text_output = output_path.read_text(encoding="utf-8")
    assert "Public Repositories for acme" in text_output
    assert "Repositories" in text_output
    assert " 1. acme/pysdk" in text_output
    assert "    Visibility     : public" in text_output


def test_list_repos_output_html_infers_format_from_extension(tmp_path) -> None:
    runner = CliRunner()
    output_path = tmp_path / "repos.html"
    repositories = [
        RepositoryRef(
            name="pysdk",
            full_name="acme/pysdk",
            default_branch="main",
            is_archived=False,
        ),
    ]
    with patch("github_report.cli.service.list_repositories", return_value=repositories):
        result = runner.invoke(app, ["list-repos", "--owner", "acme", "--output", str(output_path)])

    assert result.exit_code == 0
    html_output = output_path.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html_output
    assert "<h1>Public Repositories for acme</h1>" in html_output
    assert '<th class="text">visibility</th>' in html_output
    assert '<td class="text">public</td>' in html_output


def test_list_repos_command_renders_markdown() -> None:
    runner = CliRunner()
    repositories = [
        RepositoryRef(
            name="pysdk",
            full_name="acme/pysdk",
            default_branch="main",
            is_archived=False,
        ),
    ]
    with patch("github_report.cli.service.list_repositories", return_value=repositories):
        result = runner.invoke(app, ["list-repos", "--owner", "acme"])

    assert result.exit_code == 0
    assert "# Public Repositories for acme" in result.stdout
    assert "acme/pysdk" in result.stdout
    assert "| repo_name | visibility | default_branch | archived |" in result.stdout


def test_list_repos_all_flag_passes_include_private() -> None:
    runner = CliRunner()
    with (
        patch("github_report.cli.build_list_repos_options") as mock_build_options,
        patch("github_report.cli.service.list_repositories", return_value=[]),
    ):
        mock_build_options.return_value = type(
            "Options",
            (),
            {
                "owner": "acme",
                "include_private": True,
                "format": "markdown",
                "output": None,
            },
        )()
        result = runner.invoke(app, ["list-repos", "--owner", "acme", "--all"])

    assert result.exit_code == 0
    assert mock_build_options.call_args.kwargs["include_private"] is True


def test_loc_command_renders_csv_for_bare_repo_path_target() -> None:
    runner = CliRunner()
    with patch(
        "github_report.cli.service.build_loc_report", return_value=sample_loc_report()
    ) as mock_build:
        result = runner.invoke(
            app,
            [
                "loc",
                "nebius-ps-services/services/nebius-cxcli/",
                "--format",
                "csv",
            ],
        )

    assert result.exit_code == 0
    assert "language,files,code_lines,comment_lines,blank_lines,total_lines" in result.stdout
    assert "Python,2,42,3,7,52" in result.stdout
    assert "Total,2,42,3,7,52" in result.stdout
    options = mock_build.call_args.args[0]
    assert options.target == "nebius-ps-services/services/nebius-cxcli"
    assert options.owner is None
    assert options.ref == "main"


def test_root_owner_option_flows_into_loc_command() -> None:
    runner = CliRunner()
    with patch(
        "github_report.cli.service.build_loc_report", return_value=sample_loc_report()
    ) as mock_build:
        result = runner.invoke(
            app,
            ["--owner", "nebius", "loc", "nebius-ps-services", "--format", "csv"],
        )

    assert result.exit_code == 0
    assert mock_build.call_args.args[0].owner == "nebius"


def test_loc_output_csv_infers_format_from_extension(tmp_path) -> None:
    runner = CliRunner()
    output_path = tmp_path / "loc.csv"
    with patch("github_report.cli.service.build_loc_report", return_value=sample_loc_report()):
        result = runner.invoke(
            app,
            ["loc", "nebius-ps-services", "--output", str(output_path)],
        )

    assert result.exit_code == 0
    assert "Wrote" in result.stderr
    assert result.stdout == ""
    assert output_path.read_text(encoding="utf-8").splitlines() == [
        "language,files,code_lines,comment_lines,blank_lines,total_lines",
        "Python,2,42,3,7,52",
        "Total,2,42,3,7,52",
    ]


def test_list_repos_requires_owner() -> None:
    result = CliRunner().invoke(app, ["list-repos"])

    assert result.exit_code == 2
    assert "Missing required option '--owner'" in result.stderr


def test_root_help_mentions_owner_requirement() -> None:
    result = CliRunner().invoke(app, ["--help"])
    output = _normalize_help_output(result.stdout)

    assert result.exit_code == 0
    assert "--owner" in output
    assert "Owner-wide commands require `--owner`" in output
    assert "Default GitHub owner for commands that accept an" in output
    assert "Contributor reports and repository listings" in output
    assert "require an owner" in output
    assert "Examples:" in output
    assert "github-report top-users --owner nebius" in output
    assert (
        "github-report top-users --owner lm-academy --top 5 --days 60 --output report.txt" in output
    )
    assert "Output formats:" in output
    assert "markdown (default): raw Markdown;" in output
    assert "text: plain text; inferred from .txt." in output
    assert "html: Word-friendly HTML; inferred from .html or .htm." in output
    assert "csv: comma-separated values; inferred from .csv." in output
    assert "github-report list-repos --owner nebius --output repos.txt" in output
    assert "github-report loc nebius-ps-services/services/nebius-cxcli/" in output
    assert "repo-breakdown" not in output


def test_top_users_help_mentions_exclude_option() -> None:
    result = CliRunner().invoke(app, ["top-users", "--help"])
    output = _normalize_help_output(result.stdout)

    assert result.exit_code == 0
    assert "--owner" in output
    assert "--all-time" in output
    assert "--days" in output
    assert "[default: (30)]" in output
    assert "--no-all-time" not in output
    assert "--exclude" in output
    assert "--format" in output
    assert "--output" in output
    assert "text: plain text; inferred from .txt." in output


def test_top_users_help_includes_examples() -> None:
    result = CliRunner().invoke(app, ["top-users", "--help"])
    output = _normalize_help_output(result.stdout)

    assert result.exit_code == 0
    assert "Examples:" in output
    assert "github-report top-users --owner lm-academy --top 5 --days 60" in output
    assert "github-report top-users --owner nebius --exclude old-app --output report.txt" in output
    assert "github-report top-users --owner nebius --output report.md" in output
    assert "github-report top-users --owner nebius --output report.html" in output
    assert "github-report top-users --owner nebius --format csv --output report.csv" in output


def test_list_repos_help_includes_examples() -> None:
    result = CliRunner().invoke(app, ["list-repos", "--help"])
    output = _normalize_help_output(result.stdout)

    assert result.exit_code == 0
    assert "List public repositories for an owner; use `--all` to include private ones." in output
    assert "Examples:" in output
    assert "--all" in output
    assert "Include private repositories in" in output
    assert "github-report list-repos --owner nebius --all" in output
    assert "github-report list-repos --owner nebius --output repos.txt" in output
    assert "github-report list-repos --owner nebius --output repos.html" in output
    assert "github-report list-repos --owner nebius --format csv --output repos.csv" in output
    assert "github-report list-repos --owner lm-academy --output repos.txt" in output
    assert "github-report list-repos --owner nebius --format html --output repos.html" in output


def test_loc_help_includes_repo_path_examples() -> None:
    result = CliRunner().invoke(app, ["loc", "--help"])
    output = _normalize_help_output(result.stdout)

    assert result.exit_code == 0
    assert "Count physical source lines" in output
    assert "github-report loc nebius-ps-services" in output
    assert "github-report loc nebius-ps-services/services/nebius-cxcli/" in output
    assert "github-report loc nebius/nebius-ps-services/services/nebius-cxcli/" in output
    assert "--ref" in output
    assert "--owner" in output
    assert "Bare repo names are resolved by exact GitHub repository-name search" in output
