"""Typed models used across the reporting flow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from github_report.settings import SortBy, WindowKind


@dataclass(slots=True, frozen=True)
class RepositoryRef:
    """Metadata for a repository visible to the authenticated user."""

    name: str
    full_name: str
    default_branch: str | None
    is_archived: bool
    is_private: bool = False


@dataclass(slots=True)
class RepoContributorRow:
    """Aggregated activity for a contributor within a single repository."""

    user_name: str
    display_name: str
    repo_name: str
    account_login: str | None = None
    num_commits: int = 0
    num_modifications: int = 0


@dataclass(slots=True)
class UserContributorRow:
    """Aggregated activity for a contributor across multiple repositories."""

    user_name: str
    display_name: str
    num_commits: int = 0
    num_modifications: int = 0
    repo_count: int = 0
    repos: tuple[str, ...] = ()


@dataclass(slots=True)
class RepoScanResult:
    """Collection result for one repository scan."""

    repository: RepositoryRef
    rows: list[RepoContributorRow]
    commits_scanned: int


@dataclass(slots=True)
class ReportMetadata:
    """Metadata shared by every emitted report."""

    owner: str
    generated_at: datetime
    since: datetime | None
    until: datetime
    repos_requested: int
    repos_scanned: int
    commits_scanned: int
    include_bots: bool
    branch_scope: str
    sort_by: SortBy
    window_kind: WindowKind
    lookback_days: int | None = None


@dataclass(slots=True)
class ReportBundle:
    """Full report with both aggregated and detailed views."""

    metadata: ReportMetadata
    top_users: list[UserContributorRow]
    repo_rows: list[RepoContributorRow]
