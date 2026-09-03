"""Flux bootstrap/reconcile helpers."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from rich.markup import escape

from .github_secrets import detect_github_repo_slug
from .managed_tools import FLUX_RELEASES_URL, configured_flux_version, resolve_flux_binary
from .paths import ProjectPaths
from .soperator_adapter import (
    SOPERATOR_ADAPTER_LABEL,
    SOPERATOR_ADAPTER_LABEL_VALUE,
    SOPERATOR_ADAPTER_NAMESPACE,
    SOPERATOR_LIFECYCLE_LABEL,
    SOPERATOR_LIFECYCLE_PROTECTED,
    SOPERATOR_LIFECYCLE_RECREATABLE,
    SOPERATOR_LIFECYCLE_SHARED,
)
from .soperator_failures import (
    SoperatorMainWorkloadIdentity,
    SoperatorMainWorkloadTerminalError,
    SoperatorSafetyPauseError,
)
from .soperator_flux_graph import (
    SOPERATOR_GRAPH_LABEL,
    SOPERATOR_GRAPH_LABEL_VALUE,
    SOPERATOR_GRAPH_SCHEMA,
)
from .soperator_upgrade_progress import (
    SoperatorProgressEvent,
    SoperatorProgressSink,
    SoperatorProgressState,
    sanitized_bounded_command_output,
)

FLUX_NAMESPACE = "flux-system"
FLUX_STABLE_API_BRIDGE_VERSION = "v2.6.4"
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
_SOPERATOR_NAMESPACE_UPSTREAM_RELEASE = "soperator-fluxcd-ns"
_SOPERATOR_KRUISE_UPSTREAM_RELEASE = "soperator-fluxcd-kruise"
_SOPERATOR_CONTROLLER_UPSTREAM_RELEASE = "soperator-fluxcd-soperator"
_SOPERATOR_KRUISE_REQUIRED_CRDS = (
    "resourcedistributions.apps.kruise.io",
    "statefulsets.apps.kruise.io",
)
_SOPERATOR_KRUISE_NAMESPACE = "kruise-system"
_SOPERATOR_KRUISE_STATEFULSET_RESOURCE = "statefulsets.apps.kruise.io"
_SOPERATOR_MANAGED_BY_LABEL = "app.kubernetes.io/managed-by"
_SOPERATOR_MANAGED_BY_VALUE = "slurm-operator"
_SOPERATOR_CONTROLLER_NAMESPACE = "soperator-system"


@dataclass(frozen=True)
class FluxWaitTarget:
    resource_type: str
    name: str
    namespace: str
    kind: str
    is_source: bool
    timeout_seconds: int | None = None
    api_version: str = ""
    is_soperator_graph_member: bool = False
    is_soperator_main: bool = False
    source_kind: str = ""
    source_name: str = ""
    source_revision: str = ""
    expected_main_identity: SoperatorMainWorkloadIdentity | None = None


@dataclass(frozen=True)
class FluxTargetStatus:
    target: FluxWaitTarget
    is_ready: bool
    has_ready_condition: bool
    is_terminal_failure: bool
    failure_reason: str
    failure_message: str
    summary: str
    main_workload_identity: SoperatorMainWorkloadIdentity | None = None


@dataclass(frozen=True)
class SoperatorFluxSourceReceipt:
    """Sanitized proof that one locked source was fresh and frozen."""

    kind: str
    name: str
    namespace: str
    uid: str
    generation: int
    resource_version: str
    expected_digest: str
    observed_digest: str
    request_token: str
    observed_generation: int
    ready_transition_time: str
    artifact_transition_time: str
    suspended: bool


@dataclass(frozen=True)
class SoperatorProductReadinessReceipt:
    """Sanitized exact-object evidence for one complete readiness observation."""

    schema: str
    release: str
    cluster_name: str
    cluster_uid: str
    cluster_generation: int
    helm_releases: tuple[tuple[str, str, str, int], ...]
    sources: tuple[tuple[str, str, str, int], ...]
    adapter_daemonsets: tuple[tuple[str, str, str, int], ...]
    protected_storage: tuple[tuple[str, str, str, str], ...]
    node_sets: tuple[tuple[str, str, str, int], ...]
    pods: tuple[tuple[str, str, str, str], ...]
    active_checks: tuple[tuple[str, str, str, int], ...]


@dataclass(frozen=True)
class SoperatorNativeReadinessReceipt:
    """Sanitized observation of an upstream-native Soperator installation."""

    schema: str
    release: str
    cluster_name: str
    cluster_uid: str
    cluster_generation: int
    helm_releases: tuple[tuple[str, str, str, int], ...]
    protected_storage: tuple[tuple[str, str, str], ...]
    node_sets: tuple[tuple[str, str, str, int], ...]
    pods: tuple[tuple[str, str, str, str], ...]
    active_checks: tuple[tuple[str, str, str, int], ...]


@dataclass(frozen=True)
class FluxApplySummary:
    """Bounded presentation summary for one Flux controller manifest apply."""

    created: int
    configured: int
    unchanged: int
    other: int

    @property
    def total(self) -> int:
        return self.created + self.configured + self.unchanged + self.other

    def detail(self) -> str:
        parts = [
            f"{self.created} created",
            f"{self.configured} configured",
            f"{self.unchanged} unchanged",
        ]
        if self.other:
            parts.append(f"{self.other} other")
        return ", ".join(parts)


@dataclass(frozen=True)
class FluxMigrationSummary:
    """Bounded resource-kind counts parsed from successful `flux migrate` output."""

    total: int
    kinds: tuple[tuple[str, int], ...]

    def detail(self) -> str:
        if not self.total:
            return "custom resources migrated"
        kind_text = ", ".join(f"{kind} {count}" for kind, count in self.kinds)
        return f"{self.total} custom resources migrated" + (f" ({kind_text})" if kind_text else "")


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


def _captured_command_error(
    cmd: Sequence[str],
    *,
    returncode: int,
    stdout: str,
    stderr: str,
) -> RuntimeError:
    detail = stderr or stdout or "no diagnostic output"
    command_name = Path(cmd[0]).name if cmd else "command"
    return RuntimeError(f"{command_name} failed with exit code {returncode}:\n{detail}")


def _captured_command_timeout(
    cmd: Sequence[str],
    *,
    timeout: int,
    stdout: str | bytes | None,
    stderr: str | bytes | None,
) -> RuntimeError:
    def _text(value: str | bytes | None) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value or ""

    sanitized_stdout = sanitized_bounded_command_output(_text(stdout))
    sanitized_stderr = sanitized_bounded_command_output(_text(stderr))
    detail = sanitized_stderr or sanitized_stdout
    command_name = Path(cmd[0]).name if cmd else "command"
    message = f"{command_name} timed out after {timeout} seconds"
    if detail:
        message = f"{message}:\n{detail}"
    return RuntimeError(message)


def _run_captured(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 300,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one presentation-owned command without inheriting terminal streams."""

    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    try:
        completed = subprocess.run(
            cmd,
            cwd=cwd,
            timeout=timeout,
            env=env,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise _captured_command_timeout(
            cmd,
            timeout=timeout,
            stdout=exc.stdout,
            stderr=exc.stderr,
        ) from None
    if completed.returncode != 0:
        stdout = sanitized_bounded_command_output(completed.stdout or "")
        stderr = sanitized_bounded_command_output(completed.stderr or "")
        raise _captured_command_error(
            cmd,
            returncode=completed.returncode,
            stdout=stdout,
            stderr=stderr,
        )
    return completed


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
) -> FluxApplySummary:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    try:
        completed = subprocess.run(
            cmd,
            cwd=cwd,
            timeout=timeout,
            env=env,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise _captured_command_timeout(
            cmd,
            timeout=timeout,
            stdout=exc.stdout,
            stderr=exc.stderr,
        ) from None
    stdout = _filter_benign_kubectl_output(completed.stdout or "")
    stderr = _filter_benign_kubectl_output(completed.stderr or "")
    if completed.returncode != 0:
        raise _captured_command_error(
            cmd,
            returncode=completed.returncode,
            stdout=sanitized_bounded_command_output(stdout),
            stderr=sanitized_bounded_command_output(stderr),
        )
    counts = {"created": 0, "configured": 0, "unchanged": 0}
    other = 0
    for line in (*stdout.splitlines(), *stderr.splitlines()):
        action = line.rsplit(maxsplit=1)[-1].lower() if line.strip() else ""
        if action in counts:
            counts[action] += 1
        elif line.strip():
            other += 1
    return FluxApplySummary(
        created=counts["created"],
        configured=counts["configured"],
        unchanged=counts["unchanged"],
        other=other,
    )


def _flux_migration_summary(text: str) -> FluxMigrationSummary:
    counts: dict[str, int] = {}
    total = 0
    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip("✔").strip()
        if " migrated to version " not in line:
            continue
        identity = line.split(" migrated to version ", maxsplit=1)[0]
        kind = identity.split("/", maxsplit=1)[0].strip()
        if not kind:
            kind = "resource"
        counts[kind] = counts.get(kind, 0) + 1
        total += 1
    return FluxMigrationSummary(total=total, kinds=tuple(sorted(counts.items())))


def _emit_progress(progress: SoperatorProgressSink | None, event: SoperatorProgressEvent) -> None:
    if progress is None:
        return
    try:
        progress(event)
    except Exception:
        # Presentation must never change the Flux operation's authority or outcome.
        return


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
    resolved_flux_dir = flux_dir.resolve()
    files: list[Path] = []
    for resource in resources:
        if not isinstance(resource, str):
            continue
        normalized = resource.strip()
        if not normalized:
            continue
        candidate = (resolved_flux_dir / normalized).resolve()
        try:
            candidate.relative_to(resolved_flux_dir)
        except ValueError as exc:
            raise ValueError(
                f"Kustomization resource {normalized!r} escapes the rendered Flux directory"
            ) from exc
        if candidate.exists() and candidate.is_file():
            files.append(candidate)
    return files


def flux_dir_has_rendered_resources(flux_dir: Path) -> bool:
    return bool(_kustomization_resource_files(flux_dir))


def _soperator_release_source_documents(flux_dir: Path) -> list[dict[str, Any]]:
    graph_path = flux_dir / "soperator-release-graph.yaml"
    if not graph_path.is_file():
        return []
    accepted = {"OCIRepository", "HelmRepository", "HelmChart"}
    documents = [item for item in _iter_yaml_docs(graph_path) if item.get("kind") in accepted]
    for document in documents:
        metadata = document.get("metadata")
        if not isinstance(metadata, Mapping) or metadata.get("namespace") != FLUX_NAMESPACE:
            raise ValueError("Soperator release source objects must be scoped to flux-system")
    return documents


def _condition_is_ready(payload: Mapping[str, Any]) -> bool:
    status = payload.get("status")
    conditions = status.get("conditions") if isinstance(status, Mapping) else None
    if not isinstance(conditions, list):
        return False
    return any(
        isinstance(item, Mapping)
        and item.get("type") == "Ready"
        and str(item.get("status") or "").lower() == "true"
        for item in conditions
    )


def _true_condition(payload: Mapping[str, Any], condition_type: str) -> Mapping[str, Any]:
    status = payload.get("status")
    conditions = status.get("conditions") if isinstance(status, Mapping) else None
    if not isinstance(conditions, list):
        return {}
    return next(
        (
            item
            for item in conditions
            if isinstance(item, Mapping)
            and item.get("type") == condition_type
            and str(item.get("status") or "").lower() == "true"
        ),
        {},
    )


def _source_artifact(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    status = payload.get("status")
    artifact = status.get("artifact") if isinstance(status, Mapping) else None
    return artifact if isinstance(artifact, Mapping) else {}


def prepare_soperator_release_sources(
    paths: ProjectPaths,
    *,
    extra_env: dict[str, str] | None = None,
    cache_dir: Path | None = None,
    timeout_seconds: int = 600,
    poll_interval_seconds: float = 3.0,
    source_names: set[str] | None = None,
) -> tuple[SoperatorFluxSourceReceipt, ...]:
    """Resolve and freeze exact Soperator sources before applying the umbrella."""

    documents = _soperator_release_source_documents(paths.flux_dir)
    if source_names is not None:
        documents = [
            item
            for item in documents
            if str(item.get("metadata", {}).get("name") or "") in source_names
            or item.get("kind") == "HelmRepository"
        ]
    if not documents:
        return ()
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    effective_cache = cache_dir or Path(tempfile.mkdtemp(prefix="nebius-cxcli-source-cache-"))
    owns_cache = cache_dir is None
    try:
        repositories = [item for item in documents if item.get("kind") == "HelmRepository"]
        apply = subprocess.run(
            ["kubectl", "--cache-dir", str(effective_cache), "apply", "-f", "-"],
            env=env,
            input=yaml.safe_dump_all(repositories, sort_keys=False),
            capture_output=True,
            text=True,
            timeout=300,
        )
        if apply.returncode != 0:
            detail = _first_non_empty_line(apply.stderr or apply.stdout or "")
            raise RuntimeError(
                "failed to apply immutable Soperator release sources"
                + (f": {detail}" if detail else "")
            )

        receipts: list[SoperatorFluxSourceReceipt] = []
        expected_sources = [item for item in documents if item.get("kind") != "HelmRepository"]
        for document in expected_sources:
            kind = str(document["kind"])
            name = str(document["metadata"]["name"])
            resource = "ocirepository" if kind == "OCIRepository" else "helmchart"
            token = f"cxcli-{uuid4()}"
            desired = json.loads(json.dumps(document))
            desired.setdefault("metadata", {}).setdefault("annotations", {})[
                "reconcile.fluxcd.io/requestedAt"
            ] = token
            if kind == "HelmChart":
                desired.setdefault("spec", {})["suspend"] = False
            expected_digest = (
                str(document.get("spec", {}).get("ref", {}).get("digest") or "")
                if kind == "OCIRepository"
                else str(
                    document.get("metadata", {})
                    .get("annotations", {})
                    .get("soperator.nebius.ai/package-sha256")
                    or ""
                )
            )
            failure: Exception | None = None
            observed: dict[str, Any] | None = None
            try:
                apply = subprocess.run(
                    ["kubectl", "--cache-dir", str(effective_cache), "apply", "-f", "-"],
                    env=env,
                    input=yaml.safe_dump(desired, sort_keys=False),
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if apply.returncode != 0:
                    detail = _first_non_empty_line(apply.stderr or apply.stdout or "")
                    raise RuntimeError(
                        f"failed to reconcile locked Soperator source {name}"
                        + (f": {detail}" if detail else "")
                    )
                deadline = time.monotonic() + timeout_seconds
                while True:
                    result = subprocess.run(
                        [
                            "kubectl",
                            "--cache-dir",
                            str(effective_cache),
                            "-n",
                            FLUX_NAMESPACE,
                            "get",
                            resource,
                            name,
                            "-o",
                            "json",
                        ],
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    payload: dict[str, Any] = {}
                    if result.returncode == 0:
                        try:
                            candidate = json.loads(result.stdout or "{}")
                        except json.JSONDecodeError:
                            candidate = {}
                        if isinstance(candidate, dict):
                            payload = candidate
                    metadata = payload.get("metadata", {})
                    status = payload.get("status", {})
                    generation = int(metadata.get("generation") or -1)
                    observed_generation = int(status.get("observedGeneration") or -2)
                    handled = str(status.get("lastHandledReconcileAt") or "")
                    artifact = _source_artifact(payload)
                    observed_digest = str(artifact.get("digest") or artifact.get("revision") or "")
                    digest_matches = (
                        expected_digest
                        in " ".join(str(artifact.get(key) or "") for key in ("revision", "digest"))
                        if kind == "OCIRepository"
                        else observed_digest == expected_digest
                    )
                    artifact_ready = bool(_true_condition(payload, "ArtifactInStorage"))
                    if (
                        generation == observed_generation
                        and handled == token
                        and bool(_true_condition(payload, "Ready"))
                        and digest_matches
                        and (kind != "HelmChart" or artifact_ready)
                    ):
                        observed = payload
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError(
                            f"locked Soperator source {name} did not produce fresh artifact evidence"
                        )
                    time.sleep(max(0.1, poll_interval_seconds))
            except Exception as exc:
                failure = exc
            finally:
                if kind == "HelmChart":
                    patch = subprocess.run(
                        [
                            "kubectl",
                            "--cache-dir",
                            str(effective_cache),
                            "-n",
                            FLUX_NAMESPACE,
                            "patch",
                            "helmchart",
                            name,
                            "--type",
                            "merge",
                            "-p",
                            '{"spec":{"suspend":true}}',
                        ],
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if patch.returncode != 0 and failure is None:
                        detail = _first_non_empty_line(patch.stderr or patch.stdout or "")
                        failure = RuntimeError(
                            f"failed to freeze third-party chart source {name}"
                            + (f": {detail}" if detail else "")
                        )
            if failure is not None:
                raise failure
            verify = subprocess.run(
                [
                    "kubectl",
                    "--cache-dir",
                    str(effective_cache),
                    "-n",
                    FLUX_NAMESPACE,
                    "get",
                    resource,
                    name,
                    "-o",
                    "json",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if verify.returncode != 0:
                raise RuntimeError(f"failed to re-read frozen third-party chart source {name}")
            final_payload = json.loads(verify.stdout or "{}")
            if not isinstance(final_payload, dict):
                raise RuntimeError(f"locked Soperator source {name} returned invalid status")
            final_artifact = _source_artifact(final_payload)
            final_digest = str(final_artifact.get("digest") or final_artifact.get("revision") or "")
            if kind == "HelmChart" and (
                final_payload.get("spec", {}).get("suspend") is not True
                or final_digest != expected_digest
            ):
                raise RuntimeError(
                    f"third-party chart source {name} changed while entering the frozen state"
                )
            if kind == "OCIRepository" and expected_digest not in " ".join(
                str(final_artifact.get(key) or "") for key in ("revision", "digest")
            ):
                raise RuntimeError(
                    f"first-party OCI source {name} changed after fresh reconciliation"
                )
            assert observed is not None
            metadata = final_payload.get("metadata", {})
            status = observed.get("status", {})
            ready_condition = _true_condition(observed, "Ready")
            artifact_condition = _true_condition(observed, "ArtifactInStorage")
            receipts.append(
                SoperatorFluxSourceReceipt(
                    kind=kind,
                    name=name,
                    namespace=FLUX_NAMESPACE,
                    uid=str(metadata.get("uid") or ""),
                    generation=int(metadata.get("generation") or 0),
                    resource_version=str(metadata.get("resourceVersion") or ""),
                    expected_digest=expected_digest,
                    observed_digest=final_digest,
                    request_token=token,
                    observed_generation=int(status.get("observedGeneration") or 0),
                    ready_transition_time=str(ready_condition.get("lastTransitionTime") or ""),
                    artifact_transition_time=str(
                        artifact_condition.get("lastTransitionTime") or ""
                    ),
                    suspended=kind == "HelmChart",
                )
            )
        return tuple(receipts)
    finally:
        if owns_cache:
            shutil.rmtree(effective_cache, ignore_errors=True)


def _run_kubectl_json_process(
    command: list[str], *, env: Mapping[str, str], timeout: int = 30
) -> dict[str, Any]:
    result = subprocess.run(
        command,
        env=dict(env),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        detail = _first_non_empty_line(result.stderr or result.stdout or "")
        raise RuntimeError("kubectl failed" + (f": {detail}" if detail else ""))
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("kubectl returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("kubectl returned an unexpected JSON document")
    return payload


def _patch_helmrelease_suspend(
    *,
    name: str,
    namespace: str,
    suspend: bool,
    cache_dir: Path,
    env: Mapping[str, str],
    allow_missing: bool = False,
) -> None:
    result = subprocess.run(
        [
            "kubectl",
            "--cache-dir",
            str(cache_dir),
            "-n",
            namespace,
            "patch",
            "helmrelease",
            name,
            "--type",
            "merge",
            "-p",
            json.dumps({"spec": {"suspend": suspend}}, separators=(",", ":")),
        ],
        env=dict(env),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode == 0:
        return
    detail = _first_non_empty_line(result.stderr or result.stdout or "") or ""
    if allow_missing and "not found" in detail.lower():
        return
    raise RuntimeError(
        f"failed to {'suspend' if suspend else 'resume'} HelmRelease {namespace}/{name}"
        + (f": {detail}" if detail else "")
    )


def _remove_helmrelease_suspend(
    *,
    name: str,
    namespace: str,
    cache_dir: Path,
    env: Mapping[str, str],
) -> None:
    result = subprocess.run(
        [
            "kubectl",
            "--cache-dir",
            str(cache_dir),
            "-n",
            namespace,
            "patch",
            "helmrelease",
            name,
            "--type",
            "json",
            "-p",
            '[{"op":"remove","path":"/spec/suspend"}]',
        ],
        env=dict(env),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode == 0:
        return
    detail = _first_non_empty_line(result.stderr or result.stdout or "") or ""
    raise RuntimeError(
        f"failed to activate HelmRelease {namespace}/{name}" + (f": {detail}" if detail else "")
    )


def _wait_for_suspended_child_inventory(
    contract: Mapping[str, Any],
    *,
    cache_dir: Path,
    env: Mapping[str, str],
    timeout_seconds: int,
    poll_interval_seconds: float,
) -> None:
    releases = contract.get("releases")
    if not isinstance(releases, list) or not releases:
        raise ValueError("Soperator release graph contract has no releases")
    expected = {
        (str(item.get("namespace") or ""), str(item.get("releaseName") or ""))
        for item in releases
        if isinstance(item, Mapping)
    }
    deadline = time.monotonic() + timeout_seconds
    while True:
        payload = _run_kubectl_json_process(
            [
                "kubectl",
                "--cache-dir",
                str(cache_dir),
                "get",
                "helmreleases.helm.toolkit.fluxcd.io",
                "-A",
                "-l",
                "soperator.nebius.ai/release-graph=nebius-cxcli",
                "-o",
                "json",
            ],
            env=env,
        )
        items = payload.get("items")
        actual = (
            {
                (
                    str(item.get("metadata", {}).get("namespace") or FLUX_NAMESPACE),
                    str(item.get("metadata", {}).get("name") or ""),
                )
                for item in items
                if isinstance(item, Mapping)
            }
            if isinstance(items, list)
            else set()
        )
        all_suspended = isinstance(items, list) and all(
            isinstance(item, Mapping) and item.get("spec", {}).get("suspend") is True
            for item in items
        )
        if actual == expected and all_suspended:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("staged Soperator child HelmRelease inventory did not quiesce")
        time.sleep(max(0.1, poll_interval_seconds))


def _wait_for_helmrelease_quiescence(
    releases: set[tuple[str, str]],
    *,
    cache_dir: Path,
    env: Mapping[str, str],
    timeout_seconds: int,
    poll_interval_seconds: float,
) -> None:
    """Wait until requested suspensions are visible and no reconciliation is active."""

    if not releases:
        return
    deadline = time.monotonic() + timeout_seconds
    while True:
        quiescent = True
        for namespace, name in sorted(releases):
            payload = _run_kubectl_json_process(
                [
                    "kubectl",
                    "--cache-dir",
                    str(cache_dir),
                    "-n",
                    namespace,
                    "get",
                    "helmrelease",
                    name,
                    "-o",
                    "json",
                ],
                env=env,
            )
            metadata = payload.get("metadata", {})
            status = payload.get("status", {})
            conditions = status.get("conditions")
            reconciling = isinstance(conditions, list) and any(
                isinstance(item, Mapping)
                and item.get("type") == "Reconciling"
                and str(item.get("status") or "").lower() == "true"
                and (
                    not isinstance(metadata, Mapping)
                    or not isinstance(metadata.get("generation"), int)
                    or not isinstance(item.get("observedGeneration"), int)
                    or item["observedGeneration"] >= metadata["generation"]
                )
                for item in conditions
            )
            if (
                payload.get("spec", {}).get("suspend") is not True
                or not isinstance(metadata, Mapping)
                or not isinstance(status, Mapping)
                or metadata.get("generation") != status.get("observedGeneration")
                or reconciling
            ):
                quiescent = False
                break
        if quiescent:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("existing Soperator HelmRelease reconciliation did not quiesce")
        time.sleep(max(0.1, poll_interval_seconds))


def _wait_for_soperator_release_stage(
    contract: Mapping[str, Any],
    stage: int,
    *,
    cache_dir: Path,
    env: Mapping[str, str],
    timeout_seconds: int,
    poll_interval_seconds: float,
    main_target: FluxWaitTarget | None = None,
    freeze_main_workload_authority: (
        Callable[[SoperatorMainWorkloadIdentity], SoperatorMainWorkloadIdentity] | None
    ) = None,
) -> None:
    releases = contract.get("releases")
    selected = (
        [
            item
            for item in releases
            if isinstance(item, Mapping) and int(item.get("stage") or 0) <= stage
        ]
        if isinstance(releases, list)
        else []
    )
    if not selected:
        raise ValueError(f"Soperator release stage {stage} is empty")
    selected_main = [item for item in selected if item.get("isMain") is True]
    if len(selected_main) > 1:
        raise ValueError("Soperator release stage contains multiple main workloads")
    if freeze_main_workload_authority is not None and main_target is None:
        raise ValueError("Soperator main workload target is required to freeze authority")
    deadline = time.monotonic() + timeout_seconds
    while True:
        ready = True
        for item in selected:
            namespace = str(item.get("namespace") or FLUX_NAMESPACE)
            name = str(item.get("releaseName") or "")
            payload = _run_kubectl_json_process(
                [
                    "kubectl",
                    "--cache-dir",
                    str(cache_dir),
                    "-n",
                    namespace,
                    "get",
                    "helmrelease",
                    name,
                    "-o",
                    "json",
                ],
                env=env,
            )
            metadata = payload.get("metadata", {})
            status = payload.get("status", {})
            chart_ref = payload.get("spec", {}).get("chartRef", {})
            is_main = item.get("isMain") is True
            retain_namespace_release = (
                str(item.get("upstreamReleaseName") or "") == _SOPERATOR_NAMESPACE_UPSTREAM_RELEASE
            )
            if is_main:
                if freeze_main_workload_authority is None or main_target is None:
                    ready = False
                    break
                identity = _observe_soperator_main_workload_identity(
                    main_target,
                    payload,
                    env=env,
                    require_ready=False,
                )
                if identity is None:
                    ready = False
                    break
                frozen_identity = freeze_main_workload_authority(identity)
                if frozen_identity != identity:
                    raise SoperatorSafetyPauseError(
                        "Soperator main-workload authority differs from its observation",
                        code="main-workload-authority-conflict",
                    )
                stalled = _true_condition(payload, "Stalled")
                if stalled:
                    reason = str(stalled.get("reason") or "Stalled").strip() or "Stalled"
                    raise SoperatorMainWorkloadTerminalError(
                        f"main Soperator HelmRelease {identity.resource} stalled ({reason})",
                        identity=frozen_identity,
                        reason=reason,
                    )
            else:
                stalled = _true_condition(payload, "Stalled")
                if stalled:
                    reason = str(stalled.get("reason") or "Stalled").strip() or "Stalled"
                    raise RuntimeError(
                        f"staged Soperator HelmRelease {namespace}/{name} stalled "
                        f"({reason}); retrying the exact declared release stage"
                    )
            if (
                (payload.get("spec", {}).get("suspend") is True and not retain_namespace_release)
                or int(metadata.get("generation") or -1)
                != int(status.get("observedGeneration") or -2)
                or bool(_true_condition(payload, "Stalled"))
                or not _condition_is_ready(payload)
                or str(chart_ref.get("kind") or "") != str(item.get("sourceKind") or "")
                or str(chart_ref.get("name") or "") != str(item.get("sourceName") or "")
            ):
                ready = False
                break
        if ready:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Soperator HelmRelease stage {stage} did not become Ready")
        time.sleep(max(0.1, poll_interval_seconds))


def _deployment_is_current_and_available(payload: Mapping[str, Any]) -> bool:
    metadata = payload.get("metadata")
    spec = payload.get("spec")
    status = payload.get("status")
    if not all(isinstance(item, Mapping) for item in (metadata, spec, status)):
        return False
    generation = int(metadata.get("generation") or 0)
    desired = int(spec.get("replicas") if spec.get("replicas") is not None else 1)
    return (
        generation >= 1
        and desired >= 1
        and int(status.get("observedGeneration") or 0) == generation
        and int(status.get("readyReplicas") or 0) >= desired
        and int(status.get("availableReplicas") or 0) >= desired
    )


def _daemonset_is_current_and_available(payload: Mapping[str, Any]) -> bool:
    metadata = payload.get("metadata")
    status = payload.get("status")
    if not isinstance(metadata, Mapping) or not isinstance(status, Mapping):
        return False
    generation = int(metadata.get("generation") or 0)
    desired = int(status.get("desiredNumberScheduled") or 0)
    return (
        generation >= 1
        and desired >= 1
        and int(status.get("observedGeneration") or 0) == generation
        and int(status.get("numberReady") or 0) >= desired
        and int(status.get("numberAvailable") or 0) >= desired
        and int(status.get("numberUnavailable") or 0) == 0
    )


def _endpoints_have_ready_address(payload: Mapping[str, Any]) -> bool:
    subsets = payload.get("subsets")
    return isinstance(subsets, list) and any(
        isinstance(subset, Mapping)
        and isinstance(subset.get("addresses"), list)
        and bool(subset["addresses"])
        for subset in subsets
    )


def _restore_soperator_kruise_statefulset_defaults(
    *,
    cache_dir: Path,
    env: Mapping[str, str],
    timeout_seconds: int,
    poll_interval_seconds: float,
) -> None:
    """Restore admission defaults omitted while the Kruise webhook was absent."""

    deadline = time.monotonic() + timeout_seconds
    last_transient_detail = ""
    while True:
        payload = _run_kubectl_json_process(
            [
                "kubectl",
                "--cache-dir",
                str(cache_dir),
                "get",
                _SOPERATOR_KRUISE_STATEFULSET_RESOURCE,
                "-A",
                "-l",
                f"{_SOPERATOR_MANAGED_BY_LABEL}={_SOPERATOR_MANAGED_BY_VALUE}",
                "-o",
                "json",
            ],
            env=env,
        )
        items = payload.get("items")
        if not isinstance(items, list):
            raise RuntimeError("Soperator Kruise StatefulSet inventory is invalid")
        missing_defaults: list[tuple[str, str, str]] = []
        for item in items:
            if not isinstance(item, Mapping):
                raise RuntimeError("Soperator Kruise StatefulSet inventory is invalid")
            metadata = item.get("metadata")
            spec = item.get("spec")
            if not isinstance(metadata, Mapping) or not isinstance(spec, Mapping):
                raise RuntimeError("Soperator Kruise StatefulSet inventory is invalid")
            strategy = spec.get("updateStrategy")
            if not isinstance(strategy, Mapping) or str(strategy.get("type") or "") != (
                "RollingUpdate"
            ):
                continue
            rolling_update = strategy.get("rollingUpdate")
            if rolling_update is not None and not isinstance(rolling_update, Mapping):
                raise RuntimeError("Soperator Kruise rolling-update strategy is invalid")
            if isinstance(rolling_update, Mapping) and rolling_update.get("partition") is not None:
                continue
            labels = metadata.get("labels")
            owners = metadata.get("ownerReferences")
            if (
                not isinstance(labels, Mapping)
                or labels.get(_SOPERATOR_MANAGED_BY_LABEL) != _SOPERATOR_MANAGED_BY_VALUE
                or not isinstance(owners, list)
                or not any(
                    isinstance(owner, Mapping)
                    and owner.get("kind") == "SlurmCluster"
                    and owner.get("controller") is True
                    for owner in owners
                )
            ):
                raise SoperatorSafetyPauseError(
                    "a Kruise StatefulSet missing admission defaults is not owned by Soperator",
                    code="kruise-default-repair-owner-mismatch",
                )
            namespace = str(metadata.get("namespace") or "").strip()
            name = str(metadata.get("name") or "").strip()
            resource_version = str(metadata.get("resourceVersion") or "").strip()
            if not namespace or not name or not resource_version:
                raise RuntimeError("Soperator Kruise StatefulSet identity is incomplete")
            missing_defaults.append((namespace, name, resource_version))
        if not missing_defaults:
            return

        retry_inventory = False
        for namespace, name, resource_version in missing_defaults:
            patch = {
                "metadata": {"resourceVersion": resource_version},
                "spec": {"updateStrategy": {"rollingUpdate": {"partition": 0}}},
            }
            result = subprocess.run(
                [
                    "kubectl",
                    "--cache-dir",
                    str(cache_dir),
                    "-n",
                    namespace,
                    "patch",
                    _SOPERATOR_KRUISE_STATEFULSET_RESOURCE,
                    name,
                    "--type",
                    "merge",
                    "-p",
                    json.dumps(patch, separators=(",", ":")),
                ],
                env=dict(env),
                capture_output=True,
                text=True,
                timeout=45,
            )
            if result.returncode == 0:
                continue
            detail = _first_non_empty_line(result.stderr or result.stdout or "") or ""
            lowered = detail.lower()
            transient_markers = (
                "failed calling webhook",
                "no endpoints available for service",
                "connection refused",
                "context deadline exceeded",
                "timed out",
                "timeout",
                "conflict",
                "object has been modified",
            )
            if any(marker in lowered for marker in transient_markers):
                last_transient_detail = detail
                retry_inventory = True
                break
            raise RuntimeError(
                "failed to restore Soperator Kruise rolling-update defaults"
                + (f": {detail}" if detail else "")
            )
        if not retry_inventory:
            continue
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "Soperator Kruise admission did not become available for default recovery"
                + (f": {last_transient_detail}" if last_transient_detail else "")
            )
        time.sleep(max(0.1, poll_interval_seconds))


def _wait_for_soperator_dependency_health(
    stage_items: Sequence[Mapping[str, Any]],
    *,
    cache_dir: Path,
    env: Mapping[str, str],
    timeout_seconds: int,
    poll_interval_seconds: float,
) -> None:
    """Require stage-owned APIs and webhooks before opening their consumers."""

    upstream_names = {str(item.get("upstreamReleaseName") or "").strip() for item in stage_items}
    needs_kruise = _SOPERATOR_KRUISE_UPSTREAM_RELEASE in upstream_names
    needs_soperator_controller = _SOPERATOR_CONTROLLER_UPSTREAM_RELEASE in upstream_names
    if not needs_kruise and not needs_soperator_controller:
        return
    deadline = time.monotonic() + timeout_seconds
    while True:
        healthy = True
        try:
            if needs_kruise:
                for crd_name in _SOPERATOR_KRUISE_REQUIRED_CRDS:
                    crd = _run_kubectl_json_process(
                        [
                            "kubectl",
                            "--cache-dir",
                            str(cache_dir),
                            "get",
                            "customresourcedefinition",
                            crd_name,
                            "-o",
                            "json",
                        ],
                        env=env,
                    )
                    if not _crd_is_established(crd):
                        healthy = False
                        break
                if healthy:
                    deployment = _run_kubectl_json_process(
                        [
                            "kubectl",
                            "--cache-dir",
                            str(cache_dir),
                            "-n",
                            _SOPERATOR_KRUISE_NAMESPACE,
                            "get",
                            "deployment",
                            "kruise-controller-manager",
                            "-o",
                            "json",
                        ],
                        env=env,
                    )
                    daemonset = _run_kubectl_json_process(
                        [
                            "kubectl",
                            "--cache-dir",
                            str(cache_dir),
                            "-n",
                            _SOPERATOR_KRUISE_NAMESPACE,
                            "get",
                            "daemonset",
                            "kruise-daemon",
                            "-o",
                            "json",
                        ],
                        env=env,
                    )
                    endpoints = _run_kubectl_json_process(
                        [
                            "kubectl",
                            "--cache-dir",
                            str(cache_dir),
                            "-n",
                            _SOPERATOR_KRUISE_NAMESPACE,
                            "get",
                            "endpoints",
                            "kruise-webhook-service",
                            "-o",
                            "json",
                        ],
                        env=env,
                    )
                    healthy = (
                        _deployment_is_current_and_available(deployment)
                        and _daemonset_is_current_and_available(daemonset)
                        and _endpoints_have_ready_address(endpoints)
                    )
            if needs_soperator_controller and healthy:
                deployment = _run_kubectl_json_process(
                    [
                        "kubectl",
                        "--cache-dir",
                        str(cache_dir),
                        "-n",
                        _SOPERATOR_CONTROLLER_NAMESPACE,
                        "get",
                        "deployment",
                        "soperator-controller-manager",
                        "-o",
                        "json",
                    ],
                    env=env,
                )
                endpoints = _run_kubectl_json_process(
                    [
                        "kubectl",
                        "--cache-dir",
                        str(cache_dir),
                        "-n",
                        _SOPERATOR_CONTROLLER_NAMESPACE,
                        "get",
                        "endpoints",
                        "soperator-controller-webhook-service",
                        "-o",
                        "json",
                    ],
                    env=env,
                )
                healthy = _deployment_is_current_and_available(
                    deployment
                ) and _endpoints_have_ready_address(endpoints)
        except RuntimeError:
            healthy = False
        if healthy:
            return
        if time.monotonic() >= deadline:
            dependency = "Kruise" if needs_kruise else "Soperator controller"
            raise RuntimeError(
                f"{dependency} APIs and webhook did not become ready before opening "
                "the next Soperator release stage"
            )
        time.sleep(max(0.1, poll_interval_seconds))


def _staged_soperator_outer_release(
    documents: Sequence[dict[str, Any]],
    releases: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve the target umbrella from the rendered graph instead of a legacy name."""

    child_identities = {
        (str(item.get("namespace") or FLUX_NAMESPACE), str(item.get("releaseName") or ""))
        for item in releases
    }
    candidates = [
        item
        for item in documents
        if item.get("kind") == "HelmRelease"
        and (
            str(item.get("metadata", {}).get("namespace") or FLUX_NAMESPACE),
            str(item.get("metadata", {}).get("name") or ""),
        )
        not in child_identities
    ]
    release_names = {name for _namespace, name in child_identities if name}
    graph_umbrellas = [
        item
        for item in candidates
        if release_names
        and release_names
        <= set(
            re.findall(
                r"cxcli-[a-z0-9-]+",
                yaml.safe_dump(
                    item.get("spec", {}).get("postRenderers", []),
                    sort_keys=True,
                ),
            )
        )
    ]
    selected = graph_umbrellas if graph_umbrellas else candidates
    if len(selected) != 1:
        raise ValueError(
            "rendered Soperator graph must contain exactly one target outer HelmRelease"
        )
    metadata = selected[0].get("metadata")
    if (
        not isinstance(metadata, Mapping)
        or not str(metadata.get("name") or "").strip()
        or not str(metadata.get("namespace") or "").strip()
    ):
        raise ValueError("rendered Soperator outer HelmRelease identity is incomplete")
    return selected[0]


def _soperator_raw_child_rows(
    outer: Mapping[str, Any],
    releases: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, str], ...]:
    """Bind upstream graph rows to names rendered by the actual umbrella identity."""

    metadata = outer.get("metadata")
    spec = outer.get("spec")
    values = spec.get("values") if isinstance(spec, Mapping) else None
    configured_prefix = (
        str(values.get("fullnameOverride") or "").strip() if isinstance(values, Mapping) else ""
    )
    outer_name = str(metadata.get("name") or "").strip() if isinstance(metadata, Mapping) else ""
    outer_namespace = (
        str(metadata.get("namespace") or "").strip() if isinstance(metadata, Mapping) else ""
    )
    raw_prefix = (configured_prefix or outer_name)[:63].rstrip("-")
    if not raw_prefix or not outer_namespace:
        raise ValueError("rendered Soperator outer HelmRelease identity is incomplete")

    rows: list[dict[str, str]] = []
    for item in releases:
        upstream_name = str(item.get("upstreamReleaseName") or "").strip()
        final_name = str(item.get("releaseName") or "").strip()
        final_namespace = str(item.get("namespace") or "").strip()
        if not upstream_name.startswith("soperator-fluxcd-"):
            raise ValueError("rendered Soperator graph has an unsupported upstream child identity")
        suffix = upstream_name.removeprefix("soperator-fluxcd")
        raw_name = f"{raw_prefix}{suffix}"
        if not final_name or not final_namespace or len(raw_name) > 63:
            raise ValueError("rendered Soperator child identity is incomplete or too long")
        rows.append(
            {
                "upstreamName": upstream_name,
                "rawName": raw_name,
                "rawNamespace": outer_namespace,
                "finalName": final_name,
                "finalNamespace": final_namespace,
            }
        )
    raw_identities = {(row["rawNamespace"], row["rawName"]) for row in rows}
    final_identities = {(row["finalNamespace"], row["finalName"]) for row in rows}
    if (
        len(rows) != len(raw_identities)
        or len(rows) != len(final_identities)
        or raw_identities & final_identities
    ):
        raise ValueError("rendered Soperator child identities are ambiguous")
    return tuple(sorted(rows, key=lambda row: row["finalName"]))


def _normalize_soperator_outer_post_renderers(
    outer: dict[str, Any],
    releases: Sequence[Mapping[str, Any]],
    *,
    suspend_children: bool,
) -> tuple[dict[str, str], ...]:
    """Retarget graph patches to raw chart identities before their final rename."""

    rows = _soperator_raw_child_rows(outer, releases)
    by_upstream = {row["upstreamName"]: row for row in rows}
    spec = outer.get("spec")
    renderers = spec.get("postRenderers") if isinstance(spec, Mapping) else None
    if not isinstance(renderers, list):
        raise ValueError("rendered Soperator outer HelmRelease has no post-render contract")
    patched: set[str] = set()
    for renderer in renderers:
        kustomize = renderer.get("kustomize") if isinstance(renderer, Mapping) else None
        patches = kustomize.get("patches") if isinstance(kustomize, Mapping) else None
        if not isinstance(patches, list):
            continue
        for patch in patches:
            target = patch.get("target") if isinstance(patch, Mapping) else None
            target_name = (
                str(target.get("name") or "").strip() if isinstance(target, Mapping) else ""
            )
            row = by_upstream.get(target_name)
            if row is None:
                continue
            if target_name in patched:
                raise ValueError(
                    f"rendered Soperator child {target_name} has duplicate graph patches"
                )
            if (
                str(target.get("group") or "") != "helm.toolkit.fluxcd.io"
                or str(target.get("version") or "") != "v2"
                or str(target.get("kind") or "") != "HelmRelease"
            ):
                raise ValueError(
                    f"rendered Soperator child {target_name} has an invalid patch target"
                )
            operations = yaml.safe_load(str(patch.get("patch") or ""))
            if not isinstance(operations, list) or not all(
                isinstance(operation, Mapping) for operation in operations
            ):
                raise ValueError(
                    f"rendered Soperator child {target_name} has an invalid graph patch"
                )
            name_operations = [
                operation for operation in operations if operation.get("path") == "/metadata/name"
            ]
            if len(name_operations) != 1 or name_operations[0].get("value") != row["finalName"]:
                raise ValueError(
                    f"rendered Soperator child {target_name} has an invalid final identity"
                )
            normalized = [
                dict(operation)
                for operation in operations
                if operation.get("path")
                not in {
                    "/metadata/namespace",
                    "/spec/suspend",
                    "/spec/driftDetection/mode",
                }
            ]
            if row["upstreamName"] == _SOPERATOR_KRUISE_UPSTREAM_RELEASE:
                normalized.extend(
                    (
                        {
                            "op": "test",
                            "path": "/spec/driftDetection",
                            "value": {"mode": "warn"},
                        },
                        {
                            "op": "replace",
                            "path": "/spec/driftDetection",
                            "value": {
                                "mode": "enabled",
                                "ignore": [
                                    {
                                        "paths": [
                                            "/webhooks",
                                            "/metadata/annotations/template",
                                        ],
                                        "target": {
                                            "group": "admissionregistration.k8s.io",
                                            "version": "v1",
                                            "kind": "MutatingWebhookConfiguration",
                                            "name": ("kruise-mutating-webhook-configuration"),
                                        },
                                    },
                                    {
                                        "paths": [
                                            "/webhooks",
                                            "/metadata/annotations/template",
                                        ],
                                        "target": {
                                            "group": "admissionregistration.k8s.io",
                                            "version": "v1",
                                            "kind": "ValidatingWebhookConfiguration",
                                            "name": ("kruise-validating-webhook-configuration"),
                                        },
                                    },
                                ],
                            },
                        },
                    )
                )
            normalized.append(
                {
                    "op": "add",
                    "path": "/metadata/namespace",
                    "value": row["finalNamespace"],
                }
            )
            if suspend_children or row["upstreamName"] == _SOPERATOR_NAMESPACE_UPSTREAM_RELEASE:
                normalized.append({"op": "add", "path": "/spec/suspend", "value": True})
            target["name"] = row["rawName"]
            target.pop("namespace", None)
            patch["patch"] = yaml.safe_dump(normalized, sort_keys=False)
            patched.add(target_name)
    missing = sorted(set(by_upstream) - patched)
    if missing:
        raise ValueError(
            "rendered Soperator outer HelmRelease omits graph patches for: " + ", ".join(missing)
        )
    return rows


def _existing_soperator_release_frontier(
    payload: Mapping[str, Any],
    *,
    outer: Mapping[str, Any],
    rows: Sequence[Mapping[str, str]],
) -> set[tuple[str, str]]:
    """Return only exact, non-terminating target identities safe to suspend."""

    items = payload.get("items")
    if not isinstance(items, list) or not all(isinstance(item, Mapping) for item in items):
        raise RuntimeError("existing Soperator HelmRelease inventory is invalid")
    outer_metadata = outer.get("metadata")
    if not isinstance(outer_metadata, Mapping):
        raise ValueError("rendered Soperator outer HelmRelease identity is incomplete")
    outer_identity = (
        str(outer_metadata.get("namespace") or ""),
        str(outer_metadata.get("name") or ""),
    )
    raw_identities = {(str(row["rawNamespace"]), str(row["rawName"])) for row in rows}
    final_identities = {(str(row["finalNamespace"]), str(row["finalName"])) for row in rows}
    active: set[tuple[str, str]] = set()
    observed_raw: set[tuple[str, str]] = set()
    observed_final: set[tuple[str, str]] = set()
    for item in items:
        metadata = item.get("metadata")
        if not isinstance(metadata, Mapping):
            raise RuntimeError("existing Soperator HelmRelease metadata is invalid")
        identity = (
            str(metadata.get("namespace") or FLUX_NAMESPACE),
            str(metadata.get("name") or ""),
        )
        labels = metadata.get("labels")
        annotations = metadata.get("annotations")
        graph_owned = (
            isinstance(labels, Mapping)
            and labels.get(SOPERATOR_GRAPH_LABEL) == SOPERATOR_GRAPH_LABEL_VALUE
        )
        raw_owned = (
            isinstance(annotations, Mapping)
            and annotations.get("meta.helm.sh/release-name") == outer_identity[1]
            and annotations.get("meta.helm.sh/release-namespace") == outer_identity[0]
            and isinstance(labels, Mapping)
            and labels.get("helm.toolkit.fluxcd.io/name") == outer_identity[1]
            and labels.get("helm.toolkit.fluxcd.io/namespace") == outer_identity[0]
        )
        selected = False
        if identity == outer_identity:
            expected_spec = outer.get("spec")
            expected_chart_ref = (
                expected_spec.get("chartRef") if isinstance(expected_spec, Mapping) else None
            )
            observed_spec = item.get("spec")
            observed_chart_ref = (
                observed_spec.get("chartRef") if isinstance(observed_spec, Mapping) else None
            )
            if (
                not isinstance(labels, Mapping)
                or labels.get(SOPERATOR_LIFECYCLE_LABEL) != SOPERATOR_LIFECYCLE_RECREATABLE
                or not isinstance(expected_chart_ref, Mapping)
                or not isinstance(observed_chart_ref, Mapping)
                or any(
                    observed_chart_ref.get(field) != expected_chart_ref.get(field)
                    for field in ("kind", "name", "namespace")
                )
            ):
                raise SoperatorSafetyPauseError(
                    "the live Soperator outer HelmRelease is not the exact rendered target"
                )
            selected = True
        elif identity in raw_identities:
            if not raw_owned:
                raise SoperatorSafetyPauseError(
                    f"raw Soperator HelmRelease {identity[0]}/{identity[1]} is not owned "
                    "by the exact target umbrella"
                )
            status = item.get("status")
            spec = item.get("spec")
            if (
                not isinstance(spec, Mapping)
                or spec.get("suspend") is not True
                or (isinstance(status, Mapping) and bool(status))
            ):
                raise SoperatorSafetyPauseError(
                    f"raw Soperator HelmRelease {identity[0]}/{identity[1]} may have "
                    "reconciled and requires contaminated-target recovery"
                )
            observed_raw.add(identity)
            selected = True
        elif identity in final_identities:
            if not graph_owned:
                raise SoperatorSafetyPauseError(
                    f"target Soperator HelmRelease {identity[0]}/{identity[1]} lacks "
                    "the exact graph ownership label"
                )
            observed_final.add(identity)
            selected = True
        elif raw_owned or graph_owned:
            raise SoperatorSafetyPauseError(
                f"unexpected Soperator HelmRelease {identity[0]}/{identity[1]} is owned "
                "by the target graph"
            )
        if selected:
            if metadata.get("deletionTimestamp"):
                raise SoperatorSafetyPauseError(
                    f"Soperator HelmRelease {identity[0]}/{identity[1]} is terminating "
                    "and requires contaminated-target recovery"
                )
            else:
                active.add(identity)
    if observed_raw and observed_final:
        raise SoperatorSafetyPauseError("raw and normalized Soperator child inventories overlap")
    return active


def apply_staged_soperator_release(
    paths: ProjectPaths,
    *,
    extra_env: dict[str, str] | None = None,
    cache_dir: Path | None = None,
    timeout_seconds: int = 1800,
    poll_interval_seconds: float = 3.0,
    freeze_main_workload_authority: (
        Callable[[SoperatorMainWorkloadIdentity], SoperatorMainWorkloadIdentity] | None
    ) = None,
    on_stage_progress: Callable[[int, int, tuple[str, ...]], None] | None = None,
    prepare_main_release_stage: Callable[[], Mapping[str, object]] | None = None,
    finish_main_release_stage: (Callable[[Mapping[str, object], bool], None] | None) = None,
) -> tuple[SoperatorFluxSourceReceipt, ...]:
    """Materialize all children suspended, then open the locked graph stage by stage."""

    contract = _rendered_soperator_graph_contract(paths.flux_dir)
    if contract is None:
        raise ValueError("rendered Soperator release graph contract is missing")
    releases = contract.get("releases")
    if not isinstance(releases, list) or not releases:
        raise ValueError("rendered Soperator release graph contract is empty")
    main_rows = [
        item for item in releases if isinstance(item, Mapping) and item.get("isMain") is True
    ]
    if len(main_rows) != 1:
        raise ValueError("rendered Soperator release graph must contain exactly one main workload")
    if (prepare_main_release_stage is None) != (finish_main_release_stage is None):
        raise ValueError(
            "Soperator main-release preparation and restore callbacks must be provided together"
        )
    main_target: FluxWaitTarget | None = None
    if freeze_main_workload_authority is not None:
        main_targets = [
            target for target in _flux_wait_targets(paths.flux_dir) if target.is_soperator_main
        ]
        if len(main_targets) != 1:
            raise ValueError(
                "rendered Soperator release graph must contain exactly one main workload"
            )
        main_target = main_targets[0]
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    effective_cache = cache_dir or Path(tempfile.mkdtemp(prefix="nebius-cxcli-stage-cache-"))
    owns_cache = cache_dir is None
    try:
        build = subprocess.run(
            ["kubectl", "kustomize", str(paths.flux_dir)],
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if build.returncode != 0:
            detail = _first_non_empty_line(build.stderr or build.stdout or "")
            raise RuntimeError(
                "failed to render staged Soperator Flux bundle" + (f": {detail}" if detail else "")
            )
        final_documents = [
            item for item in yaml.safe_load_all(build.stdout) if isinstance(item, dict)
        ]
        staged_documents = copy.deepcopy(final_documents)
        staged_outer = _staged_soperator_outer_release(staged_documents, releases)
        staged_outer_metadata = staged_outer["metadata"]
        staged_outer_namespace = str(staged_outer_metadata["namespace"])
        staged_outer_name = str(staged_outer_metadata["name"])
        raw_child_rows = _normalize_soperator_outer_post_renderers(
            staged_outer,
            releases,
            suspend_children=True,
        )
        current_payload = _run_kubectl_json_process(
            [
                "kubectl",
                "--cache-dir",
                str(effective_cache),
                "get",
                "helmreleases.helm.toolkit.fluxcd.io",
                "-A",
                "-o",
                "json",
            ],
            env=env,
        )
        existing_releases = _existing_soperator_release_frontier(
            current_payload,
            outer=staged_outer,
            rows=raw_child_rows,
        )
        for namespace, name in sorted(existing_releases):
            _patch_helmrelease_suspend(
                name=name,
                namespace=namespace,
                suspend=True,
                cache_dir=effective_cache,
                env=env,
            )
        _wait_for_helmrelease_quiescence(
            existing_releases,
            cache_dir=effective_cache,
            env=env,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        outer_spec = staged_outer.setdefault("spec", {})
        outer_spec["suspend"] = False
        staged_apply = subprocess.run(
            ["kubectl", "--cache-dir", str(effective_cache), "apply", "-f", "-"],
            env=env,
            input=yaml.safe_dump_all(staged_documents, sort_keys=False),
            capture_output=True,
            text=True,
            timeout=600,
        )
        if staged_apply.returncode != 0:
            detail = _first_non_empty_line(staged_apply.stderr or staged_apply.stdout or "")
            raise RuntimeError(
                "failed to apply staged Soperator Flux bundle" + (f": {detail}" if detail else "")
            )
        _wait_for_suspended_child_inventory(
            contract,
            cache_dir=effective_cache,
            env=env,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        _patch_helmrelease_suspend(
            name=staged_outer_name,
            namespace=staged_outer_namespace,
            suspend=True,
            cache_dir=effective_cache,
            env=env,
        )
        stable_documents = copy.deepcopy(final_documents)
        stable_outer = _staged_soperator_outer_release(stable_documents, releases)
        _normalize_soperator_outer_post_renderers(
            stable_outer,
            releases,
            suspend_children=False,
        )
        stable_outer.setdefault("spec", {})["suspend"] = True
        stable_apply = subprocess.run(
            ["kubectl", "--cache-dir", str(effective_cache), "apply", "-f", "-"],
            env=env,
            input=yaml.safe_dump_all(stable_documents, sort_keys=False),
            capture_output=True,
            text=True,
            timeout=600,
        )
        if stable_apply.returncode != 0:
            detail = _first_non_empty_line(stable_apply.stderr or stable_apply.stdout or "")
            raise RuntimeError(
                "failed to apply stable Soperator control bundle"
                + (f": {detail}" if detail else "")
            )

        receipts: list[SoperatorFluxSourceReceipt] = []
        stages = sorted(
            {int(item.get("stage") or 0) for item in releases if isinstance(item, Mapping)}
        )
        for stage_index, stage in enumerate(stages, start=1):
            stage_items = [
                item
                for item in releases
                if isinstance(item, Mapping) and int(item.get("stage") or 0) == stage
            ]
            if on_stage_progress is not None:
                on_stage_progress(
                    stage_index,
                    len(stages),
                    tuple(sorted(str(item.get("releaseName") or "") for item in stage_items)),
                )
            source_names = {str(item.get("sourceName") or "") for item in stage_items}
            receipts.extend(
                prepare_soperator_release_sources(
                    paths,
                    extra_env=extra_env,
                    cache_dir=effective_cache,
                    timeout_seconds=min(timeout_seconds, 600),
                    poll_interval_seconds=poll_interval_seconds,
                    source_names=source_names,
                )
            )
            is_main_stage = any(item.get("isMain") is True for item in stage_items)
            prepared_main_stage: Mapping[str, object] | None = None
            release_opened = False
            if is_main_stage and prepare_main_release_stage is not None:
                prepared_main_stage = prepare_main_release_stage()
            try:
                for item in stage_items:
                    _remove_helmrelease_suspend(
                        name=str(item.get("releaseName") or ""),
                        namespace=str(item.get("namespace") or FLUX_NAMESPACE),
                        cache_dir=effective_cache,
                        env=env,
                    )
                _wait_for_soperator_release_stage(
                    contract,
                    stage,
                    cache_dir=effective_cache,
                    env=env,
                    timeout_seconds=timeout_seconds,
                    poll_interval_seconds=poll_interval_seconds,
                    main_target=main_target,
                    freeze_main_workload_authority=freeze_main_workload_authority,
                )
                release_opened = True
            finally:
                if prepared_main_stage is not None:
                    assert finish_main_release_stage is not None
                    finish_main_release_stage(prepared_main_stage, release_opened)
            if any(
                str(item.get("upstreamReleaseName") or "") == _SOPERATOR_KRUISE_UPSTREAM_RELEASE
                for item in stage_items
            ):
                _restore_soperator_kruise_statefulset_defaults(
                    cache_dir=effective_cache,
                    env=env,
                    timeout_seconds=timeout_seconds,
                    poll_interval_seconds=poll_interval_seconds,
                )
            _wait_for_soperator_dependency_health(
                stage_items,
                cache_dir=effective_cache,
                env=env,
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
            )
            protected_namespace_releases = {
                (
                    str(item.get("namespace") or FLUX_NAMESPACE),
                    str(item.get("releaseName") or ""),
                )
                for item in stage_items
                if str(item.get("upstreamReleaseName") or "")
                == _SOPERATOR_NAMESPACE_UPSTREAM_RELEASE
            }
            for namespace, name in sorted(protected_namespace_releases):
                _patch_helmrelease_suspend(
                    name=name,
                    namespace=namespace,
                    suspend=True,
                    cache_dir=effective_cache,
                    env=env,
                )
            _wait_for_helmrelease_quiescence(
                protected_namespace_releases,
                cache_dir=effective_cache,
                env=env,
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
            )

        _remove_helmrelease_suspend(
            name=staged_outer_name,
            namespace=staged_outer_namespace,
            cache_dir=effective_cache,
            env=env,
        )
        return tuple(receipts)
    finally:
        if owns_cache:
            shutil.rmtree(effective_cache, ignore_errors=True)


def prepare_soperator_adapter_storage(
    paths: ProjectPaths,
    *,
    extra_env: dict[str, str] | None = None,
    cache_dir: Path | None = None,
    timeout_seconds: int = 600,
    poll_interval_seconds: float = 3.0,
) -> None:
    """Apply and prove the boot-scoped storage barrier before product creation."""

    adapter_path = paths.flux_dir / "soperator-nebius-adapter.yaml"
    if not adapter_path.is_file():
        return
    documents = list(_iter_yaml_docs(adapter_path))
    if not documents:
        raise ValueError("the rendered Soperator adapter manifest is empty")
    allowed_lifecycles = {
        SOPERATOR_LIFECYCLE_PROTECTED,
        SOPERATOR_LIFECYCLE_RECREATABLE,
        SOPERATOR_LIFECYCLE_SHARED,
    }
    for document in documents:
        metadata = document.get("metadata")
        labels = metadata.get("labels") if isinstance(metadata, Mapping) else None
        if (
            not isinstance(labels, Mapping)
            or labels.get(SOPERATOR_ADAPTER_LABEL) != SOPERATOR_ADAPTER_LABEL_VALUE
            or labels.get(SOPERATOR_LIFECYCLE_LABEL) not in allowed_lifecycles
        ):
            raise ValueError("every Soperator adapter object must have an explicit lifecycle class")

    namespace = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": SOPERATOR_ADAPTER_NAMESPACE,
            "labels": {
                SOPERATOR_ADAPTER_LABEL: SOPERATOR_ADAPTER_LABEL_VALUE,
                SOPERATOR_LIFECYCLE_LABEL: SOPERATOR_LIFECYCLE_SHARED,
                "app.kubernetes.io/part-of": "soperator",
            },
        },
    }
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    effective_cache = cache_dir or Path(tempfile.mkdtemp(prefix="nebius-cxcli-adapter-cache-"))
    owns_cache = cache_dir is None
    try:
        apply = subprocess.run(
            ["kubectl", "--cache-dir", str(effective_cache), "apply", "-f", "-"],
            env=env,
            input=yaml.safe_dump_all([namespace, *documents], sort_keys=False),
            capture_output=True,
            text=True,
            timeout=300,
        )
        if apply.returncode != 0:
            detail = _first_non_empty_line(apply.stderr or apply.stdout or "")
            raise RuntimeError(
                "failed to apply the Soperator storage barrier" + (f": {detail}" if detail else "")
            )

        daemonsets = [
            str(document.get("metadata", {}).get("name") or "")
            for document in documents
            if document.get("kind") == "DaemonSet"
        ]
        if not daemonsets or any(not name for name in daemonsets):
            raise ValueError("the Soperator adapter must render named mount DaemonSets")
        deadline = time.monotonic() + timeout_seconds
        while True:
            ready = True
            for name in daemonsets:
                result = subprocess.run(
                    [
                        "kubectl",
                        "--cache-dir",
                        str(effective_cache),
                        "-n",
                        SOPERATOR_ADAPTER_NAMESPACE,
                        "get",
                        "daemonset",
                        name,
                        "-o",
                        "json",
                    ],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode != 0:
                    ready = False
                    continue
                try:
                    payload = json.loads(result.stdout or "{}")
                except json.JSONDecodeError:
                    ready = False
                    continue
                metadata = payload.get("metadata", {})
                status = payload.get("status", {})
                generation = int(metadata.get("generation") or 0)
                observed = int(status.get("observedGeneration") or 0)
                desired = int(status.get("desiredNumberScheduled") or 0)
                available = int(status.get("numberAvailable") or 0)
                if generation < 1 or observed != generation or desired < 1 or available != desired:
                    ready = False
            if ready:
                return
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "Soperator mount DaemonSets did not establish current-boot receipts "
                    "before product reconciliation"
                )
            time.sleep(max(0.1, poll_interval_seconds))
    finally:
        if owns_cache:
            shutil.rmtree(effective_cache, ignore_errors=True)


def verify_soperator_adapter_storage(
    paths: ProjectPaths,
    *,
    extra_env: dict[str, str] | None = None,
    expected_daemonsets: Sequence[str] | None = None,
) -> dict[str, object]:
    """Read-only postcondition for the boot-scoped storage barrier."""

    adapter_path = paths.flux_dir / "soperator-nebius-adapter.yaml"
    documents = list(_iter_yaml_docs(adapter_path)) if adapter_path.is_file() else []
    daemonsets = [
        str(document.get("metadata", {}).get("name") or "")
        for document in documents
        if document.get("kind") == "DaemonSet"
    ]
    if not daemonsets or any(not name for name in daemonsets):
        raise RuntimeError("Soperator storage barrier has no exact mount DaemonSet contract")
    if expected_daemonsets is not None:
        selected = tuple(str(name or "").strip() for name in expected_daemonsets)
        if (
            not selected
            or any(not name for name in selected)
            or len(set(selected)) != len(selected)
            or not set(selected) <= set(daemonsets)
        ):
            raise RuntimeError("Soperator storage barrier preimage DaemonSet contract is invalid")
        daemonsets = list(selected)
    env = os.environ.copy()
    env.update(extra_env or {})
    identities: list[dict[str, object]] = []
    for name in daemonsets:
        result = subprocess.run(
            [
                "kubectl",
                "-n",
                SOPERATOR_ADAPTER_NAMESPACE,
                "get",
                "daemonset",
                name,
                "-o",
                "json",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Soperator storage barrier DaemonSet {name} is unavailable")
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError("Soperator storage barrier returned invalid JSON") from exc
        metadata = payload.get("metadata") if isinstance(payload, Mapping) else None
        status = payload.get("status") if isinstance(payload, Mapping) else None
        metadata_map = metadata if isinstance(metadata, Mapping) else {}
        status_map = status if isinstance(status, Mapping) else {}
        generation = int(metadata_map.get("generation") or 0)
        observed = int(status_map.get("observedGeneration") or 0)
        desired = int(status_map.get("desiredNumberScheduled") or 0)
        available = int(status_map.get("numberAvailable") or 0)
        uid = str(metadata_map.get("uid") or "").strip()
        if (
            not uid
            or generation < 1
            or observed != generation
            or desired < 1
            or available != desired
        ):
            raise RuntimeError(
                f"Soperator storage barrier DaemonSet {name} is not current and fully available"
            )
        identities.append({"name": name, "uid": uid, "generation": generation})
    return {"status": "ready", "daemonsets": identities}


def _rendered_soperator_graph_contract(flux_dir: Path) -> dict[str, Any] | None:
    graph_path = flux_dir / "soperator-release-graph.yaml"
    if not graph_path.is_file():
        return None
    for document in _iter_yaml_docs(graph_path):
        if (
            document.get("kind") == "ConfigMap"
            and document.get("metadata", {}).get("name") == "nebius-cxcli-soperator-release-graph"
        ):
            raw = str(document.get("data", {}).get("graph.json") or "")
            try:
                contract = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError("rendered Soperator release graph is invalid") from exc
            if not isinstance(contract, dict):
                raise ValueError("rendered Soperator release graph must be an object")
            return contract
    raise ValueError("rendered Soperator release graph has no contract ConfigMap")


def rendered_soperator_graph_contract(flux_dir: Path) -> Mapping[str, Any] | None:
    """Load the frozen rendered graph for operation-bound ownership checks."""

    return _rendered_soperator_graph_contract(flux_dir)


def _kubectl_json(
    command: list[str],
    *,
    env: Mapping[str, str],
    timeout: int = 30,
) -> dict[str, Any] | None:
    result = subprocess.run(
        command,
        env=dict(env),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _generation_ready(payload: Mapping[str, Any]) -> bool:
    metadata = payload.get("metadata")
    status = payload.get("status")
    if not isinstance(metadata, Mapping) or not isinstance(status, Mapping):
        return False
    if status.get("observedGeneration") != metadata.get("generation"):
        return False
    return _condition_is_ready(payload)


def _nodeset_available(payload: Mapping[str, Any]) -> bool:
    """Validate the native Soperator v1alpha1 NodeSet readiness contract."""

    metadata = payload.get("metadata")
    spec = payload.get("spec")
    status = payload.get("status")
    generation = metadata.get("generation") if isinstance(metadata, Mapping) else None
    desired = spec.get("replicas") if isinstance(spec, Mapping) else None
    ready = status.get("replicas") if isinstance(status, Mapping) else None
    if (
        isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 1
        or isinstance(desired, bool)
        or not isinstance(desired, int)
        or desired < 1
        or isinstance(ready, bool)
        or not isinstance(ready, int)
    ):
        return False
    return status.get("phase") == "Ready" and ready == desired


def _slurm_cluster_available(payload: Mapping[str, Any]) -> bool:
    """Validate the native Soperator v1 SlurmCluster availability contract."""

    metadata = payload.get("metadata")
    status = payload.get("status")
    if (
        not isinstance(metadata, Mapping)
        or not isinstance(status, Mapping)
        or not isinstance(metadata.get("generation"), int)
        or metadata["generation"] < 1
        or status.get("phase") != "Available"
    ):
        return False
    conditions = status.get("conditions")
    if not isinstance(conditions, list):
        return False
    required = {
        "CommonAvailable",
        "ControllersAvailable",
        "LoginAvailable",
        "AccountingAvailable",
        "SConfigControllerAvailable",
    }
    observed: dict[str, str] = {}
    for condition in conditions:
        if not isinstance(condition, Mapping):
            return False
        condition_type = str(condition.get("type") or "")
        if condition_type in required:
            if condition_type in observed:
                return False
            observed[condition_type] = str(condition.get("status") or "").lower()
    return observed == {condition_type: "true" for condition_type in required}


def _active_check_available(payload: Mapping[str, Any]) -> bool:
    spec = payload.get("spec")
    status = payload.get("status")
    if not isinstance(spec, Mapping) or not isinstance(status, Mapping):
        return False
    check_type = spec.get("checkType") or "k8sJob"
    if check_type == "k8sJob":
        jobs_status = status.get("k8sJobsStatus")
        return isinstance(jobs_status, Mapping) and (jobs_status.get("lastJobStatus") == "Complete")
    if check_type == "slurmJob":
        jobs_status = status.get("slurmJobsStatus")
        return isinstance(jobs_status, Mapping) and (jobs_status.get("lastRunStatus") == "Complete")
    return False


def _required_active_checks_available(payloads: object) -> bool:
    if not isinstance(payloads, list):
        return False
    checked_count = 0
    for payload in payloads:
        if not isinstance(payload, Mapping):
            return False
        spec = payload.get("spec")
        if not isinstance(spec, Mapping):
            return False
        run_after_creation = spec.get("runAfterCreation", True)
        if not isinstance(run_after_creation, bool):
            return False
        if not run_after_creation:
            continue
        checked_count += 1
        if not _active_check_available(payload):
            return False
    return checked_count > 0


def _storage_backend_digest(spec: Mapping[str, Any]) -> tuple[str, str]:
    for backend in ("local", "nfs", "csi"):
        value = spec.get(backend)
        if isinstance(value, Mapping):
            digest = hashlib.sha256(
                json.dumps(dict(value), sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            return backend, digest
    return "", ""


def _validate_exact_storage_inventory(
    expected_rows: object,
    *,
    pvs: Mapping[str, Any],
    pvcs: Mapping[str, Any],
) -> tuple[bool, str]:
    if not isinstance(expected_rows, list) or not expected_rows:
        return True, ""
    expected_by_kind: dict[str, dict[tuple[str, str], Mapping[str, Any]]] = {
        "PersistentVolume": {},
        "PersistentVolumeClaim": {},
    }
    for row in expected_rows:
        if not isinstance(row, Mapping):
            return False, "readiness storage contract is invalid"
        kind = str(row.get("kind") or "")
        if kind not in expected_by_kind:
            return False, "readiness storage contract contains an unsupported kind"
        key = (str(row.get("namespace") or ""), str(row.get("name") or ""))
        if not key[1] or key in expected_by_kind[kind]:
            return False, "readiness storage contract has an invalid identity"
        expected_by_kind[kind][key] = row

    for kind, payload in (("PersistentVolume", pvs), ("PersistentVolumeClaim", pvcs)):
        items = payload.get("items")
        if not isinstance(items, list):
            return False, f"{kind} inventory is unavailable"
        actual: dict[tuple[str, str], Mapping[str, Any]] = {}
        for item in items:
            if not isinstance(item, Mapping):
                return False, f"{kind} inventory is invalid"
            metadata = item.get("metadata")
            if not isinstance(metadata, Mapping):
                return False, f"{kind} metadata is invalid"
            key = (
                str(metadata.get("namespace") or ""),
                str(metadata.get("name") or ""),
            )
            actual[key] = item
        if set(actual) != set(expected_by_kind[kind]):
            return False, f"{kind} inventory differs from the rendered adapter contract"
        for key, expected in expected_by_kind[kind].items():
            item = actual[key]
            metadata = item.get("metadata", {})
            spec = item.get("spec", {})
            if not str(metadata.get("uid") or ""):
                return False, f"{kind} {key[1]} has no stable UID"
            if str(spec.get("storageClassName") or "") != str(
                expected.get("storageClassName") or ""
            ):
                return False, f"{kind} {key[1]} has the wrong storage class"
            if kind == "PersistentVolumeClaim":
                if str(spec.get("volumeName") or "") != str(expected.get("volumeName") or ""):
                    return False, f"PersistentVolumeClaim {key[1]} has the wrong binding"
            else:
                if str(spec.get("persistentVolumeReclaimPolicy") or "") != str(
                    expected.get("reclaimPolicy") or ""
                ):
                    return False, f"PersistentVolume {key[1]} has the wrong reclaim policy"
                backend = expected.get("backend")
                if isinstance(backend, Mapping):
                    live_kind, live_digest = _storage_backend_digest(spec)
                    if (live_kind, live_digest) != (
                        str(backend.get("kind") or ""),
                        str(backend.get("identity") or ""),
                    ):
                        return False, f"PersistentVolume {key[1]} has the wrong backing identity"
    return True, ""


def _soperator_product_readiness(
    contract: Mapping[str, Any],
    *,
    env: Mapping[str, str],
) -> tuple[bool, str]:
    expected_rows = contract.get("releases")
    if not isinstance(expected_rows, list) or not expected_rows:
        return False, "release graph is empty"
    expected = {
        str(item.get("releaseName") or ""): item
        for item in expected_rows
        if isinstance(item, Mapping) and str(item.get("releaseName") or "")
    }
    releases = _kubectl_json(
        [
            "kubectl",
            "-n",
            FLUX_NAMESPACE,
            "get",
            "helmreleases.helm.toolkit.fluxcd.io",
            "-o",
            "json",
        ],
        env=env,
    )
    if releases is None or not isinstance(releases.get("items"), list):
        return False, "HelmRelease inventory is unavailable"
    owned: dict[str, Mapping[str, Any]] = {}
    family_names: set[str] = set()
    for item in releases["items"]:
        if not isinstance(item, Mapping):
            continue
        metadata = item.get("metadata")
        if not isinstance(metadata, Mapping):
            continue
        name = str(metadata.get("name") or "")
        if name.startswith("soperator-fluxcd-"):
            family_names.add(name)
        labels = metadata.get("labels")
        if (
            isinstance(labels, Mapping)
            and labels.get("soperator.nebius.ai/release-graph") == "nebius-cxcli"
        ):
            owned[name] = item
    if set(owned) != set(expected):
        missing = sorted(set(expected) - set(owned))
        unexpected = sorted(set(owned) - set(expected))
        return False, f"child HelmRelease set differs (missing={missing}, unexpected={unexpected})"
    foreign = family_names - set(expected)
    if foreign:
        return False, "unexpected Soperator HelmReleases exist: " + ", ".join(sorted(foreign))
    for name, row in expected.items():
        release = owned[name]
        if not _generation_ready(release):
            return False, f"HelmRelease {name} is not Ready at its observed generation"
        expected_suspended = (
            str(row.get("upstreamReleaseName") or "") == _SOPERATOR_NAMESPACE_UPSTREAM_RELEASE
        )
        observed_suspended = release.get("spec", {}).get("suspend") is True
        if observed_suspended != expected_suspended:
            return False, f"HelmRelease {name} is not in its declared suspension state"
        chart_ref = release.get("spec", {}).get("chartRef")
        expected_ref = {
            "kind": row.get("sourceKind"),
            "name": row.get("sourceName"),
            "namespace": FLUX_NAMESPACE,
        }
        if chart_ref != expected_ref or "chart" in release.get("spec", {}):
            return False, f"HelmRelease {name} is not bound to its locked chartRef"
        source_kind = str(row.get("sourceKind") or "")
        resource = "ocirepository" if source_kind == "OCIRepository" else "helmchart"
        source = _kubectl_json(
            [
                "kubectl",
                "-n",
                FLUX_NAMESPACE,
                "get",
                resource,
                str(row.get("sourceName") or ""),
                "-o",
                "json",
            ],
            env=env,
        )
        if source is None or not _condition_is_ready(source):
            return False, f"source for HelmRelease {name} is not Ready"
        artifact = _source_artifact(source)
        expected_revision = str(row.get("revision") or "")
        if source_kind == "OCIRepository":
            observed_revision = " ".join(
                str(artifact.get(key) or "") for key in ("revision", "digest")
            )
            if expected_revision not in observed_revision:
                return False, f"source for HelmRelease {name} has the wrong revision"
        elif str(artifact.get("digest") or "") != expected_revision:
            return False, f"source for HelmRelease {name} has the wrong package digest"

    daemonsets = _kubectl_json(
        [
            "kubectl",
            "-n",
            "soperator",
            "get",
            "daemonsets.apps",
            "-l",
            "soperator.nebius.ai/managed-by=nebius-cxcli-adapter",
            "-o",
            "json",
        ],
        env=env,
    )
    if (
        daemonsets is None
        or not isinstance(daemonsets.get("items"), list)
        or not daemonsets["items"]
    ):
        return False, "adapter DaemonSet inventory is unavailable"
    for daemonset in daemonsets["items"]:
        if not isinstance(daemonset, Mapping):
            return False, "adapter DaemonSet inventory is invalid"
        metadata = daemonset.get("metadata", {})
        status = daemonset.get("status", {})
        desired = status.get("desiredNumberScheduled") if isinstance(status, Mapping) else None
        if (
            not isinstance(metadata, Mapping)
            or not isinstance(status, Mapping)
            or status.get("observedGeneration") != metadata.get("generation")
            or not isinstance(desired, int)
            or desired < 1
            or status.get("numberReady") != desired
        ):
            return False, "adapter mount DaemonSets are not Ready"

    pvs = _kubectl_json(
        [
            "kubectl",
            "get",
            "persistentvolumes",
            "-l",
            "soperator.nebius.ai/managed-by=nebius-cxcli-adapter",
            "-o",
            "json",
        ],
        env=env,
    )
    if pvs is None or not isinstance(pvs.get("items"), list) or not pvs["items"]:
        return False, "protected PV inventory is unavailable"
    if any(
        not isinstance(item, Mapping)
        or item.get("spec", {}).get("persistentVolumeReclaimPolicy") != "Retain"
        or item.get("status", {}).get("phase") != "Bound"
        for item in pvs["items"]
    ):
        return False, "protected PVs are not Bound with Retain"

    pvcs = _kubectl_json(
        [
            "kubectl",
            "get",
            "persistentvolumeclaims",
            "-A",
            "-l",
            "soperator.nebius.ai/managed-by=nebius-cxcli-adapter",
            "-o",
            "json",
        ],
        env=env,
    )
    if pvcs is None or not isinstance(pvcs.get("items"), list) or not pvcs["items"]:
        return False, "protected PVC inventory is unavailable"
    if any(
        not isinstance(item, Mapping) or item.get("status", {}).get("phase") != "Bound"
        for item in pvcs["items"]
    ):
        return False, "protected PVCs are not Bound"

    readiness = contract.get("readiness")
    exact_storage = readiness.get("storage") if isinstance(readiness, Mapping) else None
    storage_ready, storage_detail = _validate_exact_storage_inventory(
        exact_storage,
        pvs=pvs,
        pvcs=pvcs,
    )
    if not storage_ready:
        return False, storage_detail

    pods = _kubectl_json(
        ["kubectl", "-n", "soperator", "get", "pods", "-o", "json"],
        env=env,
    )
    if pods is None or not isinstance(pods.get("items"), list) or not pods["items"]:
        return False, "Soperator Pod inventory is unavailable"
    for pod in pods["items"]:
        if not isinstance(pod, Mapping):
            return False, "Soperator Pod inventory is invalid"
        metadata = pod.get("metadata")
        status = pod.get("status")
        name = str(metadata.get("name") or "?") if isinstance(metadata, Mapping) else "?"
        phase = str(status.get("phase") or "") if isinstance(status, Mapping) else ""
        if phase == "Succeeded":
            continue
        conditions = status.get("conditions") if isinstance(status, Mapping) else None
        pod_ready = isinstance(conditions, list) and any(
            isinstance(condition, Mapping)
            and condition.get("type") == "Ready"
            and str(condition.get("status") or "").lower() == "true"
            for condition in conditions
        )
        if phase != "Running" or not pod_ready:
            return False, f"Soperator Pod {name} is not Ready or successfully Completed"

    cluster_name = str(contract.get("clusterName") or "soperator")
    cluster = _kubectl_json(
        [
            "kubectl",
            "-n",
            "soperator",
            "get",
            "slurmclusters.slurm.nebius.ai",
            cluster_name,
            "-o",
            "json",
        ],
        env=env,
    )
    if cluster is None or not _slurm_cluster_available(cluster):
        return False, f"SlurmCluster {cluster_name} is not Available"

    expected_roles = readiness.get("roles") if isinstance(readiness, Mapping) else None
    if isinstance(expected_roles, list) and expected_roles:
        cluster_spec = cluster.get("spec")
        slurm_nodes = cluster_spec.get("slurmNodes") if isinstance(cluster_spec, Mapping) else None
        if not isinstance(slurm_nodes, Mapping) or any(
            not isinstance(slurm_nodes.get(str(role)), Mapping) for role in expected_roles
        ):
            return False, "SlurmCluster does not contain every required service role"
        accounting = slurm_nodes.get("accounting")
        if isinstance(accounting, Mapping) and accounting.get("enabled") is not True:
            return False, "SlurmCluster accounting role is not enabled"

    expected_nodesets = readiness.get("nodeSets") if isinstance(readiness, Mapping) else None
    if isinstance(expected_nodesets, list) and expected_nodesets:
        nodeset_payload = _kubectl_json(
            [
                "kubectl",
                "-n",
                "soperator",
                "get",
                "nodesets.slurm.nebius.ai",
                "-o",
                "json",
            ],
            env=env,
        )
        nodeset_items = (
            nodeset_payload.get("items") if isinstance(nodeset_payload, Mapping) else None
        )
        if not isinstance(nodeset_items, list):
            return False, "NodeSet inventory is unavailable"
        actual_nodesets = {
            str(item.get("metadata", {}).get("name") or "")
            for item in nodeset_items
            if isinstance(item, Mapping)
        }
        if actual_nodesets != set(expected_nodesets):
            return False, "NodeSet inventory differs from the rendered role topology"
        if any(
            not isinstance(item, Mapping) or not _nodeset_available(item) for item in nodeset_items
        ):
            return False, "required NodeSets are not Available"

    active_checks_required = bool(
        isinstance(readiness, Mapping) and readiness.get("activeChecksRequired") is True
    )
    if active_checks_required:
        active_checks = _kubectl_json(
            [
                "kubectl",
                "-n",
                "soperator",
                "get",
                "activechecks.slurm.nebius.ai",
                "-o",
                "json",
            ],
            env=env,
        )
        items = active_checks.get("items") if isinstance(active_checks, Mapping) else None
        if not isinstance(items, list) or not items:
            return False, "required Soperator ActiveChecks are unavailable"
        if not _required_active_checks_available(items):
            return False, "required Soperator ActiveChecks are not Available"
    return True, "complete Soperator release graph and product are Ready"


def _object_observation(
    payload: Mapping[str, Any], *, default_namespace: str = ""
) -> tuple[str, str, str, int]:
    metadata = payload.get("metadata")
    if not isinstance(metadata, Mapping):
        raise RuntimeError("ready Soperator object has invalid metadata")
    return (
        str(metadata.get("namespace") or default_namespace),
        str(metadata.get("name") or ""),
        str(metadata.get("uid") or ""),
        int(metadata.get("generation") or 0),
    )


def _require_readiness_payload(
    command: list[str], *, env: Mapping[str, str], label: str
) -> dict[str, Any]:
    payload = _kubectl_json(command, env=env)
    if payload is None:
        raise RuntimeError(f"could not capture {label} readiness evidence")
    return payload


def _soperator_product_readiness_receipt(
    contract: Mapping[str, Any], *, env: Mapping[str, str]
) -> SoperatorProductReadinessReceipt:
    release_rows = contract.get("releases")
    if not isinstance(release_rows, list):
        raise RuntimeError("Soperator release graph is invalid")
    helm_release_payload = _require_readiness_payload(
        [
            "kubectl",
            "-n",
            FLUX_NAMESPACE,
            "get",
            "helmreleases.helm.toolkit.fluxcd.io",
            "-l",
            "soperator.nebius.ai/release-graph=nebius-cxcli",
            "-o",
            "json",
        ],
        env=env,
        label="HelmRelease",
    )
    helm_release_items = helm_release_payload.get("items")
    if not isinstance(helm_release_items, list):
        raise RuntimeError("could not capture HelmRelease readiness evidence")
    helm_releases = tuple(
        sorted(
            _object_observation(item, default_namespace=FLUX_NAMESPACE)
            for item in helm_release_items
            if isinstance(item, Mapping)
        )
    )
    sources: list[tuple[str, str, str, int]] = []
    source_keys = {
        (str(row.get("sourceKind") or ""), str(row.get("sourceName") or ""))
        for row in release_rows
        if isinstance(row, Mapping)
    }
    for kind, name in sorted(source_keys):
        resource = "ocirepository" if kind == "OCIRepository" else "helmchart"
        source = _require_readiness_payload(
            [
                "kubectl",
                "-n",
                FLUX_NAMESPACE,
                "get",
                resource,
                name,
                "-o",
                "json",
            ],
            env=env,
            label=f"{kind} {name}",
        )
        sources.append(_object_observation(source, default_namespace=FLUX_NAMESPACE))
    daemonsets = _require_readiness_payload(
        [
            "kubectl",
            "-n",
            "soperator",
            "get",
            "daemonsets.apps",
            "-l",
            "soperator.nebius.ai/managed-by=nebius-cxcli-adapter",
            "-o",
            "json",
        ],
        env=env,
        label="adapter DaemonSet",
    )
    pvs = _require_readiness_payload(
        [
            "kubectl",
            "get",
            "persistentvolumes",
            "-l",
            "soperator.nebius.ai/managed-by=nebius-cxcli-adapter",
            "-o",
            "json",
        ],
        env=env,
        label="protected PV",
    )
    pvcs = _require_readiness_payload(
        [
            "kubectl",
            "get",
            "persistentvolumeclaims",
            "-A",
            "-l",
            "soperator.nebius.ai/managed-by=nebius-cxcli-adapter",
            "-o",
            "json",
        ],
        env=env,
        label="protected PVC",
    )
    pods = _require_readiness_payload(
        ["kubectl", "-n", "soperator", "get", "pods", "-o", "json"],
        env=env,
        label="Soperator Pod",
    )
    cluster_name = str(contract.get("clusterName") or "soperator")
    cluster = _require_readiness_payload(
        [
            "kubectl",
            "-n",
            "soperator",
            "get",
            "slurmclusters.slurm.nebius.ai",
            cluster_name,
            "-o",
            "json",
        ],
        env=env,
        label="SlurmCluster",
    )
    readiness = contract.get("readiness")
    active_check_items: list[Any] = []
    if isinstance(readiness, Mapping) and readiness.get("activeChecksRequired") is True:
        active_checks = _require_readiness_payload(
            [
                "kubectl",
                "-n",
                "soperator",
                "get",
                "activechecks.slurm.nebius.ai",
                "-o",
                "json",
            ],
            env=env,
            label="ActiveCheck",
        )
        raw_active_checks = active_checks.get("items")
        if isinstance(raw_active_checks, list):
            active_check_items = raw_active_checks
    node_set_items: list[Any] = []
    if isinstance(readiness, Mapping) and readiness.get("nodeSets"):
        node_sets = _require_readiness_payload(
            [
                "kubectl",
                "-n",
                "soperator",
                "get",
                "nodesets.slurm.nebius.ai",
                "-o",
                "json",
            ],
            env=env,
            label="NodeSet",
        )
        raw_node_sets = node_sets.get("items")
        if isinstance(raw_node_sets, list):
            node_set_items = raw_node_sets
    storage: list[tuple[str, str, str, str]] = []
    for kind, payload in (("PersistentVolume", pvs), ("PersistentVolumeClaim", pvcs)):
        items = payload.get("items")
        if not isinstance(items, list):
            raise RuntimeError("could not capture exact storage readiness evidence")
        for item in items:
            if not isinstance(item, Mapping):
                continue
            namespace, name, uid, _generation = _object_observation(item)
            storage.append((kind, namespace, name, uid))
    pod_items = pods.get("items")
    if not isinstance(pod_items, list):
        raise RuntimeError("could not capture Pod readiness evidence")
    cluster_observation = _object_observation(cluster, default_namespace="soperator")
    return SoperatorProductReadinessReceipt(
        schema="nebius-cxcli.soperator-product-readiness.v1",
        release=str(contract.get("release") or ""),
        cluster_name=cluster_name,
        cluster_uid=cluster_observation[2],
        cluster_generation=cluster_observation[3],
        helm_releases=helm_releases,
        sources=tuple(sorted(sources)),
        adapter_daemonsets=tuple(
            sorted(
                _object_observation(item, default_namespace="soperator")
                for item in daemonsets.get("items", [])
                if isinstance(item, Mapping)
            )
        ),
        protected_storage=tuple(sorted(storage)),
        node_sets=tuple(
            sorted(
                _object_observation(item, default_namespace="soperator")
                for item in node_set_items
                if isinstance(item, Mapping)
            )
        ),
        pods=tuple(
            sorted(
                (
                    *_object_observation(item, default_namespace="soperator")[:3],
                    str(item.get("status", {}).get("phase") or ""),
                )
                for item in pod_items
                if isinstance(item, Mapping)
            )
        ),
        active_checks=tuple(
            sorted(
                _object_observation(item, default_namespace="soperator")
                for item in active_check_items
                if isinstance(item, Mapping)
            )
        ),
    )


_NATIVE_PROTECTED_PVCS = (
    "controller-spool-controller-0",
    "storage-soperator-acct-db-0",
)
_NATIVE_REQUIRED_SLURM_ROLES = frozenset({"accounting", "controller", "login"})


def _native_soperator_helm_release(item: Mapping[str, Any]) -> bool:
    metadata = item.get("metadata")
    spec = item.get("spec")
    name = str(metadata.get("name") or "") if isinstance(metadata, Mapping) else ""
    release_name = str(spec.get("releaseName") or "") if isinstance(spec, Mapping) else ""
    chart = spec.get("chart") if isinstance(spec, Mapping) else None
    chart_spec = chart.get("spec") if isinstance(chart, Mapping) else None
    chart_name = str(chart_spec.get("chart") or "") if isinstance(chart_spec, Mapping) else ""
    return "soperator" in " ".join((name, release_name, chart_name)).lower()


def _native_soperator_root_version(item: Mapping[str, Any]) -> str:
    metadata = item.get("metadata")
    spec = item.get("spec")
    name = str(metadata.get("name") or "") if isinstance(metadata, Mapping) else ""
    release_name = str(spec.get("releaseName") or "") if isinstance(spec, Mapping) else ""
    chart = spec.get("chart") if isinstance(spec, Mapping) else None
    chart_spec = chart.get("spec") if isinstance(chart, Mapping) else None
    chart_name = str(chart_spec.get("chart") or "") if isinstance(chart_spec, Mapping) else ""
    if "soperator-fluxcd" not in " ".join((name, release_name, chart_name)).lower():
        return ""
    return str(chart_spec.get("version") or "").strip() if isinstance(chart_spec, Mapping) else ""


def _native_soperator_observation(
    *, expected_release: str, env: Mapping[str, str]
) -> tuple[bool, str, SoperatorNativeReadinessReceipt | None]:
    helm_payload = _kubectl_json(
        [
            "kubectl",
            "get",
            "helmreleases.helm.toolkit.fluxcd.io",
            "-A",
            "-o",
            "json",
        ],
        env=env,
    )
    helm_items = helm_payload.get("items") if isinstance(helm_payload, Mapping) else None
    if not isinstance(helm_items, list):
        return False, "native Soperator HelmRelease inventory is unavailable", None
    soperator_releases = [
        item
        for item in helm_items
        if isinstance(item, Mapping) and _native_soperator_helm_release(item)
    ]
    if not soperator_releases:
        return False, "native Soperator HelmReleases are unavailable", None
    if any(
        not _generation_ready(item) or item.get("spec", {}).get("suspend") is True
        for item in soperator_releases
    ):
        return False, "native Soperator HelmReleases are not Ready", None
    root_versions = {
        version for item in soperator_releases if (version := _native_soperator_root_version(item))
    }
    if root_versions != {expected_release}:
        return (
            False,
            "native Soperator root HelmRelease is not pinned to " + expected_release,
            None,
        )

    clusters = _kubectl_json(
        [
            "kubectl",
            "-n",
            "soperator",
            "get",
            "slurmclusters.slurm.nebius.ai",
            "-o",
            "json",
        ],
        env=env,
    )
    cluster_items = clusters.get("items") if isinstance(clusters, Mapping) else None
    if not isinstance(cluster_items, list) or len(cluster_items) != 1:
        return False, "native Soperator must expose exactly one SlurmCluster", None
    cluster = cluster_items[0]
    if not isinstance(cluster, Mapping) or not _slurm_cluster_available(cluster):
        return False, "native Soperator SlurmCluster is not Available", None
    cluster_metadata = cluster.get("metadata")
    labels = cluster_metadata.get("labels") if isinstance(cluster_metadata, Mapping) else None
    observed_release = str(
        labels.get("app.kubernetes.io/version") if isinstance(labels, Mapping) else ""
    ).strip()
    if observed_release.lstrip("v") != expected_release.lstrip("v"):
        return False, "native Soperator SlurmCluster release is not the pinned release", None
    cluster_spec = cluster.get("spec")
    slurm_nodes = cluster_spec.get("slurmNodes") if isinstance(cluster_spec, Mapping) else None
    if not isinstance(slurm_nodes, Mapping) or not _NATIVE_REQUIRED_SLURM_ROLES.issubset(
        slurm_nodes
    ):
        return False, "native Soperator SlurmCluster service roles are incomplete", None
    accounting = slurm_nodes.get("accounting")
    if not isinstance(accounting, Mapping) or accounting.get("enabled") is not True:
        return False, "native Soperator accounting role is not enabled", None

    nodesets = _kubectl_json(
        [
            "kubectl",
            "-n",
            "soperator",
            "get",
            "nodesets.slurm.nebius.ai",
            "-o",
            "json",
        ],
        env=env,
    )
    nodeset_items = nodesets.get("items") if isinstance(nodesets, Mapping) else None
    if not isinstance(nodeset_items, list) or not nodeset_items:
        return False, "native Soperator worker NodeSets are unavailable", None
    if any(not isinstance(item, Mapping) or not _nodeset_available(item) for item in nodeset_items):
        return False, "native Soperator worker NodeSets are not Available", None

    pods = _kubectl_json(
        ["kubectl", "-n", "soperator", "get", "pods", "-o", "json"],
        env=env,
    )
    pod_items = pods.get("items") if isinstance(pods, Mapping) else None
    if not isinstance(pod_items, list) or not pod_items:
        return False, "native Soperator Pod inventory is unavailable", None
    for pod in pod_items:
        if not isinstance(pod, Mapping):
            return False, "native Soperator Pod inventory is invalid", None
        pod_status = pod.get("status")
        phase = str(pod_status.get("phase") or "") if isinstance(pod_status, Mapping) else ""
        if phase == "Succeeded":
            continue
        conditions = pod_status.get("conditions") if isinstance(pod_status, Mapping) else None
        ready = isinstance(conditions, list) and any(
            isinstance(condition, Mapping)
            and condition.get("type") == "Ready"
            and str(condition.get("status") or "").lower() == "true"
            for condition in conditions
        )
        if phase != "Running" or not ready:
            return False, "native Soperator Pods are not Ready", None

    active_checks = _kubectl_json(
        [
            "kubectl",
            "-n",
            "soperator",
            "get",
            "activechecks.slurm.nebius.ai",
            "-o",
            "json",
        ],
        env=env,
    )
    active_check_items = active_checks.get("items") if isinstance(active_checks, Mapping) else None
    if not isinstance(active_check_items, list) or not active_check_items:
        return False, "native Soperator ActiveChecks are unavailable", None
    if not _required_active_checks_available(active_check_items):
        return False, "native Soperator ActiveChecks are not Available", None

    protected_storage: list[tuple[str, str, str]] = []
    for pvc_name in _NATIVE_PROTECTED_PVCS:
        pvc = _kubectl_json(
            ["kubectl", "-n", "soperator", "get", "pvc", pvc_name, "-o", "json"],
            env=env,
        )
        pvc_metadata = pvc.get("metadata") if isinstance(pvc, Mapping) else None
        pvc_spec = pvc.get("spec") if isinstance(pvc, Mapping) else None
        pvc_status = pvc.get("status") if isinstance(pvc, Mapping) else None
        pv_name = str(pvc_spec.get("volumeName") or "") if isinstance(pvc_spec, Mapping) else ""
        pvc_bound = (
            isinstance(pvc_status, Mapping) and str(pvc_status.get("phase") or "") == "Bound"
        )
        if not isinstance(pvc_metadata, Mapping) or not pvc_bound or not pv_name:
            return False, f"protected PVC {pvc_name} is not Bound", None
        pv = _kubectl_json(["kubectl", "get", "pv", pv_name, "-o", "json"], env=env)
        pv_spec = pv.get("spec") if isinstance(pv, Mapping) else None
        pv_status = pv.get("status") if isinstance(pv, Mapping) else None
        if (
            not isinstance(pv_spec, Mapping)
            or pv_spec.get("persistentVolumeReclaimPolicy") != "Retain"
            or not isinstance(pv_status, Mapping)
            or pv_status.get("phase") != "Bound"
        ):
            return False, f"protected PV {pv_name} is not Bound with Retain", None
        protected_storage.append((pvc_name, str(pvc_metadata.get("uid") or ""), pv_name))

    cluster_observation = _object_observation(cluster, default_namespace="soperator")
    receipt = SoperatorNativeReadinessReceipt(
        schema="nebius-cxcli.soperator-native-readiness.v1",
        release=expected_release,
        cluster_name=cluster_observation[1],
        cluster_uid=cluster_observation[2],
        cluster_generation=cluster_observation[3],
        helm_releases=tuple(
            sorted(
                _object_observation(item)
                for item in soperator_releases
                if isinstance(item, Mapping)
            )
        ),
        protected_storage=tuple(sorted(protected_storage)),
        node_sets=tuple(
            sorted(
                _object_observation(item, default_namespace="soperator")
                for item in nodeset_items
                if isinstance(item, Mapping)
            )
        ),
        pods=tuple(
            sorted(
                (
                    *_object_observation(item, default_namespace="soperator")[:3],
                    str(item.get("status", {}).get("phase") or ""),
                )
                for item in pod_items
                if isinstance(item, Mapping)
            )
        ),
        active_checks=tuple(
            sorted(
                _object_observation(item, default_namespace="soperator")
                for item in active_check_items
                if isinstance(item, Mapping)
            )
        ),
    )
    return True, "upstream-native Soperator release and product are Ready", receipt


def wait_for_native_soperator_release(
    *,
    expected_release: str,
    extra_env: dict[str, str] | None = None,
    timeout_seconds: int = 5400,
    poll_interval_seconds: float = 10.0,
    emit: Callable[[str], None] | None = None,
) -> SoperatorNativeReadinessReceipt:
    """Observe an upstream-native release without requiring cxcli-owned resources."""

    normalized_release = str(expected_release or "").strip()
    if not normalized_release:
        raise ValueError("native Soperator readiness requires an exact expected release")
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    deadline = time.monotonic() + timeout_seconds
    last_detail = ""
    while True:
        ready, detail, receipt = _native_soperator_observation(
            expected_release=normalized_release,
            env=env,
        )
        if ready and receipt is not None:
            if emit:
                emit(detail)
            return receipt
        if emit and detail != last_detail:
            emit(f"Native Soperator readiness pending: {detail}")
            last_detail = detail
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "Native Soperator did not reach complete product readiness within "
                f"{timeout_seconds}s: {detail}"
            )
        time.sleep(max(0.1, poll_interval_seconds))


def wait_for_soperator_release_graph(
    paths: ProjectPaths,
    *,
    extra_env: dict[str, str] | None = None,
    timeout_seconds: int = 5400,
    poll_interval_seconds: float = 10.0,
    emit: Callable[[str], None] | None = None,
    include_active_checks: bool = True,
    main_workload_identity: SoperatorMainWorkloadIdentity | None = None,
) -> SoperatorProductReadinessReceipt | None:
    contract = _rendered_soperator_graph_contract(paths.flux_dir)
    if contract is None:
        return None
    if not include_active_checks:
        contract = dict(contract)
        readiness = contract.get("readiness")
        if isinstance(readiness, Mapping):
            readiness = dict(readiness)
            readiness["activeChecksRequired"] = False
            contract["readiness"] = readiness
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    deadline = time.monotonic() + timeout_seconds
    last_detail = ""
    main_targets = (
        [
            target
            for target in _flux_wait_targets(
                paths.flux_dir,
                main_workload_identity=main_workload_identity,
            )
            if target.is_soperator_main
        ]
        if main_workload_identity is not None
        else []
    )
    while True:
        if main_workload_identity is not None:
            if len(main_targets) != 1:
                raise SoperatorSafetyPauseError(
                    "rendered Soperator release graph has no unique main workload",
                    code="main-workload-authority-graph-invalid",
                )
            main_payload, main_detail = _kubectl_get_target(main_targets[0], env=env)
            main_status = _format_flux_target_summary(
                main_targets[0],
                main_payload,
                main_detail,
            )
            if main_status.is_terminal_failure:
                if main_payload is None:
                    raise AssertionError("terminal Flux status must have a live payload")
                identity = _exact_soperator_main_workload_identity(
                    main_targets[0],
                    main_payload,
                    env=env,
                )
                if identity is not None:
                    raise SoperatorMainWorkloadTerminalError(
                        "the graph-declared main Soperator workload reached an authoritative "
                        f"terminal condition: {main_status.failure_reason or 'Stalled'}",
                        identity=identity,
                        reason=main_status.failure_reason or "Stalled",
                    )
        ready, detail = _soperator_product_readiness(contract, env=env)
        if ready:
            receipt = _soperator_product_readiness_receipt(contract, env=env)
            ready_after_receipt, detail_after_receipt = _soperator_product_readiness(
                contract,
                env=env,
            )
            if ready_after_receipt:
                if emit:
                    emit(detail_after_receipt)
                return receipt
            detail = detail_after_receipt
        if emit and detail != last_detail:
            emit(f"Soperator readiness pending: {detail}")
            last_detail = detail
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"Soperator did not reach complete product readiness within {timeout_seconds}s: {detail}"
            )
        time.sleep(max(0.1, poll_interval_seconds))


def wait_for_soperator_noop_readiness(
    paths: ProjectPaths,
    *,
    expected_release: str,
    extra_env: dict[str, str] | None = None,
    timeout_seconds: int = 5400,
    poll_interval_seconds: float = 10.0,
    emit: Callable[[str], None] | None = None,
) -> SoperatorProductReadinessReceipt | SoperatorNativeReadinessReceipt:
    """Observe a no-op through its single topology-owned readiness authority."""

    normalized_release = str(expected_release or "").strip()
    if not normalized_release:
        raise ValueError("Soperator no-op readiness requires an exact expected release")
    graph_contract = _rendered_soperator_graph_contract(paths.flux_dir)
    if graph_contract is not None:
        if str(graph_contract.get("release") or "") != normalized_release:
            raise ValueError(
                "rendered Soperator graph release differs from the no-op target release"
            )
        receipt = wait_for_soperator_release_graph(
            paths,
            extra_env=extra_env,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            emit=emit,
        )
        if receipt is None:
            raise RuntimeError("rendered Soperator graph readiness authority disappeared")
        return receipt
    return wait_for_native_soperator_release(
        expected_release=normalized_release,
        extra_env=extra_env,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        emit=emit,
    )


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


def _flux_wait_targets(
    flux_dir: Path,
    *,
    main_workload_identity: SoperatorMainWorkloadIdentity | None = None,
) -> list[FluxWaitTarget]:
    graph_contract = _rendered_soperator_graph_contract(flux_dir)
    graph_rows: dict[tuple[str, str], Mapping[str, Any]] = {}
    if graph_contract is not None:
        if graph_contract.get("schema") != SOPERATOR_GRAPH_SCHEMA:
            raise ValueError(f"rendered Soperator release graph must use {SOPERATOR_GRAPH_SCHEMA}")
        releases = graph_contract.get("releases")
        if not isinstance(releases, list) or not releases:
            raise ValueError("rendered Soperator release graph has no releases")
        for row in releases:
            if not isinstance(row, Mapping):
                raise ValueError("rendered Soperator release graph contains an invalid release")
            graph_key = (
                str(row.get("namespace") or ""),
                str(row.get("releaseName") or ""),
            )
            if not all(graph_key) or graph_key in graph_rows:
                raise ValueError("rendered Soperator release graph has an invalid identity")
            graph_rows[graph_key] = row
        main_rows = [row for row in graph_rows.values() if row.get("isMain") is True]
        if len(main_rows) != 1:
            raise ValueError(
                "rendered Soperator release graph must contain exactly one main workload"
            )
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
            target_key = (resource_type, namespace, name)
            if target_key in seen:
                continue
            seen.add(target_key)
            graph_row = graph_rows.get((namespace, name))
            target = FluxWaitTarget(
                resource_type=resource_type,
                name=name,
                namespace=namespace,
                kind=kind,
                is_source=(group == _FLUX_SOURCE_GROUP),
                timeout_seconds=_target_manifest_timeout_seconds(
                    doc, is_source=(group == _FLUX_SOURCE_GROUP)
                ),
                api_version=api_version,
                is_soperator_graph_member=graph_row is not None,
                is_soperator_main=(graph_row or {}).get("isMain") is True,
                source_kind=str((graph_row or {}).get("sourceKind") or ""),
                source_name=str((graph_row or {}).get("sourceName") or ""),
                source_revision=str((graph_row or {}).get("revision") or ""),
                expected_main_identity=(
                    main_workload_identity if (graph_row or {}).get("isMain") is True else None
                ),
            )
            if group == _FLUX_SOURCE_GROUP:
                source_targets.append(target)
            else:
                workload_targets.append(target)
    if graph_rows:
        direct_workload_identities = {
            (target.namespace, target.name) for target in workload_targets
        }
        source_identities = {(target.namespace, target.name) for target in source_targets}
        graph_source_collisions = sorted(set(graph_rows) & source_identities)
        if graph_source_collisions:
            raise ValueError(
                "rendered Soperator release graph collides with a Flux source identity"
            )
        for (namespace, name), row in sorted(graph_rows.items()):
            if (namespace, name) in direct_workload_identities:
                continue
            workload_targets.append(
                FluxWaitTarget(
                    resource_type="helmrelease.helm.toolkit.fluxcd.io",
                    name=name,
                    namespace=namespace,
                    kind="HelmRelease",
                    is_source=False,
                    api_version="helm.toolkit.fluxcd.io/v2",
                    is_soperator_graph_member=True,
                    is_soperator_main=row.get("isMain") is True,
                    source_kind=str(row.get("sourceKind") or ""),
                    source_name=str(row.get("sourceName") or ""),
                    source_revision=str(row.get("revision") or ""),
                    expected_main_identity=(
                        main_workload_identity if row.get("isMain") is True else None
                    ),
                )
            )
        rendered_graph_targets = {
            (target.namespace, target.name)
            for target in workload_targets
            if target.is_soperator_graph_member
        }
        if rendered_graph_targets != set(graph_rows):
            raise ValueError(
                "rendered Soperator HelmRelease inventory differs from its frozen graph"
            )
        if main_workload_identity is not None:
            main_targets = [target for target in workload_targets if target.is_soperator_main]
            if len(main_targets) != 1:
                raise ValueError(
                    "rendered Soperator release graph must contain exactly one main workload"
                )
            target = main_targets[0]
            frozen_static = (
                main_workload_identity.api_version,
                main_workload_identity.kind,
                main_workload_identity.namespace,
                main_workload_identity.name,
                main_workload_identity.source_kind,
                main_workload_identity.source_name,
                main_workload_identity.source_revision,
            )
            rendered_static = (
                target.api_version,
                target.kind,
                target.namespace,
                target.name,
                target.source_kind,
                target.source_name,
                target.source_revision,
            )
            if rendered_static != frozen_static:
                raise SoperatorSafetyPauseError(
                    "frozen main-workload authority differs from the rendered release graph",
                    code="main-workload-authority-graph-changed",
                )
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
    try:
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return None, f"kubectl status read timed out after {timeout_seconds}s"
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


def _observe_soperator_main_workload_identity(
    target: FluxWaitTarget,
    payload: Mapping[str, Any],
    *,
    env: Mapping[str, str],
    require_ready: bool = False,
) -> SoperatorMainWorkloadIdentity | None:
    """Observe one complete graph-bound identity without accepting it as authority."""

    metadata = payload.get("metadata")
    spec = payload.get("spec")
    status = payload.get("status")
    if not all(isinstance(value, Mapping) for value in (metadata, spec, status)):
        raise SoperatorSafetyPauseError(
            "main Soperator HelmRelease terminal observation is structurally incomplete",
            code="main-workload-incomplete",
        )
    metadata_map = metadata if isinstance(metadata, Mapping) else {}
    spec_map = spec if isinstance(spec, Mapping) else {}
    status_map = status if isinstance(status, Mapping) else {}
    live_identity = (
        str(payload.get("apiVersion") or ""),
        str(payload.get("kind") or ""),
        str(metadata_map.get("namespace") or ""),
        str(metadata_map.get("name") or ""),
    )
    expected_identity = (target.api_version, target.kind, target.namespace, target.name)
    if live_identity != expected_identity:
        raise SoperatorSafetyPauseError(
            "main Soperator HelmRelease identity differs from the frozen release graph",
            code="main-workload-identity-changed",
        )
    uid = str(metadata_map.get("uid") or "").strip()
    try:
        generation = int(metadata_map.get("generation") or 0)
        observed_generation = int(status_map.get("observedGeneration") or 0)
    except (TypeError, ValueError) as exc:
        raise SoperatorSafetyPauseError(
            "main Soperator HelmRelease generation evidence is invalid",
            code="main-workload-generation-invalid",
        ) from exc
    if not uid or generation < 1 or observed_generation != generation:
        raise SoperatorSafetyPauseError(
            "main Soperator HelmRelease terminal condition is not bound to its current generation",
            code="main-workload-generation-stale",
        )
    if require_ready and not _condition_is_ready(payload):
        return None
    chart_ref = spec_map.get("chartRef")
    expected_ref = {
        "kind": target.source_kind,
        "name": target.source_name,
        "namespace": FLUX_NAMESPACE,
    }
    if chart_ref != expected_ref or "chart" in spec_map:
        raise SoperatorSafetyPauseError(
            "main Soperator HelmRelease source binding differs from the frozen release graph",
            code="main-workload-source-changed",
        )
    source_resource = {
        "OCIRepository": "ocirepository",
        "HelmChart": "helmchart",
    }.get(target.source_kind)
    if source_resource is None:
        raise SoperatorSafetyPauseError(
            "main Soperator HelmRelease has an unsupported frozen source kind",
            code="main-workload-source-kind-invalid",
        )
    source = _kubectl_json(
        [
            "kubectl",
            "-n",
            FLUX_NAMESPACE,
            "get",
            source_resource,
            target.source_name,
            "-o",
            "json",
        ],
        env=env,
    )
    if source is None or not _condition_is_ready(source):
        return None
    source_metadata = source.get("metadata")
    if not isinstance(source_metadata, Mapping) or (
        str(source.get("kind") or "") != target.source_kind
        or str(source_metadata.get("namespace") or "") != FLUX_NAMESPACE
        or str(source_metadata.get("name") or "") != target.source_name
    ):
        raise SoperatorSafetyPauseError(
            "main Soperator source identity differs from the frozen release graph",
            code="main-workload-source-identity-changed",
        )
    artifact = _source_artifact(source)
    revision = str(artifact.get("revision") or "")
    digest = str(artifact.get("digest") or "")
    revision_matches = target.source_revision in {revision, digest}
    if target.source_kind == "OCIRepository" and revision.endswith(f"@{target.source_revision}"):
        revision_matches = True
    if not revision_matches:
        raise SoperatorSafetyPauseError(
            "main Soperator source revision differs from the frozen release graph",
            code="main-workload-source-revision-changed",
        )
    return SoperatorMainWorkloadIdentity(
        api_version=target.api_version,
        kind=target.kind,
        namespace=target.namespace,
        name=target.name,
        source_kind=target.source_kind,
        source_name=target.source_name,
        source_revision=target.source_revision,
        uid=uid,
        generation=generation,
        observed_generation=observed_generation,
    )


def _exact_soperator_main_workload_identity(
    target: FluxWaitTarget,
    payload: Mapping[str, Any],
    *,
    env: Mapping[str, str],
) -> SoperatorMainWorkloadIdentity | None:
    """Return exact terminal authority, or pause on a contradictory observation."""

    observed = _observe_soperator_main_workload_identity(target, payload, env=env)
    if observed is None:
        return None
    expected = target.expected_main_identity
    if expected is not None and observed != expected:
        raise SoperatorSafetyPauseError(
            "main Soperator HelmRelease terminal identity differs from frozen authority",
            code="main-workload-authority-changed",
        )
    return observed


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
    if stalled is not None and _condition_status_true(stalled):
        reason = (
            str(stalled.get("reason", "")).strip()
            or str((ready or {}).get("reason", "")).strip()
            or "Stalled"
        )
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
        if (
            status.is_terminal_failure
            and target.is_soperator_main
            and target.expected_main_identity is not None
        ):
            if payload is None:
                raise AssertionError("terminal Flux status must have a live payload")
            identity = _exact_soperator_main_workload_identity(target, payload, env=env)
            if identity is None:
                status = replace(
                    status,
                    is_terminal_failure=False,
                    failure_reason="",
                    failure_message="",
                    summary=(
                        f"[yellow]{target.kind}[/yellow] {escape(_target_label(target))}: "
                        "[yellow]waiting[/yellow]; frozen source identity is not Ready"
                    ),
                )
            else:
                status = replace(status, main_workload_identity=identity)
        elif status.is_terminal_failure and target.is_soperator_graph_member:
            status = replace(
                status,
                is_terminal_failure=False,
                failure_reason="",
                failure_message="",
            )
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
    only_sources_pending = (
        bool(non_ready_statuses)
        and not terminal_failures
        and all(status.target.is_source for status in non_ready_statuses)
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
    main_workload_identity: SoperatorMainWorkloadIdentity | None = None,
) -> None:
    _require_binary("kubectl")
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    targets = _flux_wait_targets(
        paths.flux_dir,
        main_workload_identity=main_workload_identity,
    )
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
                    "[cyan]NOTE:[/cyan] Rendered HelmRelease workloads are Ready. Skipping the remaining wait for Flux "
                    "source objects that still have no Ready status.\n"
                    "Check HelmRelease status with:\n"
                    "kubectl get helmreleases.helm.toolkit.fluxcd.io -A"
                )
            return
        last_actionable_status = actionable_status
        last_terminal_failures = terminal_failures
        if any(status.main_workload_identity is not None for status in terminal_failures):
            break
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
        exact_main_failure = next(
            (
                status
                for status in last_terminal_failures
                if status.main_workload_identity is not None
            ),
            None,
        )
        if exact_main_failure is not None:
            reason = exact_main_failure.failure_reason or "Stalled"
            exact_main_identity = exact_main_failure.main_workload_identity
            if exact_main_identity is None:
                raise AssertionError("authoritative main failure must include its identity")
            raise SoperatorMainWorkloadTerminalError(
                "the graph-declared main Soperator workload reached an authoritative "
                f"terminal condition: {reason}",
                identity=exact_main_identity,
                reason=reason,
            )
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
        guidance = f"Flux resource {target_label} did not become Ready within {effective_timeout_seconds}s."
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
    emit: Callable[[str], None] | None = None,
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
    if not flux_crds_installed(extra_env=extra_env):
        if emit is not None:
            emit(
                "Flux resource APIs are not installed in the target cluster; "
                "skipping rendered Flux resource deletion."
            )
        return

    delete_documents: list[dict[str, Any]] = []
    preserved: list[str] = []
    recognized_lifecycles = {
        SOPERATOR_LIFECYCLE_PROTECTED,
        SOPERATOR_LIFECYCLE_RECREATABLE,
        SOPERATOR_LIFECYCLE_SHARED,
    }
    for manifest_path in _kustomization_resource_files(paths.flux_dir):
        for document in _iter_yaml_docs(manifest_path):
            metadata = document.get("metadata")
            if not isinstance(metadata, Mapping):
                continue
            labels = metadata.get("labels")
            label_map = labels if isinstance(labels, Mapping) else {}
            lifecycle = str(label_map.get(SOPERATOR_LIFECYCLE_LABEL) or "").strip()
            is_soperator = (
                label_map.get(SOPERATOR_ADAPTER_LABEL) == SOPERATOR_ADAPTER_LABEL_VALUE
                or label_map.get("soperator.nebius.ai/release-graph") == "nebius-cxcli"
                or (
                    document.get("kind") == "HelmRelease"
                    and metadata.get("name") == "soperator-fluxcd"
                )
                or metadata.get("name") == "terraform-fluxcd-values"
                or (
                    document.get("kind") == "Namespace"
                    and metadata.get("name") == SOPERATOR_ADAPTER_NAMESPACE
                )
            )
            if lifecycle and lifecycle not in recognized_lifecycles:
                raise ValueError(f"unknown Soperator lifecycle class {lifecycle!r}")
            if is_soperator and not lifecycle:
                raise ValueError(
                    "refusing teardown because a Soperator resource has no lifecycle class"
                )
            namespace = str(metadata.get("namespace") or "").strip()
            name = str(metadata.get("name") or "").strip()
            identity = f"{document.get('kind') or '?'} {namespace + '/' if namespace else ''}{name}"
            if lifecycle in {SOPERATOR_LIFECYCLE_PROTECTED, SOPERATOR_LIFECYCLE_SHARED} or (
                document.get("kind") == "Namespace" and name == FLUX_NAMESPACE
            ):
                preserved.append(identity)
                continue
            delete_documents.append(document)

    if preserved and emit is not None:
        emit("Preserving protected/shared rendered resources: " + ", ".join(sorted(preserved)))
    if not delete_documents:
        return

    delete_manifest = yaml.safe_dump_all(delete_documents, sort_keys=False)
    with tempfile.TemporaryDirectory(prefix="nebius-cxcli-kubectl-delete-") as cache_dir:
        cache_path = Path(cache_dir)
        delete_command = [
            "kubectl",
            "--cache-dir",
            str(cache_path),
            "delete",
            "-f",
            "-",
            "--ignore-not-found=true",
            "--wait=true",
            "--timeout=15m",
        ]
        completed = subprocess.run(
            delete_command,
            env=env,
            input=delete_manifest,
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
                delete_command,
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


def flux_install_manifest_url(*, version: str | None = None) -> str:
    selected_version = version or configured_flux_version()
    return f"{FLUX_RELEASES_URL}/download/{selected_version}/install.yaml"


def rendered_flux_api_version_gaps(
    paths: ProjectPaths,
    *,
    extra_env: dict[str, str] | None = None,
) -> tuple[str, ...]:
    """Return rendered Flux GVKs that the live CRDs do not currently serve."""

    required = {
        (target.api_version.split("/", maxsplit=1)[0], target.kind, target.api_version)
        for target in _flux_wait_targets(paths.flux_dir)
        if "/" in target.api_version
    }
    if not required:
        return ()
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    payload = _kubectl_json(
        [
            "kubectl",
            "get",
            "customresourcedefinitions.apiextensions.k8s.io",
            "-o",
            "json",
        ],
        env=env,
    )
    if payload is None:
        raise RuntimeError("could not inspect the live Flux CRD API versions")
    items = payload.get("items")
    if not isinstance(items, list):
        raise RuntimeError("the live Flux CRD inventory has an invalid shape")
    served: dict[tuple[str, str], set[str]] = {}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        spec = item.get("spec")
        if not isinstance(spec, Mapping):
            continue
        group = str(spec.get("group") or "").strip()
        names = spec.get("names")
        kind = str(names.get("kind") or "").strip() if isinstance(names, Mapping) else ""
        versions = spec.get("versions")
        if not group or not kind or not isinstance(versions, list):
            continue
        served[(group, kind)] = {
            str(version.get("name") or "").strip()
            for version in versions
            if isinstance(version, Mapping) and version.get("served") is True
        }
    gaps = []
    for group, kind, api_version in sorted(required):
        version = api_version.split("/", maxsplit=1)[1]
        if version not in served.get((group, kind), set()):
            gaps.append(f"{api_version}/{kind}")
    return tuple(gaps)


def _get_namespace_payload(
    namespace: str,
    *,
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    try:
        result = subprocess.run(
            ["kubectl", "get", "namespace", namespace, "-o", "json"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        return None
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
    try:
        result = subprocess.run(
            ["kubectl", "get", "crd", name, "-o", "json"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        return None
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


def _install_flux_controller_manifest(
    manifest_url: str,
    *,
    extra_env: dict[str, str] | None = None,
    progress: SoperatorProgressSink | None = None,
    phase_prefix: str = "flux-controller-install",
    phase_label: str = "Flux controllers",
) -> FluxApplySummary:
    _require_binary("kubectl")
    prerequisites_phase = f"{phase_prefix}-prerequisites"
    _emit_progress(
        progress,
        SoperatorProgressEvent(
            phase=prerequisites_phase,
            state=SoperatorProgressState.START,
            description=f"Waiting for {phase_label} prerequisites",
        ),
    )
    try:
        wait_for_flux_namespace_ready(extra_env=extra_env)
        wait_for_flux_crds_clear(extra_env=extra_env)
    except BaseException:
        _emit_progress(
            progress,
            SoperatorProgressEvent(
                phase=prerequisites_phase,
                state=SoperatorProgressState.FAILURE,
                description=f"{phase_label} prerequisites failed",
            ),
        )
        raise
    _emit_progress(
        progress,
        SoperatorProgressEvent(
            phase=prerequisites_phase,
            state=SoperatorProgressState.SUCCESS,
            description=f"{phase_label} prerequisites ready",
        ),
    )
    apply_phase = f"{phase_prefix}-manifest"
    _emit_progress(
        progress,
        SoperatorProgressEvent(
            phase=apply_phase,
            state=SoperatorProgressState.START,
            description=f"{phase_label} manifest",
        ),
    )
    try:
        apply_summary = _run_filtered_kubectl_apply(
            ["kubectl", "apply", "-f", manifest_url],
            timeout=600,
            extra_env=extra_env,
        )
    except BaseException:
        _emit_progress(
            progress,
            SoperatorProgressEvent(
                phase=apply_phase,
                state=SoperatorProgressState.FAILURE,
                description=f"{phase_label} manifest failed",
            ),
        )
        raise
    _emit_progress(
        progress,
        SoperatorProgressEvent(
            phase=apply_phase,
            state=SoperatorProgressState.SUCCESS,
            description=f"{phase_label} manifest applied — {apply_summary.detail()}",
        ),
    )
    rollout_phase = f"{phase_prefix}-rollout"
    total_deployments = len(FLUX_CORE_DEPLOYMENTS)
    try:
        for index, deployment in enumerate(FLUX_CORE_DEPLOYMENTS, start=1):
            state = SoperatorProgressState.START if index == 1 else SoperatorProgressState.UPDATE
            _emit_progress(
                progress,
                SoperatorProgressEvent(
                    phase=rollout_phase,
                    state=state,
                    description=f"{phase_label} rollout — {deployment}",
                    current=index,
                    total=total_deployments,
                ),
            )
            _run_captured(
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
    except BaseException:
        _emit_progress(
            progress,
            SoperatorProgressEvent(
                phase=rollout_phase,
                state=SoperatorProgressState.FAILURE,
                description=f"{phase_label} rollout failed",
            ),
        )
        raise
    wait_for_flux_crds_ready(extra_env=extra_env)
    _emit_progress(
        progress,
        SoperatorProgressEvent(
            phase=rollout_phase,
            state=SoperatorProgressState.SUCCESS,
            description=f"{phase_label} ready",
            current=total_deployments,
            total=total_deployments,
        ),
    )
    return apply_summary


def install_flux_controllers(
    *,
    extra_env: dict[str, str] | None = None,
    progress: SoperatorProgressSink | None = None,
    phase_prefix: str = "flux-controller-install",
    phase_label: str = "Flux controllers",
) -> str:
    manifest_url = flux_install_manifest_url()
    _install_flux_controller_manifest(
        manifest_url,
        extra_env=extra_env,
        progress=progress,
        phase_prefix=phase_prefix,
        phase_label=phase_label,
    )
    return manifest_url


def upgrade_flux_controllers_for_rendered_apis(
    paths: ProjectPaths,
    *,
    extra_env: dict[str, str] | None = None,
    assert_authority: Callable[[], object] | None = None,
    progress: SoperatorProgressSink | None = None,
) -> tuple[str, ...]:
    """Migrate old Flux APIs through the stable bridge before the configured upgrade."""

    initial_gaps = rendered_flux_api_version_gaps(paths, extra_env=extra_env)
    if not initial_gaps:
        return ()
    if assert_authority is not None:
        assert_authority()
    bridge_url = flux_install_manifest_url(version=FLUX_STABLE_API_BRIDGE_VERSION)
    _install_flux_controller_manifest(
        bridge_url,
        extra_env=extra_env,
        progress=progress,
        phase_prefix="flux-bridge",
        phase_label="Flux bridge controllers",
    )
    bridge_gaps = rendered_flux_api_version_gaps(paths, extra_env=extra_env)
    if bridge_gaps:
        raise RuntimeError(
            "the Flux stable-API bridge did not serve the rendered APIs: " + ", ".join(bridge_gaps)
        )
    if assert_authority is not None:
        assert_authority()
    flux_binary = resolve_flux_binary()
    migration_phase = "flux-custom-resource-migration"
    _emit_progress(
        progress,
        SoperatorProgressEvent(
            phase=migration_phase,
            state=SoperatorProgressState.START,
            description="Flux custom-resource migration",
        ),
    )
    try:
        migration_result = _run_captured(
            [flux_binary, "migrate"],
            timeout=600,
            extra_env=extra_env,
        )
    except BaseException:
        _emit_progress(
            progress,
            SoperatorProgressEvent(
                phase=migration_phase,
                state=SoperatorProgressState.FAILURE,
                description="Flux custom-resource migration failed",
            ),
        )
        raise
    migration_summary = _flux_migration_summary(
        "\n".join((migration_result.stdout or "", migration_result.stderr or ""))
    )
    _emit_progress(
        progress,
        SoperatorProgressEvent(
            phase=migration_phase,
            state=SoperatorProgressState.SUCCESS,
            description=f"Flux {migration_summary.detail()}",
        ),
    )
    if assert_authority is not None:
        assert_authority()
    configured_url = install_flux_controllers(
        extra_env=extra_env,
        progress=progress,
        phase_prefix="flux-target",
        phase_label="Flux target controllers",
    )
    remaining_gaps = rendered_flux_api_version_gaps(paths, extra_env=extra_env)
    if remaining_gaps:
        raise RuntimeError(
            "the configured Flux upgrade did not serve the rendered APIs: "
            + ", ".join(remaining_gaps)
        )
    configured_version = configured_flux_version()
    final_phase = "flux-upgrade-complete"
    _emit_progress(
        progress,
        SoperatorProgressEvent(
            phase=final_phase,
            state=SoperatorProgressState.START,
            description="Finalizing Flux API/controller upgrade",
        ),
    )
    _emit_progress(
        progress,
        SoperatorProgressEvent(
            phase=final_phase,
            state=SoperatorProgressState.SUCCESS,
            description=(
                "Flux APIs and controllers upgraded — "
                f"{FLUX_STABLE_API_BRIDGE_VERSION} → {configured_version}"
            ),
        ),
    )
    return bridge_url, configured_url


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

    required_types = {target.resource_type for target in _flux_wait_targets(paths.flux_dir)}
    required_types.update(resource_type for resource_type, _namespace in _FLUX_REQUIRED_API_TYPES)
    if not required_types:
        return

    deadline = time.monotonic() + timeout_seconds
    last_missing: list[str] = []
    while True:
        missing: list[str] = []
        for resource_type in sorted(required_types):
            cmd = ["kubectl"]
            if cache_dir is not None:
                cmd.extend(["--cache-dir", str(cache_dir)])
            cmd.extend(
                [
                    "get",
                    resource_type,
                    "-A",
                    "--ignore-not-found",
                ]
            )
            try:
                result = subprocess.run(
                    cmd,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
            except subprocess.TimeoutExpired:
                missing.append(resource_type)
                continue
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
