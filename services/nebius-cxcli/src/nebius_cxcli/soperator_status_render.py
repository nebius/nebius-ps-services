"""Terminal-safe presentation of ephemeral Soperator health observations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .soperator_status_checks import CHECK_STYLES, CheckHistory
from .soperator_status_health import HEALTH_STYLES, StatusHealthReport, safe_text


def print_installed_release(console: Console, release: Mapping[str, Any]) -> None:
    version = str(release.get("chart_version") or release.get("version") or "unknown")
    state = str(release.get("status") or "unknown")
    console.print(Text(f"Installed Soperator: {safe_text(version)} ({safe_text(state)})"))
    app = str(release.get("app_version") or "")
    if app and app.split("+", 1)[0] != version.split("+", 1)[0]:
        console.print(
            Text(
                f"Version discrepancy: app {safe_text(app)}, chart {safe_text(version)}",
                style="yellow",
            )
        )


def print_health_table(
    console: Console, report: StatusHealthReport, *, show_checks: bool = False
) -> None:
    table = Table(expand=False)
    table.add_column("Component", overflow="fold")
    table.add_column("K8s Ready", justify="right", no_wrap=True)
    table.add_column("Health", no_wrap=True)
    table.add_column("Details", overflow="fold")
    for row in report.components:
        count = (
            f"{row.ready}/{row.expected}"
            if row.ready is not None and row.expected is not None
            else "—"
        )
        table.add_row(
            Text(safe_text(row.component)),
            Text(count),
            Text(row.state, style=HEALTH_STYLES[row.state]),
            Text("" if row.state == "Healthy" else safe_text(row.detail)),
        )
    console.print(table)
    print_check_history(console, report.history, show_checks=show_checks)
    for issue in dict.fromkeys(report.issues):
        console.print(Text(f"Collection: {safe_text(issue)}", style="magenta"))


def print_check_history(
    console: Console, history: CheckHistory, *, show_checks: bool = False
) -> None:
    if history.state == "unavailable":
        console.print(
            Text(
                f"Warning: recorded check history unavailable — {safe_text(history.detail)}",
                style="yellow",
            )
        )
        return
    if history.state in {"not_installed", "not_checked"}:
        label = "Not installed" if history.state == "not_installed" else "Not checked"
        console.print(Text(f"Recorded checks: {label} — {safe_text(history.detail)}", style="blue"))
        return
    console.print(Text(f"Recorded checks (history): {history.summary}", style="blue"))
    selected = (
        list(history.records) if show_checks else [r for r in history.records if r.needs_attention]
    )
    total = len(selected)
    if not show_checks:
        selected = selected[:5]
    if not selected:
        return
    table = Table()
    for column in ("Check", "Recorded result", "Schedule", "Timestamps"):
        table.add_column(column, overflow="fold")
    for check in selected:
        table.add_row(
            Text(safe_text(check.name)),
            Text(check.result, style=CHECK_STYLES[check.result]),
            Text(
                "Suspended" if check.suspended else "Enabled",
                style="dim" if check.suspended else "",
            ),
            Text("; ".join(f"{label}: {safe_text(value)}" for label, value in check.timestamps)),
        )
    console.print(table)
    if len(selected) < total:
        console.print(
            Text(
                f"{total - len(selected)} more recorded check issues; use --show-checks to show all records."
            )
        )


def print_overall_health(console: Console, state: str, detail: str = "") -> None:
    text = Text("Overall health: ")
    text.append(state, style=HEALTH_STYLES[state])
    if detail:
        text.append(f" — {safe_text(detail)}")
    console.print(text)
