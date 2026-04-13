"""Render report results into Markdown, text, HTML, or CSV."""

from __future__ import annotations

import csv
from html import escape
from io import StringIO
from textwrap import wrap

from rich import box
from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from github_report.models import RepoContributorRow, ReportBundle, RepositoryRef, UserContributorRow
from github_report.settings import OutputFormat, SortBy, WindowKind


def format_datetime_for_display(value) -> str:
    """Render a datetime value in a stable UTC format."""

    if value is None:
        return "all history"
    return value.isoformat().replace("+00:00", "Z")


def escape_markdown_cell(value: str) -> str:
    """Escape a value for use inside a Markdown table cell."""

    return value.replace("|", "\\|")


def describe_ranking(sort_by: SortBy) -> str:
    """Return a human-readable ranking description."""

    if sort_by is SortBy.modifications:
        return "modifications first, then commits"
    return "commits first, then modifications"


def describe_window(metadata) -> str:
    """Return a human-readable window description."""

    since_text = format_datetime_for_display(metadata.since)
    until_text = format_datetime_for_display(metadata.until)
    if metadata.window_kind is WindowKind.relative_days:
        return (
            f"last {metadata.lookback_days} {_pluralize_days(metadata.lookback_days)} "
            f"({since_text} to {until_text})"
        )
    if metadata.window_kind is WindowKind.all_time:
        return f"full reachable history (through {until_text})"
    return f"custom window ({since_text} to {until_text})"


def format_repos(repos: tuple[str, ...]) -> str:
    """Render repo names as a stable comma-separated list."""

    return ", ".join(repos)


def display_user(display_name: str, user_name: str) -> str:
    """Render a contributor label with profile name fallback."""

    return display_name or user_name


def _pluralize_days(count: int | None) -> str:
    return "day" if count == 1 else "days"


def render_top_users(report: ReportBundle, *, limit: int, output_format: OutputFormat) -> str:
    """Render the aggregated contributor view."""

    rows = report.top_users[:limit]
    if output_format is OutputFormat.csv:
        return render_top_users_csv(rows)
    if output_format is OutputFormat.html:
        return render_top_users_html(report, rows)
    if output_format is OutputFormat.text:
        return render_top_users_text(report, rows)
    return render_top_users_markdown(report, rows)


def render_repo_breakdown(report: ReportBundle, *, limit: int, output_format: OutputFormat) -> str:
    """Render the per-repository contributor view."""

    rows = report.repo_rows[:limit]
    if output_format is OutputFormat.csv:
        return render_repo_breakdown_csv(rows)
    if output_format is OutputFormat.html:
        return render_repo_breakdown_html(report, rows)
    if output_format is OutputFormat.text:
        return render_repo_breakdown_text(report, rows)
    return render_repo_breakdown_markdown(report, rows)


