"""Sanitized Markdown report rendering for the three-tier lifecycle."""

from __future__ import annotations

import shlex
from typing import Any

from three_tier_semantics import SCENARIO


RESOURCE_KINDS = ("containers", "networks", "volumes", "images")


def markdown_escape(value: object) -> str:
    return (
        str(value)
        .replace("`", "'")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def recommended_fix(source: object, issue: object) -> str:
    return (
        "Inspect the owned Playwright report and trace, repair the proven owner, "
        "then start a new trial. Keep earlier failed evidence unchanged."
    )


def report_text(state: dict[str, Any]) -> str:
    verification_root = shlex.quote(str(state["verification_root"]))
    lines = [
        "# Agentic SDLC Three-Tier Live Report",
        "",
        "## Summary",
        "",
        f"- Verification ID: `{markdown_escape(state['verification_id'])}`",
        f"- Scenario: `{SCENARIO}`",
        f"- Lifecycle status: {markdown_escape(state['status'])}",
        f"- Test result: {markdown_escape(state.get('result', 'PENDING'))}",
        f"- Created at: {markdown_escape(state['created_at'])}",
        f"- Updated at: {markdown_escape(state['updated_at'])}",
        "",
        "## Environment",
        "",
        f"- Docker Engine: `{markdown_escape(state['environment']['docker_engine'])}`",
        f"- Docker Compose: `{markdown_escape(state['environment']['docker_compose'])}`",
        f"- Git: `{markdown_escape(state['environment']['git'])}`",
        f"- Browser: `{markdown_escape(state['environment']['browser_name'])}`",
        f"- Dedicated browser instance: `{markdown_escape(state['browser_instance']['status'])}`",
        f"- Headless browser: `{markdown_escape(state['environment']['headless_browser'])}`",
        f"- Project root: `{markdown_escape(state['project_root'])}`",
        f"- Report path: `{markdown_escape(state['report_path'])}`",
        "",
        "## Headless browser stages",
        "",
        "| Stage | Outcome | Owned receipt |",
        "| --- | --- | --- |",
    ]
    attempts = state.get("browser_stages", [])
    for attempt in attempts:
        lines.append(f"| {markdown_escape(attempt['stage'])} | {markdown_escape(attempt['outcome'])} | `{markdown_escape(attempt['path'])}` |")
    if not attempts:
        lines.append("| pending | NOT_RUN | pending |")
    lines.extend(["", "- Headless execution does not require desktop capture, an unlocked screen, or a foreground window.",
                  "- Locked agent continuation: UNVERIFIED unless a separate observed lock-interval report is attached."])
    lines.extend([
        "",
        "## Layer inventory",
        "",
        "| Layer | Runtime | Endpoint |",
        "| --- | --- | --- |",
        f"| Frontend GUI | {markdown_escape(state['environment']['browser_name'])} via Playwright Test | `{markdown_escape(state['endpoints'].get('web', 'pending'))}` |",
        f"| Web/API server | Docker Compose web container | `{markdown_escape(state['endpoints'].get('api', 'pending'))}` |",
        f"| PostgreSQL database | Docker Compose database container | `{markdown_escape(state['endpoints'].get('database', 'internal only; pending'))}` |",
        "",
        "## Runtime endpoints",
        "",
        f"- Web: `{markdown_escape(state['endpoints'].get('web', 'pending'))}`",
        f"- API: `{markdown_escape(state['endpoints'].get('api', 'pending'))}`",
        f"- Health: `{markdown_escape(state['endpoints'].get('health', 'pending'))}`",
        "- Database: Compose-internal only; no host-published database port.",
        "",
        "## Git identity",
        "",
        f"- Baseline SHA: `{markdown_escape(state['git'].get('baseline_sha') or 'pending')}`",
        f"- Promoted SHA: `{markdown_escape(state['git'].get('promoted_sha') or 'pending')}`",
        "",
        "## Phase results",
        "",
        "| Phase | Status | Summary | Evidence count |",
        "| --- | --- | --- | ---: |",
    ])
    if state["phases"]:
        for phase in state["phases"]:
            lines.append(
                f"| {markdown_escape(phase['phase'])} | {markdown_escape(phase['status'])} | "
                f"{markdown_escape(phase['summary'])} | {len(phase['evidence'])} |"
            )
    else:
        lines.append("| pending | NOT_RUN | No phase evidence recorded yet. | 0 |")
    lines.extend(["", "## Validation commands", ""])
    validations = state.get("validations", [])
    if validations:
        lines.extend(
            [
                "| Command | Status | Summary |",
                "| --- | --- | --- |",
            ]
        )
        for validation in validations:
            lines.append(
                f"| `{markdown_escape(validation['command'])}` | "
                f"{markdown_escape(validation['status'])} | "
                f"{markdown_escape(validation['summary'])} |"
            )
    else:
        lines.append("- NOT_RUN: No sanitized validation command was recorded.")

    issue_rows = [
        (phase["phase"], phase["status"], phase["summary"])
        for phase in state.get("phases", [])
        if phase.get("status") in {"FAIL", "PARTIAL", "NOT_RUN"}
    ]
    issue_rows.extend(
        (
            validation["command"],
            validation["status"],
            validation["summary"],
        )
        for validation in validations
        if validation.get("status") in {"FAIL", "PARTIAL", "NOT_RUN"}
    )
    lines.extend(["", "## Top issues and recommended fixes", ""])
    if issue_rows:
        lines.extend(
            [
                "| Source | Status | Issue | Recommended fix |",
                "| --- | --- | --- | --- |",
            ]
        )
        for source, status, issue in issue_rows:
            lines.append(
                f"| {markdown_escape(source)} | {markdown_escape(status)} | "
                f"{markdown_escape(issue)} | "
                f"{markdown_escape(recommended_fix(source, issue))} |"
            )
    else:
        lines.append("- None recorded.")
    semantic = state.get("semantic_summary")
    lines.extend(["", "## Layer results", ""])
    if isinstance(semantic, dict):
        lines.extend(
            [
                "| Layer | Status |",
                "| --- | --- |",
            ]
        )
        for name, status in semantic["layers"].items():
            lines.append(f"| {markdown_escape(name)} | {markdown_escape(status)} |")
        lines.extend(["", "## Test results", ""])
        lines.extend(
            [
                "| Test class | Status | Assertions | Evidence count |",
                "| --- | --- | ---: | ---: |",
            ]
        )
        for name, result in semantic["tests"].items():
            lines.append(
                f"| {markdown_escape(name)} | {markdown_escape(result['status'])} | "
                f"{result['assertions']} | {result['evidence_count']} |"
            )
        gui = semantic["gui_uat"]
        lines.extend(
            [
                "",
                "## GUI UAT",
                "",
                f"- Status: {markdown_escape(gui['status'])}",
                f"- Harness: `{markdown_escape(gui['harness'])}`",
                f"- Browser: `{markdown_escape(gui['browser'])}`",
                f"- Ordered steps: {gui['step_count']}",
                f"- Screenshots: {gui['screenshot_count']}",
                f"- API/database correlation: {'PASS' if gui['api_db_correlated'] else 'FAIL'}",
                f"- Restart persistence: {'PASS' if gui['restart_persistence'] else 'FAIL'}",
            ]
        )
    else:
        lines.append("- NOT_RUN: Semantic layer results are pending.")
        lines.extend(
            [
                "",
                "## Test results",
                "",
                "- NOT_RUN: Semantic test and GUI UAT results are pending.",
            ]
        )
    lines.extend(
        [
            "",
            "## Owned runtime resources",
            "",
            f"- Compose project: `{markdown_escape(state['compose_project'])}`",
        ]
    )
    for kind in RESOURCE_KINDS:
        values = state["resources"].get(kind, [])
        rendered = (
            ", ".join(f"`{markdown_escape(value)}`" for value in values)
            or "none recorded"
        )
        lines.append(f"- {kind.title()}: {rendered}")
    cleanup = state.get("cleanup", {})
    retention_state = {
        "KEPT": "explicitly kept",
        "DESTROYED": "destroyed",
    }.get(state["status"], "pending or cleanup incomplete")
    lines.extend(
        [
            "",
            "## Cleanup",
            "",
            f"- Status: {markdown_escape(cleanup.get('status', 'NOT_RUN'))}",
            f"- Removed resources: {len(cleanup.get('removed', []))}",
            f"- Already-absent resources: {len(cleanup.get('already_absent', []))}",
            f"- Retained owned resources: {sum(len(state['resources'].get(kind, [])) for kind in RESOURCE_KINDS) if state['status'] == 'KEPT' else 0}",
            f"- Cleanup-failed remaining resources: {len(cleanup.get('remaining', [])) if cleanup.get('status') == 'FAIL' else 0}",
            f"- Project retention state: {retention_state}",
            "- Destroy invocation: "
            f"`$sdlc-workflow-test --verification-root {markdown_escape(verification_root)} --destroy`",
            "",
            "Raw logs, prompt bodies, credentials, database contents, and private SDLC state are intentionally omitted.",
            "",
        ]
    )
    return "\n".join(lines)
