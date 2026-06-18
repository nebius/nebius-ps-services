"""Helm release and workload readiness checks."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import yaml


@dataclass(frozen=True)
class HelmCommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""


class HelmCommandRunner(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> HelmCommandResult:
        """Run a Helm or kubectl command."""


class SubprocessHelmCommandRunner:
    def __init__(self, *, extra_env: Mapping[str, str] | None = None) -> None:
        self._extra_env = dict(extra_env or {})

    def __call__(
        self,
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> HelmCommandResult:
        env = os.environ.copy()
        env.update(self._extra_env)
        try:
            completed = subprocess.run(
                list(args),
                input=input_text,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"{args[0]} is required for Helm readiness checks") from exc
        result = HelmCommandResult(
            args=tuple(str(item) for item in args),
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
        if check and result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
            raise RuntimeError(f"{_command_text(args)} failed: {detail}")
        return result


@dataclass(frozen=True)
class HelmReleaseRecord:
    name: str
    namespace: str
    chart: str
    app_version: str
    status: str
    revision: str = ""

    @property
    def chart_version(self) -> str:
        match = re.search(r"-(v?[0-9][A-Za-z0-9.+-]*)$", self.chart)
        return match.group(1) if match else ""


@dataclass(frozen=True)
class HelmWorkloadReadiness:
    kind: str
    namespace: str
    name: str
    ready: bool
    summary: str

    @property
    def label(self) -> str:
        return f"{self.kind}/{self.namespace}/{self.name}"


@dataclass(frozen=True)
class HelmChartReadinessResult:
    release: HelmReleaseRecord
    expected_version: str
    workloads: tuple[HelmWorkloadReadiness, ...]

    @property
    def ready_workload_count(self) -> int:
        return sum(1 for workload in self.workloads if workload.ready)

    def summary(self) -> str:
        workload_total = len(self.workloads)
        if workload_total:
            workload_text = f"workloads {self.ready_workload_count}/{workload_total} ready"
        else:
            workload_text = "no Deployment/StatefulSet/DaemonSet workloads rendered"
        return (
            f"Helm chart {self.release.namespace}/{self.release.name} ready: "
            f"chart={self.release.chart or 'unknown'} "
            f"app={self.release.app_version or 'unknown'}; {workload_text}."
        )


_WORKLOAD_RESOURCE_BY_KIND = {
    "Deployment": "deployment",
    "StatefulSet": "statefulset",
    "DaemonSet": "daemonset",
}


def list_helm_releases(
    *,
    command_runner: HelmCommandRunner,
    kube_context: str = "",
    namespace: str = "",
    all_namespaces: bool = False,
    filter_regex: str = "",
    timeout_seconds: int = 120,
) -> tuple[HelmReleaseRecord, ...]:
    command = ["helm"]
    if kube_context:
        command.extend(["--kube-context", kube_context])
    command.append("list")
    if all_namespaces:
        command.append("-A")
    elif namespace:
        command.extend(["-n", namespace])
    if filter_regex:
        command.extend(["--filter", filter_regex])
    command.extend(["-o", "json"])
    result = command_runner(command, timeout_seconds=timeout_seconds)
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError("helm list returned invalid JSON") from exc
    if not isinstance(payload, list):
        raise RuntimeError("helm list returned a non-list JSON payload")
    releases: list[HelmReleaseRecord] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name", "") or "").strip()
        if not name:
            continue
        releases.append(
            HelmReleaseRecord(
                name=name,
                namespace=str(item.get("namespace", "") or namespace or "").strip(),
                chart=str(item.get("chart", "") or "").strip(),
                app_version=str(item.get("app_version", "") or "").strip(),
                status=str(item.get("status", "") or "").strip(),
                revision=str(item.get("revision", "") or "").strip(),
            )
        )
    return tuple(releases)


def verify_helm_chart_ready(
    *,
    command_runner: HelmCommandRunner,
    release_name: str,
    namespace: str,
    expected_version: str = "",
    kube_context: str = "",
    timeout_seconds: int = 300,
) -> HelmChartReadinessResult:
    release = _get_helm_release(
        command_runner=command_runner,
        release_name=release_name,
        namespace=namespace,
        expected_version=expected_version,
        kube_context=kube_context,
    )
    errors: list[str] = []
    if release.status.lower() != "deployed":
        errors.append(
            f"Helm release {release.namespace}/{release.name} status is "
            f"{release.status or 'unknown'}, not deployed"
        )
    if expected_version and not _release_matches_expected_version(release, expected_version):
        errors.append(
            f"Helm release {release.namespace}/{release.name} is chart={release.chart or 'unknown'} "
            f"app={release.app_version or 'unknown'}, expected version {expected_version}"
        )
    manifest_text = _helm_manifest(
        command_runner=command_runner,
        release_name=release.name,
        namespace=release.namespace,
        kube_context=kube_context,
    )
    workloads = tuple(
        _workload_readiness(
            command_runner=command_runner,
            workload=workload,
            kube_context=kube_context,
            timeout_seconds=timeout_seconds,
        )
        for workload in _manifest_workloads(manifest_text, default_namespace=release.namespace)
    )
    for workload in workloads:
        if not workload.ready:
            errors.append(f"{workload.label} is not ready: {workload.summary}")
    result = HelmChartReadinessResult(
        release=release,
        expected_version=expected_version,
        workloads=workloads,
    )
    if errors:
        raise RuntimeError(result.summary() + "\n" + "\n".join(f"- {item}" for item in errors))
    return result


def _get_helm_release(
    *,
    command_runner: HelmCommandRunner,
    release_name: str,
    namespace: str,
    expected_version: str,
    kube_context: str,
) -> HelmReleaseRecord:
    release_name = str(release_name or "").strip()
    namespace = str(namespace or "default").strip() or "default"
    if not release_name:
        raise RuntimeError("Helm release name is required for readiness checks")
    releases = list_helm_releases(
        command_runner=command_runner,
        kube_context=kube_context,
        namespace=namespace,
        filter_regex=f"^{re.escape(release_name)}$",
    )
    exact = [release for release in releases if release.name == release_name]
    if not exact:
        version_note = f" at version {expected_version}" if expected_version else ""
        raise RuntimeError(
            f"Helm release {namespace}/{release_name}{version_note} is not installed"
        )
    if len(exact) > 1:
        raise RuntimeError(
            f"Helm release lookup for {namespace}/{release_name} returned duplicates"
        )
    return exact[0]


def _helm_manifest(
    *,
    command_runner: HelmCommandRunner,
    release_name: str,
    namespace: str,
    kube_context: str,
) -> str:
    command = ["helm"]
    if kube_context:
        command.extend(["--kube-context", kube_context])
    command.extend(["get", "manifest", release_name, "-n", namespace])
    result = command_runner(command, timeout_seconds=120, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise RuntimeError(f"Could not read Helm manifest for {namespace}/{release_name}: {detail}")
    return result.stdout or ""


def _release_matches_expected_version(release: HelmReleaseRecord, expected_version: str) -> bool:
    expected = str(expected_version or "").strip()
    if not expected:
        return True
    expected_norm = expected.removeprefix("v")
    for candidate in (release.chart, release.chart_version, release.app_version):
        text = str(candidate or "").strip()
        if not text:
            continue
        text_norm = text.removeprefix("v")
        if text == expected or text_norm == expected_norm:
            return True
    return False


def _manifest_workloads(
    manifest_text: str,
    *,
    default_namespace: str,
) -> tuple[tuple[str, str, str], ...]:
    workloads: list[tuple[str, str, str]] = []
    try:
        documents = yaml.safe_load_all(manifest_text or "")
        for document in documents:
            _append_manifest_workloads(
                document,
                default_namespace=default_namespace,
                workloads=workloads,
            )
    except yaml.YAMLError as exc:
        raise RuntimeError("Helm manifest is not valid YAML") from exc
    return tuple(workloads)


def _append_manifest_workloads(
    document: Any,
    *,
    default_namespace: str,
    workloads: list[tuple[str, str, str]],
) -> None:
    if not isinstance(document, Mapping):
        return
    if document.get("kind") == "List" and isinstance(document.get("items"), list):
        for item in document["items"]:
            _append_manifest_workloads(
                item,
                default_namespace=default_namespace,
                workloads=workloads,
            )
        return
    kind = str(document.get("kind", "") or "").strip()
    if kind not in _WORKLOAD_RESOURCE_BY_KIND:
        return
    metadata = document.get("metadata")
    if not isinstance(metadata, Mapping):
        return
    name = str(metadata.get("name", "") or "").strip()
    if not name:
        return
    namespace = str(metadata.get("namespace", "") or default_namespace or "default").strip()
    workloads.append((kind, namespace or "default", name))


def _workload_readiness(
    *,
    command_runner: HelmCommandRunner,
    workload: tuple[str, str, str],
    kube_context: str,
    timeout_seconds: int,
) -> HelmWorkloadReadiness:
    kind, namespace, name = workload
    resource = _WORKLOAD_RESOURCE_BY_KIND[kind]
    command = ["kubectl"]
    if kube_context:
        command.extend(["--context", kube_context])
    command.extend(["-n", namespace, "get", resource, name, "-o", "json"])
    result = command_runner(command, timeout_seconds=timeout_seconds, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        return HelmWorkloadReadiness(
            kind=kind,
            namespace=namespace,
            name=name,
            ready=False,
            summary=f"kubectl get failed: {detail}",
        )
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return HelmWorkloadReadiness(
            kind=kind,
            namespace=namespace,
            name=name,
            ready=False,
            summary="kubectl returned invalid JSON",
        )
    if not isinstance(payload, Mapping):
        return HelmWorkloadReadiness(
            kind=kind,
            namespace=namespace,
            name=name,
            ready=False,
            summary="kubectl returned a non-object JSON payload",
        )
    ready, summary = _workload_payload_ready(kind, payload)
    return HelmWorkloadReadiness(
        kind=kind,
        namespace=namespace,
        name=name,
        ready=ready,
        summary=summary,
    )


def _workload_payload_ready(kind: str, payload: Mapping[str, Any]) -> tuple[bool, str]:
    spec = payload.get("spec")
    status = payload.get("status")
    metadata = payload.get("metadata")
    spec_map = spec if isinstance(spec, Mapping) else {}
    status_map = status if isinstance(status, Mapping) else {}
    metadata_map = metadata if isinstance(metadata, Mapping) else {}
    generation_ready = _observed_generation_ready(metadata_map, status_map)
    if kind == "Deployment":
        desired = _int_value(spec_map.get("replicas"), default=1)
        ready = _int_value(status_map.get("readyReplicas"))
        available = _int_value(status_map.get("availableReplicas"))
        updated = _int_value(status_map.get("updatedReplicas"))
        ok = generation_ready and ready >= desired and available >= desired and updated >= desired
        return ok, _ready_summary(
            desired=desired,
            ready=ready,
            available=available,
            updated=updated,
            generation_ready=generation_ready,
        )
    if kind == "StatefulSet":
        desired = _int_value(spec_map.get("replicas"), default=1)
        ready = _int_value(status_map.get("readyReplicas"))
        updated = _int_value(status_map.get("updatedReplicas"))
        ok = generation_ready and ready >= desired and updated >= desired
        return ok, _ready_summary(
            desired=desired,
            ready=ready,
            available=None,
            updated=updated,
            generation_ready=generation_ready,
        )
    desired = _int_value(status_map.get("desiredNumberScheduled"))
    ready = _int_value(status_map.get("numberReady"))
    available = _int_value(status_map.get("numberAvailable"))
    updated = _int_value(status_map.get("updatedNumberScheduled"))
    unavailable = _int_value(status_map.get("numberUnavailable"))
    ok = generation_ready and ready >= desired and updated >= desired and unavailable == 0
    summary = _ready_summary(
        desired=desired,
        ready=ready,
        available=available,
        updated=updated,
        generation_ready=generation_ready,
    )
    if unavailable:
        summary = f"{summary}, unavailable {unavailable}"
    return ok, summary


def _observed_generation_ready(
    metadata: Mapping[str, Any],
    status: Mapping[str, Any],
) -> bool:
    generation = _int_value(metadata.get("generation"))
    observed = _int_value(status.get("observedGeneration"))
    return not generation or not observed or observed >= generation


def _ready_summary(
    *,
    desired: int,
    ready: int,
    available: int | None,
    updated: int,
    generation_ready: bool,
) -> str:
    parts = [f"ready {ready}/{desired}", f"updated {updated}/{desired}"]
    if available is not None:
        parts.insert(1, f"available {available}/{desired}")
    if not generation_ready:
        parts.append("observedGeneration is behind metadata.generation")
    return ", ".join(parts)


def _int_value(value: Any, *, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _command_text(args: Sequence[str]) -> str:
    return " ".join(str(item) for item in args)
