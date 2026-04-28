"""Runtime settings and CLI input normalization."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_TOP = 10
DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_ARCHIVE_TIMEOUT_SECONDS = 120.0
DEFAULT_CONCURRENCY = 4
DEFAULT_LOC_REF = "main"


class OutputFormat(StrEnum):
    """Supported CLI output formats."""

    csv = "csv"
    html = "html"
    markdown = "markdown"
    text = "text"


class SortBy(StrEnum):
    """Supported report ranking keys."""

    commits = "commits"
    modifications = "modifications"


class WindowKind(StrEnum):
    """Supported window selection modes."""

    relative_days = "relative_days"
    all_time = "all_time"
    custom = "custom"


class ReportOptions(BaseModel):
    """Validated settings for contributor reports."""

    model_config = ConfigDict(frozen=True)

    owner: str = Field(min_length=1)
    top: int = Field(default=DEFAULT_TOP, ge=1, le=1000)
    since: datetime | None = None
    until: datetime
    include_bots: bool = False
    format: OutputFormat = OutputFormat.markdown
    output: Path | None = None
    repos: tuple[str, ...] = ()
    exclude_repos: tuple[str, ...] = ()
    concurrency: int = Field(default=DEFAULT_CONCURRENCY, ge=1, le=16)
    timeout_seconds: float = Field(default=DEFAULT_TIMEOUT_SECONDS, gt=0.0, le=120.0)
    sort_by: SortBy = SortBy.modifications
    window_kind: WindowKind = WindowKind.relative_days
    lookback_days: int | None = Field(default=DEFAULT_LOOKBACK_DAYS, ge=1, le=3650)

    @field_validator("owner")
    @classmethod
    def normalize_owner(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Owner name must not be empty.")
        return normalized

    @model_validator(mode="after")
    def validate_dates(self) -> ReportOptions:
        if self.since is not None and self.since >= self.until:
            raise ValueError("--since must be earlier than --until.")
        return self


class ListReposOptions(BaseModel):
    """Validated settings for repository listing."""

    model_config = ConfigDict(frozen=True)

    owner: str = Field(min_length=1)
    include_private: bool = False
    format: OutputFormat = OutputFormat.markdown
    output: Path | None = None

    @field_validator("owner")
    @classmethod
    def normalize_owner(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Owner name must not be empty.")
        return normalized


class LocOptions(BaseModel):
    """Validated settings for repository line-count reports."""

    model_config = ConfigDict(frozen=True)

    target: str = Field(min_length=1)
    owner: str | None = None
    ref: str = Field(default=DEFAULT_LOC_REF, min_length=1)
    format: OutputFormat = OutputFormat.markdown
    output: Path | None = None
    timeout_seconds: float = Field(default=DEFAULT_ARCHIVE_TIMEOUT_SECONDS, gt=0.0, le=300.0)

    @field_validator("target")
    @classmethod
    def normalize_target(cls, value: str) -> str:
        return normalize_repo_path_target(value)

    @field_validator("owner")
    @classmethod
    def normalize_optional_owner(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        return normalized

    @field_validator("ref")
    @classmethod
    def normalize_ref(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Git ref must not be empty.")
        return normalized


def utc_now() -> datetime:
    """Return the current UTC time with second precision."""

    return datetime.now(UTC).replace(microsecond=0)


def resolve_github_token() -> str:
    """Resolve the GitHub token from the supported environment variables."""

    import os

    for env_var in ("GITHUB_TOKEN", "GH_TOKEN"):
        token = os.getenv(env_var)
        if token:
            return token
    raise ValueError("Set GITHUB_TOKEN or GH_TOKEN before running github-report.")


def parse_datetime_input(
    value: str | None,
    *,
    default: datetime | None,
    end_of_day: bool = False,
) -> datetime | None:
    """Parse a user-supplied date or timestamp into UTC."""

    if value is None:
        return default

    raw = value.strip()
    if not raw:
        return default

    try:
        if len(raw) == 10:
            parsed_date = date.fromisoformat(raw)
            parsed_time = time.max if end_of_day else time.min
            return datetime.combine(parsed_date, parsed_time, tzinfo=UTC)

        parsed_dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid datetime value: {value!r}") from exc

    if parsed_dt.tzinfo is None:
        parsed_dt = parsed_dt.replace(tzinfo=UTC)
    else:
        parsed_dt = parsed_dt.astimezone(UTC)
    return parsed_dt


def normalize_repo_name(owner: str, repo_name: str) -> str:
    """Normalize a repo token to an owner-qualified full name."""

    candidate = repo_name.strip()
    if not candidate:
        raise ValueError("Repository names must not be empty.")

    if "/" not in candidate:
        return f"{owner}/{candidate}"

    repo_owner, name = candidate.split("/", 1)
    if repo_owner != owner:
        raise ValueError(f"Repository {candidate!r} does not belong to owner {owner!r}.")
    if not name:
        raise ValueError(f"Repository {candidate!r} is invalid.")
    return candidate


def normalize_repo_path_target(value: str) -> str:
    """Normalize a repository or repository/path target."""

    candidate = value.strip().strip("/")
    while candidate.startswith("./"):
        candidate = candidate[2:]
    parts = [part for part in candidate.split("/") if part]
    if not parts:
        raise ValueError("Repository target must not be empty.")
    if any(part in {".", ".."} for part in parts):
        raise ValueError("Repository target must not contain '.' or '..' path segments.")
    return "/".join(parts)


def parse_repo_csv(raw_value: str | None) -> list[str]:
    """Parse a comma-separated repo list."""

    if raw_value is None:
        return []
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def parse_repo_file(path: Path | None) -> list[str]:
    """Parse a newline-delimited repo list with optional comments."""

    if path is None:
        return []

    entries: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entries.append(stripped)
    return entries


def resolve_repo_filters(owner: str, repos: str | None, repos_file: Path | None) -> tuple[str, ...]:
    """Merge repo filters from CLI inputs into a deduplicated tuple."""

    combined = parse_repo_csv(repos) + parse_repo_file(repos_file)
    normalized: list[str] = []
    seen: set[str] = set()
    for item in combined:
        repo_name = normalize_repo_name(owner, item)
        if repo_name not in seen:
            normalized.append(repo_name)
            seen.add(repo_name)
    return tuple(normalized)


def build_report_options(
    *,
    owner: str,
    top: int = DEFAULT_TOP,
    since: str | None = None,
    days: int | None = None,
    until: str | None = None,
    all_time: bool = False,
    include_bots: bool = False,
    format: OutputFormat = OutputFormat.markdown,
    output: Path | None = None,
    repos: str | None = None,
    repos_file: Path | None = None,
    exclude: str | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    sort_by: SortBy = SortBy.modifications,
) -> ReportOptions:
    """Build a validated report settings object from raw CLI input."""

    owner_name = owner.strip()
    if days is not None and _has_cli_value(since):
        raise ValueError("--days cannot be combined with --since.")
    if days is not None and all_time:
        raise ValueError("--days cannot be combined with --all-time.")

    now = utc_now()
    until_dt = parse_datetime_input(until, default=now, end_of_day=True)
    if all_time:
        window_kind = WindowKind.all_time
        since_dt = None
        lookback_days = None
    elif _has_cli_value(since):
        window_kind = WindowKind.custom
        since_dt = parse_datetime_input(since, default=None)
        lookback_days = None
    else:
        lookback_days = days if days is not None else DEFAULT_LOOKBACK_DAYS
        window_kind = WindowKind.relative_days
        since_dt = until_dt - timedelta(days=lookback_days)

    return ReportOptions(
        owner=owner_name,
        top=top,
        since=since_dt,
        until=until_dt,
        include_bots=include_bots,
        format=format,
        output=output,
        repos=resolve_repo_filters(owner_name, repos, repos_file),
        exclude_repos=resolve_repo_filters(owner_name, exclude, None),
        concurrency=concurrency,
        timeout_seconds=timeout_seconds,
        sort_by=sort_by,
        window_kind=window_kind,
        lookback_days=lookback_days,
    )


def build_list_repos_options(
    *,
    owner: str,
    include_private: bool = False,
    format: OutputFormat = OutputFormat.markdown,
    output: Path | None = None,
) -> ListReposOptions:
    """Build a validated repository list settings object from raw CLI input."""

    return ListReposOptions(
        owner=owner,
        include_private=include_private,
        format=format,
        output=output,
    )


def build_loc_options(
    *,
    target: str,
    owner: str | None = None,
    ref: str = DEFAULT_LOC_REF,
    format: OutputFormat = OutputFormat.markdown,
    output: Path | None = None,
    timeout_seconds: float = DEFAULT_ARCHIVE_TIMEOUT_SECONDS,
) -> LocOptions:
    """Build a validated line-count settings object from raw CLI input."""

    return LocOptions(
        target=target,
        owner=owner,
        ref=ref,
        format=format,
        output=output,
        timeout_seconds=timeout_seconds,
    )


def model_dump_any(model: BaseModel) -> dict[str, Any]:
    """Small helper for tests and debugging."""

    return model.model_dump()


def _has_cli_value(value: str | None) -> bool:
    """Return whether a CLI option was explicitly supplied with a non-empty value."""

    return value is not None and bool(value.strip())