def render_repo_list(
    repositories: list[RepositoryRef],
    *,
    owner: str,
    output_format: OutputFormat,
    include_private: bool,
) -> str:
    """Render accessible repository metadata."""

    if output_format is OutputFormat.csv:
        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["repo_name", "visibility", "default_branch", "archived"])
        for repo in repositories:
            writer.writerow(
                [
                    repo.full_name,
                    repo_visibility(repo),
                    repo.default_branch or "",
                    str(repo.is_archived).lower(),
                ]
            )
        return buffer.getvalue()
    if output_format is OutputFormat.html:
        return render_repo_list_html(repositories, owner=owner, include_private=include_private)
    if output_format is OutputFormat.text:
        return render_repo_list_text(repositories, owner=owner, include_private=include_private)

    lines = [
        f"# {repo_list_title(owner, include_private=include_private)}",
        "",
        f"Total repositories: {len(repositories)}",
        "",
        "| repo_name | visibility | default_branch | archived |",
        "| --- | --- | --- | ---: |",
    ]
    for repo in repositories:
        lines.append(
            f"| {escape_markdown_cell(repo.full_name)} | {repo_visibility(repo)} | "
            f"{repo.default_branch or ''} | {'yes' if repo.is_archived else 'no'} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_top_users_terminal(report: ReportBundle, *, limit: int) -> RenderableType:
    """Render the aggregated contributor view for interactive terminals."""

    metadata = report.metadata
    table = Table(box=box.ROUNDED, header_style="bold cyan")
    table.add_column("Rank", justify="right", style="bold")
    table.add_column("User", overflow="fold")
    table.add_column("Modifications", justify="right")
    table.add_column("Commits", justify="right")
    table.add_column("Repos", overflow="fold")

    for rank, row in enumerate(report.top_users[:limit], start=1):
        table.add_row(
            str(rank),
            display_user(row.display_name, row.user_name),
            str(row.num_modifications),
            str(row.num_commits),
            format_repos(row.repos),
        )

    return Group(
        Text(f"Top Contributors for {metadata.owner}", style="bold"),
        _build_report_summary_table(report),
        table,
    )


def render_repo_breakdown_terminal(report: ReportBundle, *, limit: int) -> RenderableType:
    """Render the per-repository contributor view for interactive terminals."""

    metadata = report.metadata
    table = Table(box=box.ROUNDED, header_style="bold cyan")
    table.add_column("Rank", justify="right", style="bold")
    table.add_column("User", overflow="fold")
    table.add_column("Repository", overflow="fold")
    table.add_column("Modifications", justify="right")
    table.add_column("Commits", justify="right")

    for rank, row in enumerate(report.repo_rows[:limit], start=1):
        table.add_row(
            str(rank),
            display_user(row.display_name, row.user_name),
            row.repo_name,
            str(row.num_modifications),
            str(row.num_commits),
        )

    return Group(
        Text(f"Repository Breakdown for {metadata.owner}", style="bold"),
        _build_report_summary_table(report),
        table,
    )


def render_repo_list_terminal(
    repositories: list[RepositoryRef],
    *,
    owner: str,
    include_private: bool,
) -> RenderableType:
    """Render repository metadata for interactive terminals."""

    table = Table(box=box.ROUNDED, header_style="bold cyan")
    table.add_column("Repository", overflow="fold")
    table.add_column("Visibility")
    table.add_column("Default Branch")
    table.add_column("Archived", justify="center")

    for repo in repositories:
        table.add_row(
            repo.full_name,
            repo_visibility(repo),
            repo.default_branch or "",
            "yes" if repo.is_archived else "no",
        )

    return Group(
        Text(repo_list_title(owner, include_private=include_private), style="bold"),
        Text(f"Total repositories: {len(repositories)}", style="dim"),
        table,
    )


def render_top_users_csv(rows: list[UserContributorRow]) -> str:
    """Render aggregated contributor rows as CSV."""

    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["rank", "user_name", "num_modifications", "num_commits", "repos"])
    for rank, row in enumerate(rows, start=1):
        writer.writerow(
            [
                rank,
                display_user(row.display_name, row.user_name),
                row.num_modifications,
                row.num_commits,
                format_repos(row.repos),
            ],
        )
    return buffer.getvalue()


def render_repo_breakdown_csv(rows: list[RepoContributorRow]) -> str:
    """Render per-repo contributor rows as CSV."""

    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["rank", "user_name", "repo_name", "num_modifications", "num_commits"])
    for rank, row in enumerate(rows, start=1):
        writer.writerow(
            [
                rank,
                display_user(row.display_name, row.user_name),
                row.repo_name,
                row.num_modifications,
                row.num_commits,
            ],
        )
    return buffer.getvalue()


