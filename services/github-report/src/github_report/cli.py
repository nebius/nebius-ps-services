"""CLI entrypoints for github-report."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from pathlib import Path
from textwrap import dedent
from typing import Annotated, NoReturn

import typer
from click.core import ParameterSource
from pydantic import ValidationError
from rich.console import Console, RenderableType

from github_report import __version__
from github_report.models import LocReport, ReportBundle
from github_report.renderers import (
    render_loc_report,
    render_loc_report_terminal,
    render_repo_breakdown,
    render_repo_breakdown_terminal,
    render_repo_list,
    render_repo_list_terminal,
    render_top_users,
    render_top_users_terminal,
)
from github_report.services.github_client import GitHubClientError
from github_report.services.reporting import GitHubReportService
from github_report.settings import (
    DEFAULT_ARCHIVE_TIMEOUT_SECONDS,
    DEFAULT_CONCURRENCY,
    DEFAULT_LOC_REF,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_TOP,
    LocOptions,
    OutputFormat,
    ReportOptions,
    SortBy,
    build_list_repos_options,
    build_loc_options,
    build_report_options,
)

OUTPUT_FORMAT_GUIDE = dedent(
    """
    Output formats:

      markdown (default): raw Markdown; inferred from .md or .markdown. When
      writing to stdout in an interactive terminal, the CLI renders a rich table
      instead of raw Markdown.

      text: plain text; inferred from .txt.

      html: Word-friendly HTML; inferred from .html or .htm.

      csv: comma-separated values; inferred from .csv.
    """
).strip()

OUTPUT_FORMAT_HELP = (
    "Output format. Supported values: markdown, text, html, csv. "
    "When --output is omitted, markdown renders as a rich table in interactive terminals. "
    "When --output is set and --format is omitted, infer from .md/.markdown, .txt, "
    ".html/.htm, or .csv."
)

OUTPUT_FILE_HELP = (
    "Write the report to a file instead of stdout. "
    "With --format omitted, infer markdown from .md/.markdown, text from .txt, "
    "html from .html/.htm, and csv from .csv."
)

ROOT_EPILOG = dedent(
    f"""
    Examples:

      github-report top-users --owner nebius

      github-report top-users --owner lm-academy --top 5 --days 60 --output report.txt

      github-report top-users --owner nebius --exclude old-app --output report.txt

      github-report top-users --owner nebius --output report.html

      github-report top-users --owner nebius --format csv --output report.csv

      github-report top-users --owner nebius --repos app-a,app-b --since 2026-01-01

      github-report list-repos --owner nebius --output repos.txt

      github-report loc nebius-ps-services

      github-report loc nebius-ps-services/services/nebius-cxcli/

    {OUTPUT_FORMAT_GUIDE}
    """
).strip()

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=(
        "Rank GitHub contributors, list repositories, and count repository lines of "
        "code. Owner-wide commands require `--owner`; `loc` accepts a repo/path target."
    ),
    epilog=ROOT_EPILOG,
)
console = Console(stderr=True)
output_console = Console()
service = GitHubReportService()

TOP_USERS_EPILOG = dedent(
    f"""
    Examples:

      github-report top-users --owner nebius

      github-report top-users --owner lm-academy --top 5 --days 60

      github-report top-users --owner nebius --exclude old-app --output report.txt

      github-report top-users --owner nebius --output report.md

      github-report top-users --owner nebius --output report.html

      github-report top-users --owner nebius --format csv --output report.csv

      github-report top-users --owner nebius --repos app-a,app-b --since 2026-01-01

      github-report top-users --owner nebius --per-repo --output report.html

    {OUTPUT_FORMAT_GUIDE}
    """
).strip()

LIST_REPOS_EPILOG = dedent(
    f"""
    Examples:

      github-report list-repos --owner nebius

      github-report list-repos --owner nebius --all

      github-report list-repos --owner nebius --output repos.txt

      github-report list-repos --owner nebius --output repos.html

      github-report list-repos --owner nebius --format csv --output repos.csv

      github-report list-repos --owner lm-academy --output repos.txt

      github-report list-repos --owner nebius --format html --output repos.html

    {OUTPUT_FORMAT_GUIDE}
    """
).strip()

LOC_EPILOG = dedent(
    f"""
    Examples:

      github-report loc nebius-ps-services

      github-report loc nebius-ps-services/services/nebius-cxcli/

      github-report loc nebius/nebius-ps-services/services/nebius-cxcli/

      github-report loc --owner nebius nebius-ps-services/services/nebius-cxcli/

      github-report loc nebius-ps-services --format csv --output loc.csv

    Targets can be repo, repo/path, owner/repo, or owner/repo/path. Bare repo names
    are resolved by exact GitHub repository-name search; use --owner or an
    owner-qualified target if the name is ambiguous.

    {OUTPUT_FORMAT_GUIDE}
    """
).strip()


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def callback(
    ctx: typer.Context,
    owner: Annotated[
        str | None,
        typer.Option(
            "--owner",
            help=(
                "Default GitHub owner for commands that accept an owner. "
                "Contributor reports and repository listings require an owner; "
                "`loc` can also resolve one from an owner-qualified target."
            ),
            show_default=False,
        ),
    ] = None,
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            callback=_version_callback,
            expose_value=False,
            help="Show the installed github-report version and exit.",
            is_eager=True,
        ),
    ] = None,
) -> None:
    """CLI callback."""

    ctx.obj = {"owner": owner}


@app.command("top-users", epilog=TOP_USERS_EPILOG)
def top_users(
    ctx: typer.Context,
    owner: Annotated[
        str | None,
        typer.Option(
            help=(
                "GitHub owner to scan. Supports organizations and personal accounts. "
                "Overrides the root `--owner`."
            ),
            show_default=False,
        ),
    ] = None,
    top: Annotated[int, typer.Option(help="Number of rows to emit.")] = DEFAULT_TOP,
    since: Annotated[
        str | None,
        typer.Option(help="Start date or timestamp in UTC. Overrides the default --days window."),
    ] = None,
    days: Annotated[
        int | None,
        typer.Option(
            "--days",
            min=1,
            help="Relative lookback window in days. Defaults to 30 when --since is omitted.",
            show_default="30",
        ),
    ] = None,
    until: Annotated[
        str | None,
        typer.Option(help="End date or timestamp in UTC. Defaults to now."),
    ] = None,
    all_time: Annotated[
        bool,
        typer.Option(
            "--all-time",
            help="Ignore --since and scan all reachable default-branch history.",
        ),
    ] = False,
    repos: Annotated[
        str | None,
        typer.Option(help="Comma-separated repo names or owner/repo identifiers."),
    ] = None,
    exclude: Annotated[
        str | None,
        typer.Option(help="Comma-separated repo names or owner/repo identifiers to exclude."),
    ] = None,
    repos_file: Annotated[
        Path | None,
        typer.Option(
            exists=True,
            dir_okay=False,
            file_okay=True,
            readable=True,
            resolve_path=True,
            help="Text file with one repo per line.",
        ),
    ] = None,
    sort_by: Annotated[SortBy, typer.Option(help="Ranking metric.")] = SortBy.modifications,
    include_bots: Annotated[
        bool,
        typer.Option(
            "--include-bots/--exclude-bots", help="Include bot accounts in ranking results."
        ),
    ] = False,
    format: Annotated[
        OutputFormat,
        typer.Option(help=OUTPUT_FORMAT_HELP),
    ] = OutputFormat.markdown,
    per_repo: Annotated[
        bool,
        typer.Option(
            "--per-repo/--aggregate",
            help="Return one row per user and repo instead of aggregating across all selected repos.",
        ),
    ] = False,
    output: Annotated[
        Path | None,
        typer.Option(help=OUTPUT_FILE_HELP),
    ] = None,
    concurrency: Annotated[
        int,
        typer.Option(help="Concurrent GraphQL requests. Increase carefully."),
    ] = DEFAULT_CONCURRENCY,
    timeout_seconds: Annotated[
        float,
        typer.Option(help="Per-request timeout in seconds."),
    ] = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """Emit top contributors across selected repositories for an owner."""

    resolved_format = _resolve_output_format(ctx, format, output)
    options = _build_report_options(
        owner=_resolve_owner(ctx, owner),
        top=top,
        since=since,
        days=days,
        until=until,
        all_time=all_time,
        include_bots=include_bots,
        format=resolved_format,
        output=output,
        repos=repos,
        repos_file=repos_file,
        exclude=exclude,
        concurrency=concurrency,
        timeout_seconds=timeout_seconds,
        sort_by=sort_by,
    )
    report = _run_report(options)
    content = (
        render_repo_breakdown(report, limit=options.top, output_format=options.format)
        if per_repo
        else render_top_users(report, limit=options.top, output_format=options.format)
    )
    renderable = (
        _build_terminal_renderable(
            report,
            limit=options.top,
            per_repo=per_repo,
            output_format=options.format,
        )
        if options.output is None
        else None
    )
    _write_output(content, options.output, renderable=renderable)


@app.command("list-repos", epilog=LIST_REPOS_EPILOG)
def list_repos(
    ctx: typer.Context,
    owner: Annotated[
        str | None,
        typer.Option(
            help=(
                "GitHub owner to inspect. Supports organizations and personal accounts. "
                "Overrides the root `--owner`."
            ),
            show_default=False,
        ),
    ] = None,
    all_repos: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Include private repositories in addition to public repositories.",
        ),
    ] = False,
    format: Annotated[
        OutputFormat,
        typer.Option(help=OUTPUT_FORMAT_HELP),
    ] = OutputFormat.markdown,
    output: Annotated[
        Path | None,
        typer.Option(help=OUTPUT_FILE_HELP),
    ] = None,
) -> None:
    """List public repositories for an owner; use `--all` to include private ones."""

    try:
        options = build_list_repos_options(
            owner=_resolve_owner(ctx, owner),
            include_private=all_repos,
            format=_resolve_output_format(ctx, format, output),
            output=output,
        )
        repositories = service.list_repositories(options)
    except (ValidationError, ValueError) as exc:
        _exit_with_error(str(exc), code=2)
    except GitHubClientError as exc:
        _exit_with_error(str(exc), code=1)

    _write_output(
        render_repo_list(
            repositories,
            owner=options.owner,
            output_format=options.format,
            include_private=options.include_private,
        ),
        options.output,
        renderable=(
            render_repo_list_terminal(
                repositories,
                owner=options.owner,
                include_private=options.include_private,
            )
            if options.output is None and options.format is OutputFormat.markdown
            else None
        ),
    )


@app.command("loc", epilog=LOC_EPILOG)
def loc(
    ctx: typer.Context,
    target: Annotated[
        str,
        typer.Argument(
            help=(
                "Repository target as repo, repo/path, owner/repo, or owner/repo/path. "
                "A path scope is counted as a standalone project within the repository."
            )
        ),
    ],
    owner: Annotated[
        str | None,
        typer.Option(
            help=("GitHub owner for bare repo/path targets. Overrides the root `--owner`."),
            show_default=False,
        ),
    ] = None,
    ref: Annotated[
        str,
        typer.Option(
            "--ref",
            help="Git branch, tag, or SHA to inspect.",
            show_default=DEFAULT_LOC_REF,
        ),
    ] = DEFAULT_LOC_REF,
    format: Annotated[
        OutputFormat,
        typer.Option(help=OUTPUT_FORMAT_HELP),
    ] = OutputFormat.markdown,
    output: Annotated[
        Path | None,
        typer.Option(help=OUTPUT_FILE_HELP),
    ] = None,
    timeout_seconds: Annotated[
        float,
        typer.Option(help="Repository archive download timeout in seconds."),
    ] = DEFAULT_ARCHIVE_TIMEOUT_SECONDS,
) -> None:
    """Count physical source lines in a repository or folder, defaulting to `main`."""

    resolved_format = _resolve_output_format(ctx, format, output)
    try:
        options = build_loc_options(
            target=target,
            owner=_resolve_optional_owner(ctx, owner),
            ref=ref,
            format=resolved_format,
            output=output,
            timeout_seconds=timeout_seconds,
        )
    except (ValidationError, ValueError) as exc:
        _exit_with_error(str(exc), code=2)

    report = _run_loc_report(options)
    _write_output(
        render_loc_report(report, output_format=options.format),
        options.output,
        renderable=(
            render_loc_report_terminal(report)
            if options.output is None and options.format is OutputFormat.markdown
            else None
        ),
    )


def main() -> None:
    """Invoke the Typer application."""

    app()


def _build_report_options(**kwargs) -> ReportOptions:
    try:
        return build_report_options(**kwargs)
    except (ValidationError, ValueError) as exc:
        _exit_with_error(str(exc), code=2)


def _run_report(options: ReportOptions) -> ReportBundle:
    try:
        with _status_context(_build_report_status_message(options)):
            return asyncio.run(service.build_report(options))
    except (GitHubClientError, ValueError) as exc:
        _exit_with_error(str(exc), code=1)


def _run_loc_report(options: LocOptions) -> LocReport:
    try:
        with _status_context(_build_loc_status_message(options)):
            return service.build_loc_report(options)
    except (GitHubClientError, ValueError) as exc:
        _exit_with_error(str(exc), code=1)


def _write_output(
    content: str,
    output_path: Path | None,
    *,
    renderable: RenderableType | None = None,
) -> None:
    if output_path is None:
        if renderable is not None and output_console.is_terminal:
            output_console.print(renderable)
            return
        typer.echo(content.rstrip("\n"))
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    console.print(f"[green]Wrote[/green] {output_path}")


def _exit_with_error(message: str, *, code: int) -> NoReturn:
    console.print(f"[red]Error:[/red] {message}")
    raise typer.Exit(code=code)


def _build_report_status_message(options: ReportOptions) -> str:
    selected_repo_count = len(options.repos)
    excluded_repo_count = len(options.exclude_repos)
    repo_scope = (
        f"{selected_repo_count} selected {_pluralize_repos(selected_repo_count)}"
        if selected_repo_count
        else "all accessible repos"
    )
    if excluded_repo_count:
        repo_scope = (
            f"{repo_scope}, excluding {excluded_repo_count} {_pluralize_repos(excluded_repo_count)}"
        )
    return f"Collecting GitHub activity for {options.owner} ({repo_scope})..."


def _build_loc_status_message(options: LocOptions) -> str:
    owner_prefix = f"{options.owner}/" if options.owner else ""
    return f"Counting lines of code for {owner_prefix}{options.target}@{options.ref}..."


def _status_context(message: str):
    if not console.is_terminal:
        return nullcontext()
    try:
        return console.status(message, spinner="dots", transient=True)
    except TypeError:
        return console.status(message, spinner="dots")


def _pluralize_repos(count: int) -> str:
    return "repo" if count == 1 else "repos"


def _resolve_owner(ctx: typer.Context, command_owner: str | None) -> str:
    if command_owner:
        return command_owner
    if isinstance(ctx.obj, dict) and ctx.obj.get("owner"):
        return str(ctx.obj["owner"])
    _exit_with_error(
        "Missing required option '--owner'. Provide a GitHub organization or personal account.",
        code=2,
    )


def _resolve_optional_owner(ctx: typer.Context, command_owner: str | None) -> str | None:
    if command_owner:
        return command_owner
    if isinstance(ctx.obj, dict) and ctx.obj.get("owner"):
        return str(ctx.obj["owner"])
    return None


def _resolve_output_format(
    ctx: typer.Context,
    output_format: OutputFormat,
    output_path: Path | None,
) -> OutputFormat:
    if output_path is None:
        return output_format
    if ctx.get_parameter_source("format") is not ParameterSource.DEFAULT:
        return output_format
    inferred_format = _infer_output_format_from_path(output_path)
    return inferred_format or output_format


def _infer_output_format_from_path(output_path: Path) -> OutputFormat | None:
    suffix = output_path.suffix.lower()
    if suffix == ".csv":
        return OutputFormat.csv
    if suffix in {".htm", ".html"}:
        return OutputFormat.html
    if suffix in {".md", ".markdown"}:
        return OutputFormat.markdown
    if suffix == ".txt":
        return OutputFormat.text
    return None


def _build_terminal_renderable(
    report: ReportBundle,
    *,
    limit: int,
    per_repo: bool,
    output_format: OutputFormat,
) -> RenderableType | None:
    if output_format is not OutputFormat.markdown:
        return None
    if per_repo:
        return render_repo_breakdown_terminal(report, limit=limit)
    return render_top_users_terminal(report, limit=limit)
