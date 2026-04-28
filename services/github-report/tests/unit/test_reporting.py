from __future__ import annotations

import pytest

from github_report.models import RepoContributorRow, RepositoryRef
from github_report.services.reporting import (
    GitHubReportService,
    select_repositories,
    sort_repo_rows,
    summarize_users,
)
from github_report.settings import ListReposOptions, SortBy


def test_summarize_users_aggregates_across_repositories() -> None:
    rows = [
        RepoContributorRow(
            "alice",
            "Alice Example (@alice)",
            "nebius/pysdk",
            num_commits=4,
            num_modifications=40,
        ),
        RepoContributorRow(
            "alice",
            "Alice Example (@alice)",
            "nebius/gosdk",
            num_commits=2,
            num_modifications=15,
        ),
        RepoContributorRow("bob", "bob", "nebius/pysdk", num_commits=5, num_modifications=12),
    ]

    users = summarize_users(rows, SortBy.commits)

    assert [
        (
            row.user_name,
            row.display_name,
            row.num_commits,
            row.num_modifications,
            row.repo_count,
            row.repos,
        )
        for row in users
    ] == [
        ("alice", "Alice Example (@alice)", 6, 55, 2, ("nebius/gosdk", "nebius/pysdk")),
        ("bob", "bob", 5, 12, 1, ("nebius/pysdk",)),
    ]


def test_sort_repo_rows_can_rank_by_modifications() -> None:
    rows = [
        RepoContributorRow(
            "alice", "Alice Example (@alice)", "nebius/pysdk", num_commits=4, num_modifications=10
        ),
        RepoContributorRow(
            "bob", "Bob Example (@bob)", "nebius/gosdk", num_commits=2, num_modifications=80
        ),
        RepoContributorRow(
            "carol", "Carol Example (@carol)", "nebius/api", num_commits=6, num_modifications=30
        ),
    ]

    sorted_rows = sort_repo_rows(rows, SortBy.modifications)

    assert [(row.user_name, row.repo_name) for row in sorted_rows] == [
        ("bob", "nebius/gosdk"),
        ("carol", "nebius/api"),
        ("alice", "nebius/pysdk"),
    ]


def test_select_repositories_can_exclude_from_all_accessible() -> None:
    repositories = [
        RepositoryRef("api", "nebius/api", "main", False),
        RepositoryRef("gosdk", "nebius/gosdk", "main", False),
        RepositoryRef("pysdk", "nebius/pysdk", "main", False),
    ]

    selected = select_repositories(repositories, (), ("nebius/gosdk",))

    assert [repo.full_name for repo in selected] == [
        "nebius/api",
        "nebius/pysdk",
    ]


def test_select_repositories_applies_exclusions_after_requested_filter() -> None:
    repositories = [
        RepositoryRef("api", "nebius/api", "main", False),
        RepositoryRef("gosdk", "nebius/gosdk", "main", False),
        RepositoryRef("pysdk", "nebius/pysdk", "main", False),
    ]

    selected = select_repositories(
        repositories,
        ("nebius/api", "nebius/gosdk"),
        ("nebius/gosdk",),
    )

    assert [repo.full_name for repo in selected] == ["nebius/api"]


def test_select_repositories_rejects_unknown_excluded_repo() -> None:
    repositories = [
        RepositoryRef("api", "nebius/api", "main", False),
        RepositoryRef("pysdk", "nebius/pysdk", "main", False),
    ]

    with pytest.raises(ValueError, match="Excluded repositories are not accessible"):
        select_repositories(repositories, (), ("nebius/gosdk",))


class _FakeMetadataClient:
    calls: list[tuple[str]]

    def __init__(self, token: str) -> None:
        self.token = token
        self.calls = []

    def list_accessible_repositories(self, owner_name: str) -> list[RepositoryRef]:
        self.calls.append((owner_name,))
        return [
            RepositoryRef("api", "nebius/api", "main", False, False),
            RepositoryRef("internal", "nebius/internal", "main", False, True),
        ]


def test_list_repositories_defaults_to_public_only(monkeypatch) -> None:
    monkeypatch.setattr("github_report.services.reporting.resolve_github_token", lambda: "token")
    service = GitHubReportService(metadata_client_cls=_FakeMetadataClient)

    repositories = service.list_repositories(ListReposOptions(owner="nebius"))

    assert [repo.full_name for repo in repositories] == ["nebius/api"]


def test_list_repositories_all_includes_private(monkeypatch) -> None:
    monkeypatch.setattr("github_report.services.reporting.resolve_github_token", lambda: "token")
    service = GitHubReportService(metadata_client_cls=_FakeMetadataClient)

    repositories = service.list_repositories(ListReposOptions(owner="nebius", include_private=True))

    assert [repo.full_name for repo in repositories] == [
        "nebius/api",
        "nebius/internal",
    ]