def render_top_users_text(report: ReportBundle, rows: list[UserContributorRow]) -> str:
    """Render aggregated contributor rows as plain text."""

    entry_lines: list[str] = []
    for rank, row in enumerate(rows, start=1):
        entry_lines.extend(
            _render_text_entry(
                rank=rank,
                title=display_user(row.display_name, row.user_name),
                fields=[
                    ("Modifications", f"{row.num_modifications:,}"),
                    ("Commits", f"{row.num_commits:,}"),
                    ("Repos", format_repos(row.repos) or "-"),
                ],
            )
        )
    return render_text_document(
        title=f"Top Contributors for {report.metadata.owner}",
        summary_rows=build_summary_rows(report),
        body_title="Contributors",
        body_lines=entry_lines,
    )


def render_repo_breakdown_text(report: ReportBundle, rows: list[RepoContributorRow]) -> str:
    """Render per-repo contributor rows as plain text."""

    entry_lines: list[str] = []
    for rank, row in enumerate(rows, start=1):
        entry_lines.extend(
            _render_text_entry(
                rank=rank,
                title=display_user(row.display_name, row.user_name),
                fields=[
                    ("Repository", row.repo_name),
                    ("Modifications", f"{row.num_modifications:,}"),
                    ("Commits", f"{row.num_commits:,}"),
                ],
            )
        )
    return render_text_document(
        title=f"Repository Breakdown for {report.metadata.owner}",
        summary_rows=build_summary_rows(report),
        body_title="Contributor Rows",
        body_lines=entry_lines,
    )


def render_repo_list_text(
    repositories: list[RepositoryRef],
    *,
    owner: str,
    include_private: bool,
) -> str:
    """Render repository metadata as plain text."""

    entry_lines: list[str] = []
    for index, repo in enumerate(repositories, start=1):
        entry_lines.extend(
            _render_text_entry(
                rank=index,
                title=repo.full_name,
                fields=[
                    ("Visibility", repo_visibility(repo)),
                    ("Default branch", repo.default_branch or "-"),
                    ("Archived", "yes" if repo.is_archived else "no"),
                ],
            )
        )
    return render_text_document(
        title=repo_list_title(owner, include_private=include_private),
        summary_rows=[("Total repositories", str(len(repositories)))],
        body_title="Repositories",
        body_lines=entry_lines,
    )


def render_top_users_markdown(report: ReportBundle, rows: list[UserContributorRow]) -> str:
    """Render aggregated contributor rows as Markdown."""

    metadata = report.metadata
    lines = [
        f"# Top Contributors for {metadata.owner}",
        "",
        f"- Generated at: `{format_datetime_for_display(metadata.generated_at)}`",
        f"- Window: `{describe_window(metadata)}`",
        f"- Repositories scanned: `{metadata.repos_scanned}` of `{metadata.repos_requested}`",
        f"- Commits scanned: `{metadata.commits_scanned}`",
        f"- Branch scope: `{metadata.branch_scope}`",
        f"- Bots included: `{'yes' if metadata.include_bots else 'no'}`",
        f"- Ranking metric: `{describe_ranking(metadata.sort_by)}`",
        "",
        "| rank | user_name | num_modifications | num_commits | repos |",
        "| ---: | --- | ---: | ---: | --- |",
    ]
    for rank, row in enumerate(rows, start=1):
        lines.append(
            f"| {rank} | {escape_markdown_cell(display_user(row.display_name, row.user_name))} | {row.num_modifications} | "
            f"{row.num_commits} | {escape_markdown_cell(format_repos(row.repos))} |",
        )
    lines.append("")
    return "\n".join(lines)


