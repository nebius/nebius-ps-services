"""GitHub API clients used by the reporting service."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote, urlparse

import httpx
from github import Auth, Github
from github.GithubException import GithubException

from github_report.models import RepoContributorRow, RepoScanResult, RepositoryRef

REPO_ACTIVITY_QUERY = """
query RepoActivity(
  $owner: String!
  $repo: String!
  $cursor: String
  $since: GitTimestamp
  $until: GitTimestamp
) {
  repository(owner: $owner, name: $repo) {
    defaultBranchRef {
      name
      target {
        ... on Commit {
          history(first: 100, after: $cursor, since: $since, until: $until) {
            pageInfo {
              hasNextPage
              endCursor
            }
            nodes {
              additions
              deletions
              author {
                name
                email
                user {
                  login
                  name
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


class GitHubClientError(RuntimeError):
    """Raised when GitHub API calls fail."""


class GitHubRepositoryNotFoundError(GitHubClientError):
    """Raised when a requested GitHub repository does not exist or is not visible."""


@dataclass(slots=True, frozen=True)
class AuthorIdentity:
    """Stable contributor identity plus human-friendly display label."""

    user_name: str
    display_name: str
    account_login: str | None = None


class GitHubMetadataClient:
    """Thin PyGithub wrapper used for repository discovery."""

    def __init__(self, token: str, *, per_page: int = 100) -> None:
        self._client = Github(auth=Auth.Token(token), per_page=per_page)

    def list_accessible_repositories(self, owner_name: str) -> list[RepositoryRef]:
        """List repositories visible to the authenticated user for an owner."""

        try:
            owner = self._client.get_user(owner_name)
            if owner.type == "Organization":
                repositories_iterable = self._client.get_organization(owner_name).get_repos()
            else:
                repositories_iterable = owner.get_repos()
            repositories = [
                RepositoryRef(
                    name=repo.name,
                    full_name=repo.full_name,
                    default_branch=repo.default_branch,
                    is_archived=repo.archived,
                    is_private=repo.private,
                )
                for repo in repositories_iterable
            ]
        except GithubException as exc:
            raise GitHubClientError(
                f"GitHub repository discovery failed for owner {owner_name!r}."
            ) from exc

        return sorted(repositories, key=lambda repo: repo.full_name)


class GitHubArchiveClient:
    """REST client for repository metadata lookup and archive downloads."""

    def __init__(self, token: str, *, timeout_seconds: float) -> None:
        self._client = httpx.Client(
            base_url="https://api.github.com",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=httpx.Timeout(timeout_seconds),
        )

    def get_repository(self, owner_name: str, repo_name: str) -> RepositoryRef:
        """Return repository metadata for an owner-qualified repository."""

        encoded_owner = quote(owner_name, safe="")
        encoded_repo = quote(repo_name, safe="")
        response = self._client.get(f"/repos/{encoded_owner}/{encoded_repo}")
        if response.status_code == 404:
            raise GitHubRepositoryNotFoundError(
                f"Repository {owner_name}/{repo_name} was not found or is not visible."
            )
        _raise_for_status(response, f"GitHub repository lookup failed for {owner_name}/{repo_name}")
        return _repository_ref_from_payload(response.json())

    def find_repository_by_name(self, repo_name: str) -> RepositoryRef:
        """Resolve a bare repository name to one exact visible GitHub repository."""

        response = self._client.get(
            "/search/repositories",
            params={"q": f"{repo_name} in:name", "per_page": "100"},
        )
        _raise_for_status(
            response,
            f"GitHub repository search failed for repository name {repo_name!r}",
        )
        payload = response.json()
        matches = [
            item
            for item in payload.get("items", [])
            if str(item.get("name", "")).casefold() == repo_name.casefold()
        ]
        unique_matches = {
            str(item["full_name"]): _repository_ref_from_payload(item)
            for item in matches
            if item.get("full_name")
        }
        if not unique_matches:
            raise GitHubRepositoryNotFoundError(
                f"Repository {repo_name!r} was not found by exact name search. "
                "Provide --owner or an owner-qualified target."
            )
        if len(unique_matches) > 1:
            choices = ", ".join(sorted(unique_matches))
            raise GitHubClientError(
                f"Repository name {repo_name!r} is ambiguous: {choices}. "
                "Provide --owner or an owner-qualified target."
            )
        return next(iter(unique_matches.values()))

    def download_repository_zipball(
        self,
        repository: RepositoryRef,
        *,
        ref: str,
    ) -> bytes:
        """Download a repository zip archive for a branch, tag, or SHA ref."""

        owner_name, repo_name = repository.full_name.split("/", 1)
        encoded_owner = quote(owner_name, safe="")
        encoded_repo = quote(repo_name, safe="")
        encoded_ref = quote(ref, safe="")
        response = self._client.get(
            f"/repos/{encoded_owner}/{encoded_repo}/zipball/{encoded_ref}",
            follow_redirects=False,
        )
        if response.status_code in {301, 302, 303, 307, 308}:
            redirect_url = response.headers.get("location")
            if not redirect_url:
                raise GitHubClientError(
                    f"GitHub archive download for {repository.full_name}@{ref} "
                    "did not include a redirect URL."
                )
            _validate_archive_redirect_url(redirect_url)
            response = self._client.get(redirect_url)

        if response.status_code == 404:
            raise GitHubRepositoryNotFoundError(
                f"Archive for {repository.full_name}@{ref} was not found. "
                "Check that the repository is visible and the ref exists."
            )
        _raise_for_status(
            response,
            f"GitHub archive download failed for {repository.full_name}@{ref}",
        )
        return response.content

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""

        self._client.close()


class GitHubGraphQLClient:
    """Async GraphQL client for batched default-branch commit history scans."""

    def __init__(
        self,
        token: str,
        *,
        timeout_seconds: float,
        concurrency: int,
        max_retries: int = 3,
    ) -> None:
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self._timeout = httpx.Timeout(timeout_seconds)
        self._limits = httpx.Limits(
            max_connections=concurrency,
            max_keepalive_connections=concurrency,
        )
        self._semaphore = asyncio.Semaphore(concurrency)
        self._max_retries = max_retries
        self._account_type_cache: dict[str, str | None] = {}

    async def collect_activity(
        self,
        repositories: list[RepositoryRef],
        *,
        since: datetime | None,
        until: datetime,
        include_bots: bool,
    ) -> list[RepoScanResult]:
        """Collect contributor activity for each repository."""

        async with httpx.AsyncClient(
            base_url="https://api.github.com",
            headers=self._headers,
            timeout=self._timeout,
            limits=self._limits,
        ) as client:
            tasks = [
                self._collect_repository_activity(
                    client,
                    repository,
                    since=since,
                    until=until,
                )
                for repository in repositories
            ]
            scan_results = list(await asyncio.gather(*tasks))
            if include_bots:
                return scan_results
            bot_logins = await self._resolve_bot_logins(
                client,
                [
                    row.account_login
                    for result in scan_results
                    for row in result.rows
                    if row.account_login is not None
                ],
            )
            return _filter_scan_results_by_account_login(scan_results, bot_logins)

    async def _collect_repository_activity(
        self,
        client: httpx.AsyncClient,
        repository: RepositoryRef,
        *,
        since: datetime | None,
        until: datetime,
    ) -> RepoScanResult:
        owner, repo_name = repository.full_name.split("/", 1)
        rows: dict[str, RepoContributorRow] = {}
        cursor: str | None = None
        commits_scanned = 0

        while True:
            data = await self._post_graphql(
                client,
                {
                    "query": REPO_ACTIVITY_QUERY,
                    "variables": {
                        "owner": owner,
                        "repo": repo_name,
                        "cursor": cursor,
                        "since": _format_git_timestamp(since),
                        "until": _format_git_timestamp(until),
                    },
                },
            )
            repository_data = data.get("repository")
            if repository_data is None:
                raise GitHubClientError(
                    f"Repository {repository.full_name!r} was not returned by GitHub."
                )

            default_branch_ref = repository_data.get("defaultBranchRef")
            if default_branch_ref is None:
                return RepoScanResult(repository=repository, rows=[], commits_scanned=0)

            history = default_branch_ref["target"]["history"]
            for node in history["nodes"]:
                author_identity = _normalize_author(node.get("author"))

                row = rows.setdefault(
                    author_identity.user_name,
                    RepoContributorRow(
                        user_name=author_identity.user_name,
                        display_name=author_identity.display_name,
                        repo_name=repository.full_name,
                        account_login=author_identity.account_login,
                    ),
                )
                if (
                    row.display_name == row.user_name
                    and author_identity.display_name != row.user_name
                ):
                    row.display_name = author_identity.display_name
                row.num_commits += 1
                row.num_modifications += int(node.get("additions", 0)) + int(
                    node.get("deletions", 0)
                )
                commits_scanned += 1

            if not history["pageInfo"]["hasNextPage"]:
                break
            cursor = history["pageInfo"]["endCursor"]

        return RepoScanResult(
            repository=repository,
            rows=list(rows.values()),
            commits_scanned=commits_scanned,
        )

    async def _post_graphql(self, client: httpx.AsyncClient, payload: dict) -> dict:
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                async with self._semaphore:
                    response = await client.post("/graphql", json=payload)
                response.raise_for_status()
                body = response.json()
                errors = body.get("errors")
                if errors:
                    message = "; ".join(
                        error.get("message", "Unknown GraphQL error") for error in errors
                    )
                    raise GitHubClientError(message)
                data = body.get("data")
                if data is None:
                    raise GitHubClientError(
                        "GitHub GraphQL response did not include a data payload."
                    )
                return data
            except (httpx.HTTPError, GitHubClientError) as exc:
                last_error = exc
                if not _is_retryable(exc) or attempt == self._max_retries:
                    break
                await asyncio.sleep(2 ** (attempt - 1))

        raise GitHubClientError("GitHub GraphQL request failed.") from last_error

    async def _resolve_bot_logins(
        self,
        client: httpx.AsyncClient,
        logins: Iterable[str],
    ) -> set[str]:
        unique_logins = sorted({login for login in logins if login})
        if not unique_logins:
            return set()

        account_types = await asyncio.gather(
            *(self._resolve_account_type(client, login) for login in unique_logins)
        )
        return {
            login
            for login, account_type in zip(unique_logins, account_types, strict=True)
            if account_type == "Bot"
        }

    async def _resolve_account_type(
        self,
        client: httpx.AsyncClient,
        login: str,
    ) -> str | None:
        cached_type = self._account_type_cache.get(login)
        if cached_type is not None or login in self._account_type_cache:
            return cached_type

        account_type = await self._fetch_account_type(client, login)
        normalized_type = str(account_type) if account_type else None
        self._account_type_cache[login] = normalized_type
        return normalized_type

    async def _fetch_account_type(
        self,
        client: httpx.AsyncClient,
        login: str,
    ) -> str | None:
        last_error: Exception | None = None
        encoded_login = quote(login, safe="")
        for attempt in range(1, self._max_retries + 1):
            try:
                async with self._semaphore:
                    response = await client.get(f"/users/{encoded_login}")
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                body = response.json()
                account_type = body.get("type")
                return str(account_type) if account_type else None
            except httpx.HTTPError as exc:
                last_error = exc
                if not _is_retryable(exc) or attempt == self._max_retries:
                    break
                await asyncio.sleep(2 ** (attempt - 1))

        raise GitHubClientError(
            f"GitHub account lookup failed for login {login!r}."
        ) from last_error


def _format_git_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def _repository_ref_from_payload(payload: dict) -> RepositoryRef:
    full_name = str(payload.get("full_name") or "")
    if "/" not in full_name:
        raise GitHubClientError("GitHub repository response did not include a full_name.")
    return RepositoryRef(
        name=str(payload.get("name") or full_name.split("/", 1)[1]),
        full_name=full_name,
        default_branch=(
            str(payload["default_branch"]) if payload.get("default_branch") is not None else None
        ),
        is_archived=bool(payload.get("archived", False)),
        is_private=bool(payload.get("private", False)),
    )


def _raise_for_status(response: httpx.Response, message: str) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise GitHubClientError(
            f"{message}: HTTP {response.status_code} {response.reason_phrase}."
        ) from exc


def _validate_archive_redirect_url(redirect_url: str) -> None:
    hostname = urlparse(redirect_url).hostname
    if hostname not in {"api.github.com", "codeload.github.com"}:
        raise GitHubClientError(
            f"GitHub archive download redirected to unexpected host {hostname!r}."
        )


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429, 500, 502, 503, 504}
    if isinstance(exc, GitHubClientError):
        return "rate limit" in str(exc).lower()
    return False


def _filter_scan_results_by_account_login(
    scan_results: list[RepoScanResult],
    excluded_logins: set[str],
) -> list[RepoScanResult]:
    if not excluded_logins:
        return scan_results

    return [
        RepoScanResult(
            repository=result.repository,
            rows=[row for row in result.rows if row.account_login not in excluded_logins],
            # Preserve the true scan volume; filtering affects output rows, not scan work done.
            commits_scanned=result.commits_scanned,
        )
        for result in scan_results
    ]


def _normalize_author(author: dict | None) -> AuthorIdentity:
    if not author:
        return AuthorIdentity(user_name="<unknown>", display_name="<unknown>")

    user = author.get("user") or {}
    login = user.get("login")
    profile_name = (user.get("name") or "").strip()
    if login:
        if profile_name:
            return AuthorIdentity(
                user_name=login,
                display_name=f"{profile_name} (@{login})",
                account_login=login,
            )
        return AuthorIdentity(user_name=login, display_name=login, account_login=login)

    email = author.get("email")
    name = (author.get("name") or "").strip()
    if email:
        label = f"{name or 'unknown'} <{email}>"
        return AuthorIdentity(user_name=label, display_name=label)
    if name:
        return AuthorIdentity(user_name=name, display_name=name)
    return AuthorIdentity(user_name="<unknown>", display_name="<unknown>")
