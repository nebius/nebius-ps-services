from __future__ import annotations

from datetime import UTC, datetime

import pytest

from github_report.settings import OutputFormat, SortBy, WindowKind, build_report_options


def test_build_report_options_merges_repo_filters(tmp_path) -> None:
    repo_file = tmp_path / "repos.txt"
    repo_file.write_text("# comment\npysdk\nnebius/api\n\npysdk\n", encoding="utf-8")

    options = build_report_options(
        org="nebius",
        top=50,
        since="2026-01-01",
        until="2026-01-31",
        repos="gosdk, js-sdk",
        repos_file=repo_file,
        exclude="csa-soperator-deployments, nebius/api",
        format=OutputFormat.csv,
        sort_by=SortBy.modifications,
    )

    assert options.top == 50
    assert options.since == datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    assert options.until == datetime(2026, 1, 31, 23, 59, 59, 999999, tzinfo=UTC)
    assert options.repos == (
        "nebius/gosdk",
        "nebius/js-sdk",
        "nebius/pysdk",
        "nebius/api",
    )
    assert options.exclude_repos == (
        "nebius/csa-soperator-deployments",
        "nebius/api",
    )
    assert options.sort_by is SortBy.modifications
    assert options.window_kind is WindowKind.custom


def test_build_report_options_defaults_to_last_30_days_window() -> None:
    options = build_report_options(org="nebius", until="2026-03-13T12:00:00Z")

    assert options.since == datetime(2026, 2, 11, 12, 0, 0, tzinfo=UTC)
    assert options.until == datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    assert options.lookback_days == 30
    assert options.window_kind is WindowKind.relative_days


def test_build_report_options_supports_relative_days_window() -> None:
    options = build_report_options(
        org="nebius",
        days=60,
        until="2026-03-13T12:00:00Z",
    )

    assert options.since == datetime(2026, 1, 12, 12, 0, 0, tzinfo=UTC)
    assert options.until == datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    assert options.lookback_days == 60
    assert options.window_kind is WindowKind.relative_days


def test_build_report_options_anchors_default_days_to_until() -> None:
    options = build_report_options(
        org="nebius",
        until="2026-03-01",
    )

    assert options.since == datetime(2026, 1, 30, 23, 59, 59, 999999, tzinfo=UTC)
    assert options.until == datetime(2026, 3, 1, 23, 59, 59, 999999, tzinfo=UTC)
    assert options.lookback_days == 30
    assert options.window_kind is WindowKind.relative_days


def test_build_report_options_all_time_clears_since() -> None:
    options = build_report_options(
        org="nebius",
        all_time=True,
        since="2026-01-01",
        until="2026-02-01",
    )

    assert options.since is None
    assert options.until == datetime(2026, 2, 1, 23, 59, 59, 999999, tzinfo=UTC)
    assert options.sort_by is SortBy.modifications
    assert options.window_kind is WindowKind.all_time


def test_build_report_options_rejects_other_org_repos() -> None:
    with pytest.raises(ValueError, match="does not belong to org"):
        build_report_options(org="nebius", repos="octocat/hello-world")


def test_build_report_options_rejects_days_with_since() -> None:
    with pytest.raises(ValueError, match="--days cannot be combined with --since"):
        build_report_options(org="nebius", since="2026-01-01", days=60)