def render_repo_breakdown_markdown(report: ReportBundle, rows: list[RepoContributorRow]) -> str:
    """Render per-repo contributor rows as Markdown."""

    metadata = report.metadata
    lines = [
        f"# Repository Breakdown for {metadata.owner}",
        "",
        f"- Generated at: `{format_datetime_for_display(metadata.generated_at)}`",
        f"- Window: `{describe_window(metadata)}`",
        f"- Repositories scanned: `{metadata.repos_scanned}` of `{metadata.repos_requested}`",
        f"- Commits scanned: `{metadata.commits_scanned}`",
        f"- Branch scope: `{metadata.branch_scope}`",
        f"- Bots included: `{'yes' if metadata.include_bots else 'no'}`",
        f"- Ranking metric: `{describe_ranking(metadata.sort_by)}`",
        "",
        "| rank | user_name | repo_name | num_modifications | num_commits |",
        "| ---: | --- | --- | ---: | ---: |",
    ]
    for rank, row in enumerate(rows, start=1):
        lines.append(
            f"| {rank} | {escape_markdown_cell(display_user(row.display_name, row.user_name))} | "
            f"{escape_markdown_cell(row.repo_name)} | {row.num_modifications} | "
            f"{row.num_commits} |",
        )
    lines.append("")
    return "\n".join(lines)


def render_top_users_html(report: ReportBundle, rows: list[UserContributorRow]) -> str:
    """Render aggregated contributor rows as HTML."""

    metadata = report.metadata
    return render_html_document(
        title=f"Top Contributors for {metadata.owner}",
        summary_rows=build_summary_rows(report),
        headers=[
            ("rank", "num"),
            ("user_name", "text"),
            ("num_modifications", "num"),
            ("num_commits", "num"),
            ("repos", "text"),
        ],
        rows=[
            [
                str(rank),
                display_user(row.display_name, row.user_name),
                str(row.num_modifications),
                str(row.num_commits),
                format_repos(row.repos),
            ]
            for rank, row in enumerate(rows, start=1)
        ],
    )


def render_repo_breakdown_html(report: ReportBundle, rows: list[RepoContributorRow]) -> str:
    """Render per-repository contributor rows as HTML."""

    metadata = report.metadata
    return render_html_document(
        title=f"Repository Breakdown for {metadata.owner}",
        summary_rows=build_summary_rows(report),
        headers=[
            ("rank", "num"),
            ("user_name", "text"),
            ("repo_name", "text"),
            ("num_modifications", "num"),
            ("num_commits", "num"),
        ],
        rows=[
            [
                str(rank),
                display_user(row.display_name, row.user_name),
                row.repo_name,
                str(row.num_modifications),
                str(row.num_commits),
            ]
            for rank, row in enumerate(rows, start=1)
        ],
    )


def render_repo_list_html(
    repositories: list[RepositoryRef],
    *,
    owner: str,
    include_private: bool,
) -> str:
    """Render repository metadata as HTML."""

    return render_html_document(
        title=repo_list_title(owner, include_private=include_private),
        summary_rows=[("Total repositories", str(len(repositories)))],
        headers=[
            ("repo_name", "text"),
            ("visibility", "text"),
            ("default_branch", "text"),
            ("archived", "text"),
        ],
        rows=[
            [
                repo.full_name,
                repo_visibility(repo),
                repo.default_branch or "",
                "yes" if repo.is_archived else "no",
            ]
            for repo in repositories
        ],
    )


def repo_visibility(repository: RepositoryRef) -> str:
    """Render repository visibility in a stable, user-facing form."""

    return "private" if repository.is_private else "public"


def repo_list_title(owner: str, *, include_private: bool) -> str:
    """Return the list title that matches the selected visibility scope."""

    if include_private:
        return f"Accessible Repositories for {owner}"
    return f"Public Repositories for {owner}"


def build_summary_rows(report: ReportBundle) -> list[tuple[str, str]]:
    """Build summary rows shared by HTML and terminal renderers."""

    metadata = report.metadata
    return [
        ("Generated at", format_datetime_for_display(metadata.generated_at)),
        ("Window", describe_window(metadata)),
        ("Repositories scanned", f"{metadata.repos_scanned} of {metadata.repos_requested}"),
        ("Commits scanned", str(metadata.commits_scanned)),
        ("Branch scope", metadata.branch_scope),
        ("Bots included", "yes" if metadata.include_bots else "no"),
        ("Ranking metric", describe_ranking(metadata.sort_by)),
    ]


