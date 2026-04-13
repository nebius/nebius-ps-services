"""Flux bootstrap/reconcile helpers."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from rich.markup import escape

from .github_secrets import detect_github_repo_slug
from .managed_tools import FLUX_RELEASES_URL, configured_flux_version, resolve_flux_binary
from .paths import ProjectPaths

FLUX_NAMESPACE = "flux-system"
CLUSTER_HANDOFF_ACCESS_ENV = "NEBIUS_CXCLI_CLUSTER_HANDOFF_ACCESS"
FLUX_CORE_DEPLOYMENTS = (
    "source-controller",
    "kustomize-controller",
    "helm-controller",
    "notification-controller",
)
_FLUX_REQUIRED_API_TYPES = (
    ("gitrepositories.source.toolkit.fluxcd.io", FLUX_NAMESPACE),
    ("helmcharts.source.toolkit.fluxcd.io", FLUX_NAMESPACE),
    ("helmrepositories.source.toolkit.fluxcd.io", FLUX_NAMESPACE),
    ("ocirepositories.source.toolkit.fluxcd.io", FLUX_NAMESPACE),
    ("helmreleases.helm.toolkit.fluxcd.io", FLUX_NAMESPACE),
    ("kustomizations.kustomize.toolkit.fluxcd.io", FLUX_NAMESPACE),
)
_FLUX_SOURCE_GROUP = "source.toolkit.fluxcd.io"
_FLUX_READY_GROUPS = {
    _FLUX_SOURCE_GROUP,
    "helm.toolkit.fluxcd.io",
    "kustomize.toolkit.fluxcd.io",
}
_FLUX_NAMESPACE_STUCK_RESOURCE_TYPES = (
    "gitrepositories.source.toolkit.fluxcd.io",
    "helmcharts.source.toolkit.fluxcd.io",
    "helmrepositories.source.toolkit.fluxcd.io",
    "kustomizations.kustomize.toolkit.fluxcd.io",
)
_FLUX_REQUIRED_CRDS = tuple(
    sorted({resource_type for resource_type, _namespace in _FLUX_REQUIRED_API_TYPES})
)
_BENIGN_KUBECTL_OUTPUT_MARKERS = (
    "token from NEBIUS_IAM_TOKEN env is used",
    "missing the kubectl.kubernetes.io/last-applied-configuration annotation",
    "The missing annotation will be patched automatically.",
)
_GO_DURATION_PART_RE = re.compile(r"(\d+(?:\.\d+)?)(ns|us|µs|ms|s|m|h)")


@dataclass(frozen=True)
class FluxWaitTarget:
    resource_type: str
    name: str
    namespace: str
    kind: str
    is_source: bool
    timeout_seconds: int | None = None


@dataclass(frozen=True)
class FluxTargetStatus:
    target: FluxWaitTarget
    is_ready: bool
    has_ready_condition: bool
    is_terminal_failure: bool
    failure_reason: str
    failure_message: str
    summary: str


def _require_binary(name: str) -> None:
    if shutil.which(name):
        return
    raise RuntimeError(f"{name} is required but was not found in PATH")


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 300,
    extra_env: dict[str, str] | None = None,
) -> None:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    subprocess.run(cmd, cwd=cwd, check=True, timeout=timeout, env=env)


def _filter_benign_kubectl_output(text: str) -> str:
    kept_lines = [
        line
        for line in text.splitlines()
        if not any(marker in line for marker in _BENIGN_KUBECTL_OUTPUT_MARKERS)
    ]
    return "\n".join(kept_lines).strip()


def _run_filtered_kubectl_apply(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 300,
    extra_env: dict[str, str] | None = None,
) -> None:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    completed = subprocess.run(
        cmd,
        cwd=cwd,
        timeout=timeout,
        env=env,
        capture_output=True,
        text=True,
    )
    stdout = _filter_benign_kubectl_output(completed.stdout or "")
    stderr = _filter_benign_kubectl_output(completed.stderr or "")
    if stdout:
        sys.stdout.write(stdout + ("\n" if not stdout.endswith("\n") else ""))
    if stderr:
        sys.stderr.write(stderr + ("\n" if not stderr.endswith("\n") else ""))
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode,
            cmd,
            output=stdout,
            stderr=stderr,
        )


def _first_non_empty_line(text: str) -> str | None:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line:
            return line
    return None


def cluster_handoff_reachability_guidance(
    *,
    extra_env: dict[str, str] | None,
    action: str,
) -> str:
    access = str((extra_env or {}).get(CLUSTER_HANDOFF_ACCESS_ENV, "")).strip().lower()
    if access != "internal":
        return ""
    return (
        f"The selected cluster handoff for {action} uses a private MK8s control-plane endpoint. "
        "The machine running `nebius-cxcli` must already have network reachability to that private endpoint. "
        "Provide a private network path such as a VPN, routed private network, SSH or WireGuard tunnel, "
        "a subnet router, or run the command from an environment that already has Nebius private-network access."
    )


def _kustomization_resource_files(flux_dir: Path) -> list[Path]:
    kustomization_path = flux_dir / "kustomization.yaml"
    if not kustomization_path.exists():
        return []
    raw = yaml.safe_load(kustomization_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return []
    resources = raw.get("resources")
    if not isinstance(resources, list):
        return []
    files: list[Path] = []
    for resource in resources:
        if not isinstance(resource, str):
            continue
        normalized = resource.strip()
        if not normalized:
            continue
        candidate = (flux_dir / normalized).resolve()
        if candidate.exists() and candidate.is_file():
            files.append(candidate)
    return files


def flux_dir_has_rendered_resources(flux_dir: Path) -> bool:
    return bool(_kustomization_resource_files(flux_dir))


def _iter_yaml_docs(path: Path) -> list[dict[str, Any]]:
    payload = path.read_text(encoding="utf-8")
    docs: list[dict[str, Any]] = []
    for raw_doc in yaml.safe_load_all(payload):
        if isinstance(raw_doc, dict):
            docs.append(raw_doc)
    return docs


def _parse_go_duration_seconds(raw: str) -> int | None:
    text = raw.strip()
    if not text:
        return None
    total_seconds = 0.0
    position = 0
    for match in _GO_DURATION_PART_RE.finditer(text):
        if match.start() != position:
            return None
        value = float(match.group(1))
        unit = match.group(2)
        position = match.end()
        multiplier = {
            "ns": 1e-9,
            "us": 1e-6,
            "µs": 1e-6,
            "ms": 1e-3,
            "s": 1.0,
            "m": 60.0,
            "h": 3600.0,
        }[unit]
        total_seconds += value * multiplier
    if position != len(text):
        return None
    return max(1, int(math.ceil(total_seconds)))


def _target_manifest_timeout_seconds(doc: dict[str, Any], *, is_source: bool) -> int | None:
    if is_source:
        return None
    spec = doc.get("spec")
    if not isinstance(spec, dict):
        return None
    raw_timeout = spec.get("timeout")
    if not isinstance(raw_timeout, str):
        return None
    return _parse_go_duration_seconds(raw_timeout)


def _suggested_flux_wait_timeout_seconds(
    targets: list[FluxWaitTarget], *, minimum_seconds: int = 600, grace_seconds: int = 60
) -> int:
    hinted_timeouts = [
        timeout for timeout in (target.timeout_seconds for target in targets) if timeout is not None
    ]
    if not hinted_timeouts:
        return minimum_seconds
    return max(minimum_seconds, max(hinted_timeouts) + grace_seconds)


def _flux_wait_targets(flux_dir: Path) -> list[FluxWaitTarget]:
    files = _kustomization_resource_files(flux_dir)
    if not files:
        files = sorted(
            path for path in flux_dir.glob("*.yaml") if path.name != "kustomization.yaml"
        )
    source_targets: list[FluxWaitTarget] = []
    workload_targets: list[FluxWaitTarget] = []
    seen: set[tuple[str, str, str]] = set()
    for path in files:
        for doc in _iter_yaml_docs(path):
            api_version = str(doc.get("apiVersion", "")).strip()
            kind = str(doc.get("kind", "")).strip()
            metadata = doc.get("metadata")
            if not api_version or not kind or not isinstance(metadata, dict):
                continue
            if "/" not in api_version:
                continue
            group, _version = api_version.split("/", maxsplit=1)
            if group not in _FLUX_READY_GROUPS:
                continue
            name = str(metadata.get("name", "")).strip()
            if not name:
                continue
            namespace = str(metadata.get("namespace", "")).strip()
            resource_type = f"{kind.lower()}.{group}"
            key = (resource_type, namespace, name)
            if key in seen:
                continue
            seen.add(key)
            target = FluxWaitTarget(
                resource_type=resource_type,
                name=name,
                namespace=namespace,
                kind=kind,
                is_source=(group == _FLUX_SOURCE_GROUP),
                timeout_seconds=_target_manifest_timeout_seconds(
                    doc, is_source=(group == _FLUX_SOURCE_GROUP)
                ),
            )
            if group == _FLUX_SOURCE_GROUP:
                source_targets.append(target)
            else:
                workload_targets.append(target)
    return [*source_targets, *workload_targets]


def _target_label(target: FluxWaitTarget) -> str:
    if target.namespace:
        return f"{target.namespace}/{target.name}"
    return target.name


def _kubectl_get_target(
    target: FluxWaitTarget,
    *,
    env: dict[str, str],
    timeout_seconds: int = 20,
) -> tuple[dict[str, Any] | None, str]:
    cmd = ["kubectl", "get", f"{target.resource_type}/{target.name}"]
    if target.namespace:
        cmd.extend(["-n", target.namespace])
    cmd.extend(["-o", "json"])
    result = subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    if result.returncode != 0:
        detail = _first_non_empty_line(result.stderr or result.stdout or "") or "not visible yet"
        return None, detail
    try:
        return json.loads(result.stdout or "{}"), ""
    except json.JSONDecodeError:
        return None, "returned unreadable status payload"


def _condition_by_type(payload: dict[str, Any], condition_type: str) -> dict[str, Any] | None:
    status = payload.get("status")
    if not isinstance(status, dict):
        return None
    conditions = status.get("conditions")
    if not isinstance(conditions, list):
        return None
    for item in conditions:
        if not isinstance(item, dict):
            continue
        if str(item.get("type", "")).strip() == condition_type:
            return item
    return None


def _ready_condition(payload: dict[str, Any]) -> dict[str, Any] | None:
    return _condition_by_type(payload, "Ready")


def _stalled_condition(payload: dict[str, Any]) -> dict[str, Any] | None:
    return _condition_by_type(payload, "Stalled")


def _condition_status_true(condition: dict[str, Any] | None) -> bool:
    if not isinstance(condition, dict):
        return False
    return str(condition.get("status", "")).strip().lower() == "true"


def _format_flux_target_summary(
    target: FluxWaitTarget, payload: dict[str, Any] | None, detail: str
) -> FluxTargetStatus:
    label = _target_label(target)
    if payload is None:
        return FluxTargetStatus(
            target=target,
            is_ready=False,
            has_ready_condition=False,
            is_terminal_failure=False,
            failure_reason="",
            failure_message="",
            summary=(
                f"[yellow]{target.kind}[/yellow] {escape(label)}: "
                f"[yellow]waiting[/yellow]; {escape(detail)}"
            ),
        )

    ready = _ready_condition(payload)
    stalled = _stalled_condition(payload)
    if _condition_status_true(stalled):
        reason = str(stalled.get("reason", "")).strip() or str(
            (ready or {}).get("reason", "")
        ).strip() or "Stalled"
        message = (
            _first_non_empty_line(str(stalled.get("message", "")).strip())
            or _first_non_empty_line(str((ready or {}).get("message", "")).strip())
            or ""
        )
        summary = f"[red]{target.kind}[/red] {escape(label)}: [red]{escape(reason)}[/red]"
        if message:
            summary += f"; {escape(message)}"
        return FluxTargetStatus(
            target=target,
            is_ready=False,
            has_ready_condition=ready is not None,
            is_terminal_failure=True,
            failure_reason=reason,
            failure_message=message,
            summary=summary,
        )

    if ready is None:
        return FluxTargetStatus(
            target=target,
            is_ready=False,
            has_ready_condition=False,
            is_terminal_failure=False,
            failure_reason="",
            failure_message="",
            summary=(
                f"[yellow]{target.kind}[/yellow] {escape(label)}: "
                "[yellow]waiting[/yellow]; controller has not published a Ready condition yet"
            ),
        )

    status_value = str(ready.get("status", "")).strip().lower()
    reason = str(ready.get("reason", "")).strip()
    message = _first_non_empty_line(str(ready.get("message", "")).strip()) or ""
    if status_value == "true":
        summary = f"[green]{target.kind}[/green] {escape(label)}: [green]Ready[/green]"
        if reason:
            summary += f" ({escape(reason)})"
        return FluxTargetStatus(
            target=target,
            is_ready=True,
            has_ready_condition=True,
            is_terminal_failure=False,
            failure_reason="",
            failure_message="",
            summary=summary,
        )

    state_label = reason or "NotReady"
    summary = (
        f"[yellow]{target.kind}[/yellow] {escape(label)}: [yellow]{escape(state_label)}[/yellow]"
    )
    if message:
        summary += f"; {escape(message)}"
    return FluxTargetStatus(
        target=target,
        is_ready=False,
        has_ready_condition=True,
        is_terminal_failure=False,
        failure_reason="",
        failure_message="",
        summary=summary,
    )


def _select_actionable_flux_status(statuses: list[FluxTargetStatus]) -> FluxTargetStatus | None:
    for status in statuses:
        if status.is_terminal_failure and not status.target.is_source:
            return status
    for status in statuses:
        if status.is_terminal_failure:
            return status
    for status in statuses:
        if not status.is_ready and not status.target.is_source:
            return status
    return next((status for status in statuses if not status.is_ready), None)


def _flux_status_block(
    targets: list[FluxWaitTarget],
    *,
    env: dict[str, str],
    started_at: float,
) -> tuple[list[FluxTargetStatus], bool, bool, str]:
    lines: list[str] = []
    ready_count = 0
    statuses: list[FluxTargetStatus] = []
    for target in targets:
        payload, detail = _kubectl_get_target(target, env=env)
        status = _format_flux_target_summary(target, payload, detail)
        statuses.append(status)
        if status.is_ready:
            ready_count += 1
        lines.append(f"[dim]-[/dim] {status.summary}")
    total = len(targets)
    elapsed = max(0, int(time.monotonic() - started_at))
    minutes, seconds = divmod(elapsed, 60)
    elapsed_label = f"{minutes}m{seconds:02d}s" if minutes else f"{seconds}s"
    header = (
        f"[bold cyan]Flux status[/bold cyan] [dim][{elapsed_label}][/dim] "
        f"[bold]{ready_count}/{total} Ready[/bold]"
    )
    non_ready_statuses = [status for status in statuses if not status.is_ready]
    terminal_failures = [status for status in non_ready_statuses if status.is_terminal_failure]
    only_sources_pending = bool(non_ready_statuses) and not terminal_failures and all(
        status.target.is_source for status in non_ready_statuses
    )
    return statuses, ready_count == total, only_sources_pending, "\n".join([header, *lines])


def wait_for_rendered_flux_resources(
    paths: ProjectPaths,
    *,
    extra_env: dict[str, str] | None = None,
    timeout_seconds: int | None = None,
    emit: Callable[[str], None] | None = None,
    poll_interval_seconds: float = 5.0,
    repeat_interval_seconds: float = 20.0,
) -> None:
    _require_binary("kubectl")
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    targets = _flux_wait_targets(paths.flux_dir)
    if not targets:
        return
    started_at = time.monotonic()
    effective_timeout_seconds = (
        timeout_seconds
        if timeout_seconds is not None
        else _suggested_flux_wait_timeout_seconds(targets)
    )
    deadline = started_at + effective_timeout_seconds
    last_emitted = ""
    last_emit_time = 0.0
    last_actionable_status: FluxTargetStatus | None = None
    last_terminal_failures: list[FluxTargetStatus] = []

    while True:
        statuses, all_ready, only_sources_pending, block = _flux_status_block(
            targets,
            env=env,
            started_at=started_at,
        )
        non_ready_statuses = [status for status in statuses if not status.is_ready]
        terminal_failures = [status for status in non_ready_statuses if status.is_terminal_failure]
        pending_workloads = [
            status
            for status in non_ready_statuses
            if not status.is_terminal_failure and not status.target.is_source
        ]
        actionable_status = _select_actionable_flux_status(non_ready_statuses)
        now = time.monotonic()
        if emit and (block != last_emitted or (now - last_emit_time) >= repeat_interval_seconds):
            emit(block)
            last_emitted = block
            last_emit_time = now
        if all_ready:
            return
        if only_sources_pending:
            if emit:
                emit(
                    "[cyan]NOTE:[/cyan] All rendered workload resources are Ready, but one or more rendered Flux source "
                    "objects still have no Ready status. Treating the local apply as successful and skipping the remaining "
                    "source wait. Inspect the source objects separately if you need source-controller health details."
                )
            return
        last_actionable_status = actionable_status
        last_terminal_failures = terminal_failures
        if terminal_failures and not pending_workloads:
            break
        if now >= deadline:
            break
        time.sleep(max(0.1, poll_interval_seconds))

    if last_actionable_status is None:
        raise RuntimeError(
            f"Rendered Flux resources did not become Ready within {effective_timeout_seconds}s."
        )

    target = last_actionable_status.target
    target_label = f"{target.kind} '{target.name}'"
    if target.namespace:
        target_label = f"{target_label} in namespace '{target.namespace}'"
    describe_cmd = ["kubectl"]
    if target.namespace:
        describe_cmd.extend(["-n", target.namespace])
    describe_cmd.extend(["describe", f"{target.resource_type}/{target.name}"])
    events_cmd = ["kubectl"]
    if target.namespace:
        events_cmd.extend(["-n", target.namespace])
    events_cmd.extend(["get", "events", "--sort-by=.lastTimestamp"])
    if last_terminal_failures:
        failure_summary = "; ".join(
            f"{status.target.kind} {status.target.namespace}/{status.target.name}: "
            f"{status.failure_reason or 'terminal failure'}"
            for status in last_terminal_failures
        )
        guidance = "One or more rendered Flux resources reached a terminal failure state."
        if failure_summary:
            guidance += f"\nFailed resources: {failure_summary}"
        if last_actionable_status.failure_message:
            guidance += f"\nFailure detail: {last_actionable_status.failure_message}"
        guidance += (
            "\nThe rendered manifests were already applied; other Flux-managed resources may continue "
            "reconciling in the cluster after this CLI command exits."
        )
    else:
        guidance = (
            f"Flux resource {target_label} did not become Ready within {effective_timeout_seconds}s."
        )
    if last_emitted:
        guidance += "\nLast known status:\n" + last_emitted
    raise RuntimeError(
        guidance
        + "\nInspect with `"
        + " ".join(describe_cmd)
        + "` and `"
        + " ".join(events_cmd)
        + "`."
    )


def delete_rendered_flux(
    paths: ProjectPaths,
    *,
    extra_env: dict[str, str] | None = None,
) -> None:
    """Delete rendered Flux manifests in local destroy mode."""
    if not flux_dir_has_rendered_resources(paths.flux_dir):
        return
    _require_binary("kubectl")
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    cluster_check = subprocess.run(
        ["kubectl", "cluster-info"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if cluster_check.returncode != 0:
        detail = _first_non_empty_line(cluster_check.stderr or cluster_check.stdout or "")
        message = "kubectl could not reach the target Kubernetes cluster for local destroy"
        if detail:
            message = f"{message}: {detail}"
        guidance = cluster_handoff_reachability_guidance(
            extra_env=extra_env,
            action="local destroy",
        )
        if guidance:
            message = f"{message}\n{guidance}"
        raise RuntimeError(message)

    with tempfile.TemporaryDirectory(prefix="nebius-cxcli-kubectl-delete-") as cache_dir:
        cache_path = Path(cache_dir)
        completed = subprocess.run(
            [
                "kubectl",
                "--cache-dir",
                str(cache_path),
                "delete",
                "-k",
                str(paths.flux_dir),
                "--ignore-not-found=true",
                "--wait=true",
                "--timeout=15m",
            ],
            env=env,
            timeout=1800,
            capture_output=True,
            text=True,
        )
        stdout = _filter_benign_kubectl_output(completed.stdout or "")
        stderr = _filter_benign_kubectl_output(completed.stderr or "")
        if stdout:
            sys.stdout.write(stdout + ("\n" if not stdout.endswith("\n") else ""))
        if stderr:
            sys.stderr.write(stderr + ("\n" if not stderr.endswith("\n") else ""))
        if completed.returncode != 0:
            raise subprocess.CalledProcessError(
                completed.returncode,
                [
                    "kubectl",
                    "--cache-dir",
                    str(cache_path),
                    "delete",
                    "-k",
                    str(paths.flux_dir),
                    "--ignore-not-found=true",
                    "--wait=true",
                    "--timeout=15m",
                ],
                output=stdout,
                stderr=stderr,
            )


def flux_controllers_installed(*, extra_env: dict[str, str] | None = None) -> bool:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        ["kubectl", "-n", FLUX_NAMESPACE, "get", "deployment", *FLUX_CORE_DEPLOYMENTS],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return result.returncode == 0


def flux_crds_installed(*, extra_env: dict[str, str] | None = None) -> bool:
    return all(
        _crd_is_established(_get_crd_payload(crd_name, extra_env=extra_env))
        for crd_name in _FLUX_REQUIRED_CRDS
    )


def flux_bootstrap_resources_installed(*, extra_env: dict[str, str] | None = None) -> bool:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    resources = (
        ("gitrepository", "flux-system"),
        ("kustomization", "flux-system"),
    )
    for kind, name in resources:
        result = subprocess.run(
            ["kubectl", "-n", FLUX_NAMESPACE, "get", kind, name],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode != 0:
            return False
    return True


def flux_install_manifest_url() -> str:
    version = configured_flux_version()
    return f"{FLUX_RELEASES_URL}/download/{version}/install.yaml"


def _get_namespace_payload(
    namespace: str,
    *,
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        ["kubectl", "get", "namespace", namespace, "-o", "json"],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _get_crd_payload(
    name: str,
    *,
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        ["kubectl", "get", "crd", name, "-o", "json"],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _namespace_is_terminating(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    metadata = payload.get("metadata")
    status = payload.get("status")
    deletion_timestamp = (
        str(metadata.get("deletionTimestamp", "")).strip() if isinstance(metadata, dict) else ""
    )
    phase = str(status.get("phase", "")).strip() if isinstance(status, dict) else ""
    return bool(deletion_timestamp) or phase.lower() == "terminating"


def _namespace_termination_detail(payload: dict[str, Any]) -> str:
    status = payload.get("status")
    if not isinstance(status, dict):
        return f"namespace {FLUX_NAMESPACE!r} is terminating"
    conditions = status.get("conditions")
    messages: list[str] = []
    if isinstance(conditions, list):
        for item in conditions:
            if not isinstance(item, dict):
                continue
            if str(item.get("status", "")).strip().lower() != "true":
                continue
            message = _first_non_empty_line(str(item.get("message", "")).strip() or "")
            if message:
                messages.append(message)
    phase = str(status.get("phase", "")).strip() or "Unknown"
    if messages:
        return f"{FLUX_NAMESPACE!r} phase={phase}; " + " | ".join(messages)
    return f"{FLUX_NAMESPACE!r} phase={phase}; deletion is still in progress"


def _crd_is_deleting(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return False
    return bool(str(metadata.get("deletionTimestamp", "")).strip())


def _crd_is_established(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict) or _crd_is_deleting(payload):
        return False
    status = payload.get("status")
    if not isinstance(status, dict):
        return False
    conditions = status.get("conditions")
    if not isinstance(conditions, list):
        return False
    for item in conditions:
        if not isinstance(item, dict):
            continue
        if (
            str(item.get("type", "")).strip() == "Established"
            and str(item.get("status", "")).strip().lower() == "true"
        ):
            return True
    return False


def wait_for_flux_namespace_ready(
    *,
    extra_env: dict[str, str] | None = None,
    timeout_seconds: int = 30,
    poll_interval_seconds: float = 3.0,
) -> None:
    """Block Flux install/bootstrap while the flux-system namespace is terminating."""
    deadline = time.monotonic() + timeout_seconds
    last_detail = ""
    while True:
        payload = _get_namespace_payload(FLUX_NAMESPACE, extra_env=extra_env)
        if not _namespace_is_terminating(payload):
            return
        if isinstance(payload, dict):
            last_detail = _namespace_termination_detail(payload)
        if time.monotonic() >= deadline:
            inspect_cmd = (
                f"kubectl get namespace {FLUX_NAMESPACE} -o yaml && "
                f"kubectl -n {FLUX_NAMESPACE} get "
                + ",".join(_FLUX_NAMESPACE_STUCK_RESOURCE_TYPES)
                + " -o yaml"
            )
            guidance = (
                f"Flux namespace {FLUX_NAMESPACE!r} is stuck terminating. "
                "Wait for deletion to finish, or remove the remaining Flux resources/finalizers before retrying."
            )
            if last_detail:
                guidance += f"\nLast known namespace status: {last_detail}"
            guidance += f"\nInspect with `{inspect_cmd}`."
            raise RuntimeError(guidance)
        time.sleep(max(0.1, poll_interval_seconds))


def wait_for_flux_crds_clear(
    *,
    extra_env: dict[str, str] | None = None,
    timeout_seconds: int = 60,
    poll_interval_seconds: float = 3.0,
) -> None:
    """Wait until any previously deleting Flux CRDs have fully disappeared."""
    deadline = time.monotonic() + timeout_seconds
    last_deleting: list[str] = []
    while True:
        deleting = [
            crd_name
            for crd_name in _FLUX_REQUIRED_CRDS
            if _crd_is_deleting(_get_crd_payload(crd_name, extra_env=extra_env))
        ]
        if not deleting:
            return
        last_deleting = deleting
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "Flux CRDs are still terminating and cannot be reinstalled yet: "
                + ", ".join(last_deleting)
                + ". Wait for deletion to finish, then retry."
            )
        time.sleep(max(0.1, poll_interval_seconds))


def wait_for_flux_crds_ready(
    *,
    extra_env: dict[str, str] | None = None,
    timeout_seconds: int = 180,
    poll_interval_seconds: float = 3.0,
) -> None:
    """Wait until the required Flux CRDs exist and are Established."""
    deadline = time.monotonic() + timeout_seconds
    last_missing: list[str] = []
    while True:
        missing = [
            crd_name
            for crd_name in _FLUX_REQUIRED_CRDS
            if not _crd_is_established(_get_crd_payload(crd_name, extra_env=extra_env))
        ]
        if not missing:
            return
        last_missing = missing
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "Flux controllers were installed, but the required CRDs are not ready: "
                + ", ".join(last_missing)
                + ". Retry once CRD registration has settled."
            )
        time.sleep(max(0.1, poll_interval_seconds))


def install_flux_controllers(*, extra_env: dict[str, str] | None = None) -> str:
    _require_binary("kubectl")
    wait_for_flux_namespace_ready(extra_env=extra_env)
    wait_for_flux_crds_clear(extra_env=extra_env)
    manifest_url = flux_install_manifest_url()
    _run_filtered_kubectl_apply(
        ["kubectl", "apply", "-f", manifest_url],
        timeout=600,
        extra_env=extra_env,
    )
    for deployment in FLUX_CORE_DEPLOYMENTS:
        _run(
            [
                "kubectl",
                "-n",
                FLUX_NAMESPACE,
                "rollout",
                "status",
                f"deployment/{deployment}",
                "--timeout=5m",
            ],
            timeout=360,
            extra_env=extra_env,
        )
    wait_for_flux_crds_ready(extra_env=extra_env)
    return manifest_url


def wait_for_flux_resource_apis(
    paths: ProjectPaths,
    *,
    extra_env: dict[str, str] | None = None,
    cache_dir: Path | None = None,
    timeout_seconds: int = 180,
    poll_interval_seconds: float = 3.0,
) -> None:
    """Wait until the Flux CRD-backed resource APIs used by the rendered bundle are discoverable."""
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    wait_for_flux_crds_ready(extra_env=extra_env)

    required_types = {
        (target.resource_type, target.namespace or FLUX_NAMESPACE)
        for target in _flux_wait_targets(paths.flux_dir)
    }
    required_types.update(_FLUX_REQUIRED_API_TYPES)
    if not required_types:
        return

    deadline = time.monotonic() + timeout_seconds
    last_missing: list[str] = []
    while True:
        missing: list[str] = []
        for resource_type, namespace in sorted(required_types):
            cmd = ["kubectl"]
            if cache_dir is not None:
                cmd.extend(["--cache-dir", str(cache_dir)])
            cmd.extend(
                [
                    "-n",
                    namespace,
                    "get",
                    resource_type,
                    "--ignore-not-found",
                ]
            )
            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )
            if result.returncode != 0:
                missing.append(resource_type)
        if not missing:
            return
        last_missing = missing
        if time.monotonic() >= deadline:
            missing_list = ", ".join(last_missing)
            raise RuntimeError(
                "Flux controllers were installed, but the Kubernetes API server has not yet registered "
                f"the required Flux resource APIs: {missing_list}. Retry once CRD discovery has settled."
            )
        time.sleep(max(0.1, poll_interval_seconds))


def _resolve_owner_repo(repo_root: Path) -> tuple[str, str]:
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if "/" not in repository:
        repository = detect_github_repo_slug(repo_root)
    owner, repo = repository.split("/", maxsplit=1)
    return owner, repo


def ensure_flux(paths: ProjectPaths, *, extra_env: dict[str, str] | None = None) -> str:
    """Bootstrap Flux once, then reconcile idempotently on subsequent runs."""
    _require_binary("kubectl")
    wait_for_flux_namespace_ready(extra_env=extra_env)
    flux_binary = resolve_flux_binary()

    relative_flux_path = paths.flux_dir.relative_to(paths.repo_root).as_posix()

    flux_ready = flux_controllers_installed(extra_env=extra_env) and flux_crds_installed(
        extra_env=extra_env
    )
    if not flux_ready:
        install_flux_controllers(extra_env=extra_env)

    if flux_bootstrap_resources_installed(extra_env=extra_env):
        _run(
            [flux_binary, "reconcile", "source", "git", "flux-system"],
            timeout=300,
            extra_env=extra_env,
        )
        _run(
            [flux_binary, "reconcile", "kustomization", "flux-system", "--with-source"],
            timeout=300,
            extra_env=extra_env,
        )
        return "reconciled"

    owner, repo = _resolve_owner_repo(paths.repo_root)
    branch = os.environ.get("GITHUB_REF_NAME", "main")

    bootstrap_cmd = [
        flux_binary,
        "bootstrap",
        "github",
        "--owner",
        owner,
        "--repository",
        repo,
        "--branch",
        branch,
        "--path",
        relative_flux_path,
        "--token-auth",
    ]
    if os.environ.get("NEBIUS_CXCLI_FLUX_GITHUB_PERSONAL", "").lower() in {
        "1",
        "true",
        "yes",
    }:
        bootstrap_cmd.append("--personal")

    _run(bootstrap_cmd, cwd=paths.repo_root, timeout=1800, extra_env=extra_env)
    _run(
        [flux_binary, "reconcile", "kustomization", "flux-system", "--with-source"],
        timeout=300,
        extra_env=extra_env,
    )
    return "bootstrapped"
