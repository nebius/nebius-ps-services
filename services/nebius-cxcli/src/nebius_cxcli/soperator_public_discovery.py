"""Config-independent, information-only Soperator discovery orchestration."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from rich.markdown import Markdown

from .mk8s_upgrade import Mk8sKubernetesVersionExecutor, live_node_group_from_sdk
from .nebius_api_helpers import bounded_nebius_request_kwargs
from .regions import SUPPORTED_REGION_IDS
from .sdk_auth import init_nebius_sdk
from .soperator_discovery import (
    build_soperator_public_discovery_report,
    soperator_public_discovery_snapshot_region,
    write_soperator_public_discovery_report,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def soperator_onboard_evidence_command_args(
    *,
    config_path: Path,
    cluster_id: str,
    target_ref: str,
    kube_context: str | None,
    access: str,
) -> tuple[str, ...]:
    """Return the independently repeatable command recorded with onboard evidence."""

    args = [
        "nebius-cxcli",
        "soperator",
        "onboard",
        str(config_path),
        "--cluster-id",
        cluster_id,
        "--target-id",
        target_ref,
        "--access",
        access,
    ]
    if _text(kube_context):
        args.extend(["--kube-context", str(kube_context)])
    args.append("--no-interactive")
    return tuple(args)


class SoperatorPublicDiscoveryRuntime:
    """Own provider binding, read-only collection, and public report output."""

    def __init__(
        self,
        *,
        handoff_spec_for_identity: Callable[..., Any],
        write_kubeconfig_file: Callable[[Path, Any], None],
        read_kubernetes_uid: Callable[..., str],
        collect_snapshot: Callable[..., Mapping[str, Any]],
        command_status: Callable[[str], Any],
        console: Any,
        build_report: Callable[..., dict[str, Any]] = build_soperator_public_discovery_report,
        write_report: Callable[..., tuple[Path, Path, str]] = (
            write_soperator_public_discovery_report
        ),
    ) -> None:
        self._handoff_spec_for_identity = handoff_spec_for_identity
        self._write_kubeconfig_file = write_kubeconfig_file
        self._read_kubernetes_uid = read_kubernetes_uid
        self._collect_snapshot = collect_snapshot
        self._command_status = command_status
        self._console = console
        self._build_report = build_report
        self._write_report = write_report

    @staticmethod
    def provider_observation(
        *,
        tenant_id: str,
        project_id: str,
        cluster_id: str,
    ) -> tuple[Any, dict[str, Any], list[dict[str, str]]]:
        """Bind tenant, project, and MK8s identity and collect provider version facts."""

        try:
            from nebius.api.nebius.iam.v1 import GetProjectRequest, ProjectServiceClient
        except Exception as exc:  # pragma: no cover - import guard
            raise RuntimeError(
                "Nebius IAM SDK bindings are required for Soperator discovery scope validation."
            ) from exc

        sdk = init_nebius_sdk(
            parent_id=project_id,
            endpoint=_text(os.environ.get("NEBIUS_ENDPOINT")) or None,
            context="Soperator discovery",
            prefer_operator_auth=True,
        )
        provider_errors: list[dict[str, str]] = []
        try:
            try:
                request_kwargs: Any = bounded_nebius_request_kwargs()
                project = (
                    ProjectServiceClient(sdk)
                    .get(
                        GetProjectRequest(id=project_id),
                        **request_kwargs,
                    )
                    .wait()
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Nebius project '{project_id}' does not exist or is not accessible."
                ) from exc
            project_metadata = getattr(project, "metadata", None)
            observed_project_id = _text(getattr(project_metadata, "id", None))
            observed_tenant_id = _text(getattr(project_metadata, "parent_id", None))
            if not observed_project_id or observed_project_id != project_id:
                raise RuntimeError(
                    "Soperator discovery project identity does not match --project-id."
                )
            if not observed_tenant_id or observed_tenant_id != tenant_id:
                raise RuntimeError(
                    f"Nebius project '{project_id}' does not belong to tenant '{tenant_id}'."
                )

            executor = Mk8sKubernetesVersionExecutor(sdk)
            try:
                cluster = executor.get_cluster(cluster_id)
            except Exception as exc:
                raise RuntimeError(
                    f"Nebius MK8s cluster '{cluster_id}' does not exist or is not accessible."
                ) from exc
            cluster_metadata = getattr(cluster, "metadata", None)
            observed_cluster_id = _text(getattr(cluster_metadata, "id", None))
            observed_cluster_project_id = _text(getattr(cluster_metadata, "parent_id", None))
            if not observed_cluster_id or observed_cluster_id != cluster_id:
                raise RuntimeError(
                    "Soperator discovery cluster identity does not match --cluster-id."
                )
            if not observed_cluster_project_id or observed_cluster_project_id != project_id:
                raise RuntimeError(
                    f"Nebius MK8s cluster '{cluster_id}' does not belong to project '{project_id}'."
                )

            raw_node_groups: tuple[Any, ...] = ()
            node_group_collection_failed = False
            try:
                raw_node_groups = tuple(executor.list_node_groups(cluster_id))
            except Exception:
                node_group_collection_failed = True
                provider_errors.append(
                    {
                        "collector": "nebius-mk8s-node-groups",
                        "message": "Nebius MK8s node-group inventory could not be collected.",
                    }
                )
            node_groups: list[dict[str, Any]] = []
            for raw_group in raw_node_groups:
                try:
                    group = live_node_group_from_sdk(raw_group)
                except (TypeError, ValueError):
                    node_group_collection_failed = True
                    provider_errors.append(
                        {
                            "collector": "nebius-mk8s-node-group",
                            "message": (
                                "One Nebius MK8s node group returned an unsupported version shape."
                            ),
                        }
                    )
                    continue
                raw_status = getattr(raw_group, "status", None)
                node_groups.append(
                    {
                        "id": group.id,
                        "name": group.name,
                        "kubernetes_version": group.version,
                        "platform": group.platform,
                        "preset": group.preset,
                        "os": group.os,
                        "drivers_preset": group.drivers_preset,
                        "gpu": group.gpu,
                        "actual_node_count": _non_negative_int(
                            getattr(raw_status, "node_count", None)
                        ),
                        "target_node_count": _non_negative_int(
                            getattr(raw_status, "target_node_count", None)
                        ),
                        "provider_ready_node_count": _non_negative_int(
                            getattr(raw_status, "ready_node_count", None)
                        ),
                        "outdated_node_count": _non_negative_int(
                            getattr(raw_status, "outdated_node_count", None)
                        ),
                        "state": _text(getattr(raw_status, "state", None)) or None,
                        "reconciling": (
                            getattr(raw_status, "reconciling", None)
                            if isinstance(getattr(raw_status, "reconciling", None), bool)
                            else None
                        ),
                        "resource_version": group.resource_version,
                    }
                )
            control_plane = getattr(getattr(cluster, "spec", None), "control_plane", None)
            provider = {
                "control_plane_version": _text(getattr(control_plane, "version", None)),
                "node_groups": node_groups,
                "collection_lanes": [
                    {
                        "name": "provider-node-groups",
                        "status": "failed" if node_group_collection_failed else "succeeded",
                        "item_count": len(node_groups),
                    }
                ],
            }
            return cluster, provider, provider_errors
        finally:
            with suppress(Exception):
                sdk.sync_close()

    @contextmanager
    def provider_context(
        self,
        *,
        project_id: str,
        cluster_id: str,
        access: str,
    ) -> Iterator[tuple[str, Mapping[str, str], str]]:
        """Yield provider-generated, temporary Kubernetes access for one MK8s id."""

        spec = self._handoff_spec_for_identity(
            project_id=project_id,
            client_name="",
            cluster_id=cluster_id,
            access=access,
        )
        with tempfile.TemporaryDirectory(prefix="nebius-cxcli-kube-") as kube_root:
            kubeconfig_path = Path(kube_root) / "config"
            self._write_kubeconfig_file(kubeconfig_path, spec)
            yield spec.context_name, {"KUBECONFIG": str(kubeconfig_path)}, spec.server

    @staticmethod
    def region(
        *,
        cluster: Any,
        provider_server: str,
        snapshot: Mapping[str, Any],
    ) -> str:
        """Derive one region from mutually agreeing provider/Kubernetes evidence."""

        candidates: set[str] = set()
        for source in (
            getattr(cluster, "metadata", None),
            getattr(cluster, "spec", None),
            getattr(cluster, "status", None),
        ):
            for field_name in ("region", "region_id"):
                value = _text(getattr(source, field_name, None))
                if value:
                    candidates.add(value)
            labels = getattr(source, "labels", None)
            if isinstance(labels, Mapping):
                for key in (
                    "region",
                    "region_id",
                    "nebius.com/region",
                    "topology.kubernetes.io/region",
                    "topology.nebius.com/region",
                ):
                    value = _text(labels.get(key))
                    if value:
                        candidates.add(value)
        server = _text(provider_server).lower()
        candidates.update(region for region in SUPPORTED_REGION_IDS if region in server)
        snapshot_region = soperator_public_discovery_snapshot_region(snapshot)
        if snapshot_region:
            candidates.add(snapshot_region)
        if len(candidates) > 1:
            raise RuntimeError(
                "Soperator discovery region evidence disagrees: " + ", ".join(sorted(candidates))
            )
        return next(iter(candidates), "")

    @staticmethod
    def _access(value: str) -> str:
        access = _text(value).lower().replace("_", "-") or "external"
        if access not in {"external", "internal"}:
            raise RuntimeError("MK8s access must be either external or internal.")
        return access

    @staticmethod
    def _expected_region(value: str | None) -> str:
        if value is None:
            return ""
        region = _text(value)
        if region not in SUPPORTED_REGION_IDS:
            available = ", ".join(SUPPORTED_REGION_IDS)
            raise RuntimeError(
                f"Unsupported region id {region or '<empty>'!r}. Expected one of: {available}"
            )
        return region

    @staticmethod
    def _snapshot_uid(snapshot: Mapping[str, Any], *, source_label: str) -> str:
        identity = snapshot.get("cluster_identity")
        uid = _text(identity.get("kubernetes_uid") if isinstance(identity, Mapping) else "")
        if not uid:
            raise RuntimeError(
                "Soperator discovery could not confirm "
                f"{source_label}: the kube-system namespace UID is unknown."
            )
        return uid

    def run(
        self,
        *,
        output_root: Path,
        tenant_id: str,
        project_id: str,
        cluster_id: str,
        region_id: str | None,
        kube_context: str | None,
        access: str,
    ) -> dict[str, Any]:
        """Collect, write, and render one scope-bound public discovery report."""

        normalized_tenant_id = _text(tenant_id)
        normalized_project_id = _text(project_id)
        normalized_cluster_id = _text(cluster_id)
        for option_name, value in (
            ("--tenant-id", normalized_tenant_id),
            ("--project-id", normalized_project_id),
            ("--cluster-id", normalized_cluster_id),
        ):
            if not value:
                raise RuntimeError(f"{option_name} must not be empty.")
        expected_region = self._expected_region(region_id)
        normalized_access = self._access(access)
        resolved_output_root = output_root.expanduser()
        if resolved_output_root.exists() and not resolved_output_root.is_dir():
            raise RuntimeError(
                f"Soperator discovery output root must be a directory: {output_root}"
            )

        with self._command_status("[cyan]Validating Nebius cluster identity...[/cyan]"):
            cluster, provider, provider_errors = self.provider_observation(
                tenant_id=normalized_tenant_id,
                project_id=normalized_project_id,
                cluster_id=normalized_cluster_id,
            )
        explicit_context = _text(kube_context)
        with self.provider_context(
            project_id=normalized_project_id,
            cluster_id=normalized_cluster_id,
            access=normalized_access,
        ) as (provider_context, provider_env, provider_server):
            if explicit_context:
                provider_uid = self._read_kubernetes_uid(
                    kube_context=provider_context,
                    extra_env=provider_env,
                )
                if not provider_uid:
                    raise RuntimeError(
                        "Soperator discovery could not bind provider-generated access to "
                        f"Nebius MK8s cluster '{normalized_cluster_id}'."
                    )
                with self._command_status(
                    "[cyan]Collecting read-only Soperator evidence...[/cyan]"
                ):
                    snapshot_payload = self._collect_snapshot(
                        kube_context=explicit_context,
                        require_complete_identity=False,
                    )
                explicit_uid = self._snapshot_uid(
                    snapshot_payload,
                    source_label=f"explicit --kube-context '{explicit_context}'",
                )
                if explicit_uid != provider_uid:
                    raise RuntimeError(
                        f"Explicit --kube-context '{explicit_context}' resolves to a different "
                        f"Kubernetes cluster than Nebius MK8s cluster '{normalized_cluster_id}'."
                    )
            else:
                with self._command_status(
                    "[cyan]Collecting read-only Soperator evidence...[/cyan]"
                ):
                    snapshot_payload = self._collect_snapshot(
                        kube_context=provider_context,
                        extra_env=provider_env,
                        require_complete_identity=False,
                    )
            if not isinstance(snapshot_payload, Mapping):
                raise RuntimeError("Soperator discovery collector returned an invalid snapshot.")
            snapshot = dict(snapshot_payload)
            observed_region = self.region(
                cluster=cluster,
                provider_server=provider_server,
                snapshot=snapshot,
            )
        if expected_region and observed_region != expected_region:
            observed_label = observed_region or "unknown"
            raise RuntimeError(
                f"Soperator discovery --region-id '{expected_region}' does not match live "
                f"cluster region '{observed_label}'."
            )

        collection_errors = snapshot.get("collection_errors")
        if not isinstance(collection_errors, list):
            collection_errors = []
            snapshot["collection_errors"] = collection_errors
        collection_errors.extend(provider_errors)
        cluster_metadata = getattr(cluster, "metadata", None)
        report = self._build_report(
            tenant_id=normalized_tenant_id,
            project_id=normalized_project_id,
            cluster_id=normalized_cluster_id,
            cluster_name=_text(getattr(cluster_metadata, "name", None)),
            region_id=observed_region,
            access=normalized_access,
            snapshot=snapshot,
            provider=provider,
        )
        json_path, markdown_path, markdown = self._write_report(
            resolved_output_root,
            report=report,
        )
        self._console.print(Markdown(markdown, hyperlinks=False))
        self._console.print(f"Soperator discovery status: {report['status']}")
        self._console.print(f"Soperator discovery JSON: {json_path}", soft_wrap=True)
        self._console.print(f"Soperator discovery Markdown: {markdown_path}", soft_wrap=True)
        return report
