from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from github_report.models import RepoContributorRow, RepoScanResult, RepositoryRef
from github_report.services.github_client import (
    GitHubGraphQLClient,
    _filter_scan_results_by_account_login,
    _normalize_author,
)


def test_normalize_author_uses_profile_name_when_available() -> None:
    identity = _normalize_author(
        {
            "name": "Ignored Commit Name",
            "email": "alice@example.com",
            "user": {"login": "alice", "name": "Alice Example"},
        }
    )

    assert identity.user_name == "alice"
    assert identity.display_name == "Alice Example (@alice)"
    assert identity.account_login == "alice"


def test_normalize_author_falls_back_to_login_without_profile_name() -> None:
    identity = _normalize_author(
        {
            "name": "Ignored Commit Name",
            "email": "bob@example.com",
            "user": {"login": "bob", "name": None},
        }
    )

    assert identity.user_name == "bob"
    assert identity.display_name == "bob"
    assert identity.account_login == "bob"


def test_resolve_bot_logins_uses_github_account_type_and_caches() -> None:
    client = GitHubGraphQLClient("token", timeout_seconds=30.0, concurrency=2)
    fetch_account_type = AsyncMock(side_effect=["User", "Bot"])
    client._fetch_account_type = fetch_account_type

    first_result = asyncio.run(
        client._resolve_bot_logins(object(), ["alice", "renovate[bot]", "renovate[bot]"])
    )
    second_result = asyncio.run(client._resolve_bot_logins(object(), ["renovate[bot]"]))

    assert first_result == {"renovate[bot]"}
    assert second_result == {"renovate[bot]"}
    assert fetch_account_type.await_count == 2


def test_filter_scan_results_by_account_login_preserves_commits_scanned() -> None:
    scan_results = [
        RepoScanResult(
            repository=RepositoryRef("api", "nebius/api", "main", False),
            rows=[
                RepoContributorRow(
                    "alice",
                    "Alice Example (@alice)",
                    "nebius/api",
                    account_login="alice",
                    num_commits=3,
                    num_modifications=30,
                ),
                RepoContributorRow(
                    "renovate[bot]",
                    "renovate[bot]",
                    "nebius/api",
                    account_login="renovate[bot]",
                    num_commits=5,
                    num_modifications=50,
                ),
            ],
            commits_scanned=10,
        )
    ]

    filtered_results = _filter_scan_results_by_account_login(scan_results, {"renovate[bot]"})

    assert filtered_results[0].commits_scanned == 10
    assert [row.user_name for row in filtered_results[0].rows] == ["alice"]


def test_collect_activity_skips_account_lookup_when_bots_are_included() -> None:
    client = GitHubGraphQLClient("token", timeout_seconds=30.0, concurrency=2)
    repository = RepositoryRef("api", "nebius/api", "main", False)
    sample_result = RepoScanResult(
        repository=repository,
        rows=[
            RepoContributorRow(
                "renovate[bot]",
                "renovate[bot]",
                "nebius/api",
                account_login="renovate[bot]",
                num_commits=2,
                num_modifications=20,
            )
        ],
        commits_scanned=2,
    )

    client._collect_repository_activity = AsyncMock(return_value=sample_result)
    client._resolve_bot_logins = AsyncMock()

    results = asyncio.run(
        client.collect_activity(
            [repository],
            since=None,
            until=datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC),
            include_bots=True,
        )
    )

    assert results == [sample_result]
    client._resolve_bot_logins.assert_not_awaited()