def render_text_document(
    *,
    title: str,
    summary_rows: list[tuple[str, str]],
    body_title: str,
    body_lines: list[str],
) -> str:
    """Render a plain-text report for copy-paste into chat and editors."""

    lines = [
        title,
        "=" * len(title),
        "",
    ]
    if summary_rows:
        lines.extend(_render_text_fields(summary_rows))
        lines.append("")
    lines.extend(
        [
            body_title,
            "-" * len(body_title),
            "",
        ]
    )
    if body_lines:
        lines.extend(body_lines)
    else:
        lines.append("(no rows)")
    lines.append("")
    return "\n".join(lines)


def _render_text_entry(
    *,
    rank: int,
    title: str,
    fields: list[tuple[str, str]],
) -> list[str]:
    lines = [f"{rank:>2}. {title}"]
    lines.extend(_render_text_fields(fields, base_indent="    "))
    lines.append("")
    return lines


def _render_text_fields(
    fields: list[tuple[str, str]],
    *,
    base_indent: str = "",
    width: int = 100,
) -> list[str]:
    label_width = max((len(label) for label, _ in fields), default=0)
    lines: list[str] = []
    for label, value in fields:
        prefix = f"{base_indent}{label:<{label_width}} : "
        continuation_prefix = " " * len(prefix)
        wrapped_values = wrap(
            value,
            width=max(20, width - len(prefix)),
            break_long_words=False,
            break_on_hyphens=False,
        ) or [""]
        lines.append(f"{prefix}{wrapped_values[0]}")
        for continuation in wrapped_values[1:]:
            lines.append(f"{continuation_prefix}{continuation}")
    return lines


def render_html_document(
    *,
    title: str,
    summary_rows: list[tuple[str, str]],
    headers: list[tuple[str, str]],
    rows: list[list[str]],
) -> str:
    """Render a simple HTML report that pastes cleanly into Word."""

    summary_html = "\n".join(
        (f"      <tr><th>{escape(label)}</th><td>{escape(value)}</td></tr>")
        for label, value in summary_rows
    )
    header_html = "".join(
        f'<th class="{escape(css_class)}">{escape(label)}</th>' for label, css_class in headers
    )
    body_html = "\n".join(
        "      <tr>"
        + "".join(
            f'<td class="{escape(headers[index][1])}">{escape(value)}</td>'
            for index, value in enumerate(row)
        )
        + "</tr>"
        for row in rows
    )
    return "\n".join(
        [
            "<!DOCTYPE html>",
            '<html lang="en">',
            "<head>",
            '  <meta charset="utf-8" />',
            f"  <title>{escape(title)}</title>",
            "  <style>",
            "    body { font-family: Aptos, 'Segoe UI', sans-serif; margin: 24px; color: #1f2328; }",
            "    h1 { margin-bottom: 16px; }",
            "    table { border-collapse: collapse; width: 100%; margin: 0 0 20px; }",
            "    th, td { border: 1px solid #d0d7de; padding: 8px 10px; vertical-align: top; text-align: left; }",
            "    th { background: #f6f8fa; }",
            "    .num { text-align: right; white-space: nowrap; }",
            "    .text { text-align: left; }",
            "    .summary { width: auto; min-width: 420px; }",
            "    .summary th { width: 220px; }",
            "  </style>",
            "</head>",
            "<body>",
            f"  <h1>{escape(title)}</h1>",
            '  <table class="summary">',
            "    <tbody>",
            summary_html,
            "    </tbody>",
            "  </table>",
            "  <table>",
            "    <thead>",
            f"      <tr>{header_html}</tr>",
            "    </thead>",
            "    <tbody>",
            body_html,
            "    </tbody>",
            "  </table>",
            "</body>",
            "</html>",
            "",
        ]
    )


def _build_report_summary_table(report: ReportBundle) -> Table:
    """Build a compact metadata table shared by terminal renderers."""

    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold")
    table.add_column()
    for label, value in build_summary_rows(report):
        table.add_row(label, value)
    return table
