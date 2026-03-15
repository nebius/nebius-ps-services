"""Business logic for repository selection, aggregation, and ranking."""

from __future__ import annotations

from github_report.models import (
    RepoContributorRow,
    ReportBundle,
    ReportMetadata,
    RepositoryRef,
    UserContributorRow,
)
from github_report.services.github_client import (
    GitHubGraphQLClient,
    GitHubMetadataClient,
)
from github_report.settings import (
    ListReposOptions,
    ReportOptions,
    SortBy,
    resolve_github_token,
    utc_now,
)


class GitHubReportService:
    """Coordinates repository discovery and contributor ranking."""

    def __init__(
        self,
        *,
        metadata_client_cls: type[GitHubMetadataClient] = GitHubMetadataClient,
        graphql_client_cls: type[GitHubGraphQLClient] = GitHubGraphQLClient,
    ) -> None:
        self._metadata_client_cls = metadata_client_cls
        self._graphql_client_cls = graphql_client_cls

    def list_repositories(self, options: ListReposOptions) -> list[RepositoryRef]:
        """Return accessible repositories for an owner."""

        token = resolve_github_token()
        client = self._metadata_client_cls(token)
        return client.list_accessible_repositories(options.owner)

    async def build_report(self, options: ReportOptions) -> ReportBundle:
        """Build both the aggregated and detailed report views."""

        token = resolve_github_token()
        metadata_client = self._metadata_client_cls(token)
        available_repositories = metadata_client.list_accessible_repositories(options.owner)
        selected_repositories = select_repositories(
            available_repositories,
            options.repos,
            options.exclude_repos,
        )

        graphql_client = self._graphql_client_cls(
            token,
            timeout_seconds=options.timeout_seconds,
            concurrency=options.concurrency,
        )
        scan_results = await graphql_client.collect_activity(
            selected_repositories,
            since=options.since,
            until=options.until,
            include_bots=options.include_bots,
        )

        repo_rows = sort_repo_rows(
            [row for result in scan_results for row in result.rows],
            options.sort_by,
        )
        top_users = summarize_users(repo_rows, options.sort_by)
        metadata = ReportMetadata(
            owner=options.owner,
            generated_at=utc_now(),
            since=options.since,
            until=options.until,
            repos_requested=len(selected_repositories),
            repos_scanned=len(scan_results),
            commits_scanned=sum(result.commits_scanned for result in scan_results),
            include_bots=options.include_bots,
            branch_scope="repository default branch only",
            sort_by=options.sort_by,
            window_kind=options.window_kind,
            lookback_days=options.lookback_days,
        )
        return ReportBundle(metadata=metadata, top_users=top_users, repo_rows=repo_rows)


def select_repositories(
    available_repositories: list[RepositoryRef],
    requested_repositories: tuple[str, ...],
    excluded_repositories: tuple[str, ...] = (),
) -> list[RepositoryRef]:
    """Resolve repo filters against the repositories visible to the token."""

    repository_map = {repo.full_name: repo for repo in available_repositories}
    missing_requested = [
        repo_name for repo_name in requested_repositories if repo_name not in repository_map
    ]
    if missing_requested:
        missing_list = ", ".join(missing_requested)
        raise ValueError(
            f"Requested repositories are not accessible with the current token: {missing_list}"
        )

    missing_excluded = [
        repo_name for repo_name in excluded_repositories if repo_name not in repository_map
    ]
    if missing_excluded:
        missing_list = ", ".join(missing_excluded)
        raise ValueError(
            f"Excluded repositories are not accessible with the current token: {missing_list}"
        )

    selected_repositories = (
        [repository_map[repo_name] for repo_name in requested_repositories]
        if requested_repositories
        else available_repositories
    )
    if not excluded_repositories:
        return selected_repositories

    excluded_repository_names = set(excluded_repositories)
    return [
        repo for repo in selected_repositories if repo.full_name not in excluded_repository_names
    ]


def summarize_users(
    repo_rows: list[RepoContributorRow], sort_by: SortBy
) -> list[UserContributorRow]:
    """Aggregate contributor rows across repositories."""

    aggregated: dict[str, UserContributorRow] = {}
    repo_names_by_user: dict[str, set[str]] = {}
    for row in repo_rows:
        user_row = aggregated.setdefault(
            row.user_name,
            UserContributorRow(user_name=row.user_name, display_name=row.display_name),
        )
        user_row.num_commits += row.num_commits
        user_row.num_modifications += row.num_modifications
        if user_row.display_name == user_row.user_name and row.display_name != row.user_name:
            user_row.display_name = row.display_name
        repo_names = repo_names_by_user.setdefault(row.user_name, set())
        if row.repo_name not in repo_names:
            repo_names.add(row.repo_name)
            user_row.repo_count += 1

    for user_name, user_row in aggregated.items():
        user_row.repos = tuple(sorted(repo_names_by_user[user_name]))

    return sorted(aggregated.values(), key=lambda row: user_sort_key(row, sort_by))
def sort_repo_rows(rows: list[RepoContributorRow], sort_by: SortBy) -> list[RepoContributorRow]:
    """Sort the detailed rows for consistent output."""

    return sorted(rows, key=lambda row: repo_row_sort_key(row, sort_by))


def user_sort_key(row: UserContributorRow, sort_by: SortBy) -> tuple:
    """Sort contributors by the requested rank metric."""

    if sort_by is SortBy.modifications:
        return (-row.num_modifications, -row.num_commits, row.user_name)
    return (-row.num_commits, -row.num_modifications, row.user_name)


def repo_row_sort_key(row: RepoContributorRow, sort_by: SortBy) -> tuple:
    """Sort repo rows by the requested rank metric."""

    if sort_by is SortBy.modifications:
        return (-row.num_modifications, -row.num_commits, row.user_name, row.repo_name)
    return (-row.num_commits, -row.num_modifications, row.user_name, row.repo_name)
