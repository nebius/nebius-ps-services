"""Best-effort deployment status reporting during long-running apply operations."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from nebius.api.nebius.common.v1 import GetOperationRequest, ListOperationsRequest
from nebius.api.nebius.mk8s.v1 import (
    ClusterServiceClient,
    ClusterStatus,
    ListClustersRequest,
    ListNodeGroupsRequest,
    NodeGroupServiceClient,
    NodeGroupStatus,
)
from rich.markup import escape

from .component_instances import component_instance_label
from .runtime_config import to_plain_data
from .sdk_auth import init_nebius_sdk


def _as_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _format_elapsed(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    minutes, seconds_part = divmod(total_seconds, 60)
    hours, minutes_part = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes_part:02d}m{seconds_part:02d}s"
    if minutes:
        return f"{minutes}m{seconds_part:02d}s"
    return f"{seconds_part}s"


def _shorten(text: str, *, limit: int = 72) -> str:
    value = _as_text(text)
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _event_level_rank(level: str) -> int:
    normalized = _as_text(level).upper()
    if normalized == "ERROR":
        return 3
    if normalized == "WARN":
        return 2
    if normalized == "INFO":
        return 1
    return 0


def _is_transient_node_group_warning(*, level: str, message: str, state_name: str) -> bool:
    if _as_text(level).upper() != "WARN":
        return False
    if _as_text(state_name).upper() not in {"PROVISIONING", "CREATING"}:
        return False
    normalized = _as_text(message).lower()
    return (
        "node condition ready is false" in normalized
        or "waiting for a node with matching providerid to exist" in normalized
    )


def _transient_node_group_note(message: str) -> str | None:
    normalized = _as_text(message).lower()
    if "waiting for a node with matching providerid to exist" in normalized:
        return "waiting for node registration"
    if "node condition ready is false" in normalized:
        return "waiting for node readiness"
    return None


@dataclass(frozen=True)
class Mk8sDeploymentTarget:
    project_id: str
    cluster_name: str


@dataclass(frozen=True)
class StatusWatcherTarget:
    component_id: str
    kind: str
    parent_id: str
    resource_name: str
    instance_id: str = ""


def _mk8s_deployment_target(config: Any) -> Mk8sDeploymentTarget | None:
    from .components import component_lookup

    payload = to_plain_data(config)
    if not isinstance(payload, dict):
        return None
    project_id = _as_text(payload.get("client_info", {}).get("nebius", {}).get("project_id"))
    if not project_id:
        return None
    infra = payload.get("infra")
    if not isinstance(infra, dict):
        return None
    components = infra.get("components")
    if not isinstance(components, list):
        return None

    entry_by_id = component_lookup("infra")
    for item in components:
        if not isinstance(item, Mapping) or not bool(item.get("enabled", False)):
            continue
        component_id = _as_text(item.get("id")).lower()
        entry = entry_by_id.get(component_id)
        if entry is None or getattr(entry, "handoff", None) is None:
            continue
        inputs = item.get("inputs")
        if not isinstance(inputs, Mapping):
            continue
        # Use status.name_input to find the cluster name field dynamically.
        name_input = "cluster_name"
        status = getattr(entry, "status", None)
        if status is not None:
            name_input = getattr(status, "name_input", "name") or "name"
        cluster_name = _as_text(inputs.get(name_input))
        if not cluster_name:
            continue
        return Mk8sDeploymentTarget(project_id=project_id, cluster_name=cluster_name)
    return None


def _resource_metadata_name(resource: Any) -> str:
    metadata = getattr(resource, "metadata", None)
    return _as_text(getattr(metadata, "name", None))


def _resource_metadata_id(resource: Any) -> str:
    metadata = getattr(resource, "metadata", None)
    return _as_text(getattr(metadata, "id", None) or getattr(resource, "id", None))


def _response_collection(response: Any, *field_names: str) -> list[Any]:
    for field_name in field_names:
        values = getattr(response, field_name, None)
        if values is not None:
            return list(values or [])
    return []


def _enum_field_name(message: Any, field_name: str, *, prefixes: tuple[str, ...] = ()) -> str:
    if message is None:
        return "UNKNOWN"
    raw_value = getattr(message, field_name, None)
    if isinstance(raw_value, Enum):
        name = _as_text(getattr(raw_value, "name", None)).upper()
        for prefix in prefixes:
            if name.startswith(prefix):
                name = name[len(prefix) :]
                break
        return name or "UNKNOWN"
    descriptor = getattr(message, "DESCRIPTOR", None)
    field = getattr(descriptor, "fields_by_name", {}).get(field_name) if descriptor is not None else None
    enum_type = getattr(field, "enum_type", None)
    if enum_type is not None:
        with suppress(Exception):
            value = enum_type.values_by_number.get(int(raw_value))
            if value is not None:
                name = _as_text(value.name).upper()
                for prefix in prefixes:
                    if name.startswith(prefix):
                        name = name[len(prefix) :]
                        break
                return name or "UNKNOWN"
    text = _as_text(raw_value).upper()
    if not text:
        return "UNKNOWN"
    for prefix in prefixes:
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    return text or "UNKNOWN"


def _latest_operation_summary(operation_service: Any, resource_id: str) -> str | None:
    response = operation_service.list(ListOperationsRequest(resource_id=resource_id)).wait()
    operations = list(getattr(response, "operations", []) or [])
    if not operations:
        return None

    latest = operations[0]
    latest_created = getattr(latest, "created_at", None)
    for item in operations[1:]:
        created_at = getattr(item, "created_at", None)
        if created_at and (not latest_created or created_at > latest_created):
            latest = item
            latest_created = created_at

    operation_id = _as_text(getattr(latest, "id", None))
    description = _as_text(getattr(latest, "description", None)) or "operation"
    age_summary = ""
    if latest_created is not None:
        with suppress(Exception):
            age_summary = _format_elapsed(
                max(0.0, (datetime.now(latest_created.tzinfo) - latest_created).total_seconds())
            )
    done_summary = "state unknown"
    with suppress(Exception):
        full = operation_service.get(GetOperationRequest(id=operation_id)).wait()
        done_summary = "done" if full.done() else "running"
    parts = [description]
    if operation_id:
        parts.append(operation_id)
    parts.append(done_summary)
    if age_summary:
        parts.append(age_summary)
    return "op " + " ".join(parts)


class _HeartbeatPoller:
    def summary(self) -> str:
        return "API unavailable; heartbeat only"

    def close(self) -> None:
        return


class _Mk8sStatusPoller:
    def __init__(self, target: Mk8sDeploymentTarget) -> None:
        self._target = target
        self._cluster_id: str | None = None
        self._auth_warning: str | None = None

        self._sdk = init_nebius_sdk(
            parent_id=target.project_id,
            context="deployment status polling",
        )
        self._cluster_client = ClusterServiceClient(self._sdk)
        self._node_group_client = NodeGroupServiceClient(self._sdk)

    def _cluster_state_name(self, raw_state: Any) -> str:
        try:
            return ClusterStatus.State(int(raw_state)).name
        except Exception:
            text = _as_text(raw_state)
            return text or "UNKNOWN"

    def _node_group_state_name(self, raw_state: Any) -> str:
        try:
            return NodeGroupStatus.State(int(raw_state)).name
        except Exception:
            text = _as_text(raw_state)
            return text or "UNKNOWN"

    def _find_cluster(self):
        response = self._cluster_client.list(
            ListClustersRequest(parent_id=self._target.project_id)
        ).wait()
        items = _response_collection(response, "items", "clusters")
        for item in items:
            metadata = getattr(item, "metadata", None)
            if _as_text(getattr(metadata, "name", None)) == self._target.cluster_name:
                return item
        return None

    def _list_node_groups(self, cluster_id: str) -> list[Any]:
        response = self._node_group_client.list(
            ListNodeGroupsRequest(parent_id=cluster_id)
        ).wait()
        return _response_collection(response, "items", "node_groups")

    def _node_group_event_summaries(self, item: Any, *, state_name: str) -> tuple[str | None, tuple[str, ...]]:
        status = getattr(item, "status", None)
        events = list(getattr(status, "events", []) or [])
        best_level = ""
        best_message = ""
        transient_notes: list[str] = []
        for event in events:
            last_occurrence = getattr(event, "last_occurrence", None)
            level = _as_text(getattr(last_occurrence, "level", None)).upper()
            if _event_level_rank(level) < _event_level_rank("WARN"):
                continue
            message = _as_text(getattr(last_occurrence, "message", None))
            if not message:
                continue
            if _is_transient_node_group_warning(level=level, message=message, state_name=state_name):
                note = _transient_node_group_note(message)
                if note and note not in transient_notes:
                    transient_notes.append(note)
                continue
            if _event_level_rank(level) > _event_level_rank(best_level):
                best_level = level
                best_message = message
        if not best_level or not best_message:
            return None, tuple(transient_notes)
        metadata = getattr(item, "metadata", None)
        name = _as_text(getattr(metadata, "name", None)) or _as_text(
            getattr(metadata, "id", None) or getattr(item, "id", None)
        )
        return f"{best_level} {name}: {_shorten(best_message, limit=96)}", tuple(transient_notes)

    def _latest_operation_summary(self, cluster_id: str) -> str | None:
        return _latest_operation_summary(self._cluster_client.operation_service(), cluster_id)

    def summary(self) -> str:
        cluster = self._find_cluster()
        if cluster is None:
            return (
                f"mk8s cluster '{self._target.cluster_name}' is not visible yet in "
                f"project {self._target.project_id}."
            )

        metadata = getattr(cluster, "metadata", None)
        status = getattr(cluster, "status", None)
        cluster_id = _as_text(getattr(metadata, "id", None) or getattr(cluster, "id", None))
        if cluster_id:
            self._cluster_id = cluster_id
        state_name = self._cluster_state_name(getattr(status, "state", None))
        control_plane = getattr(status, "control_plane", None)
        endpoints = getattr(control_plane, "endpoints", None)
        public_endpoint = _as_text(getattr(endpoints, "public_endpoint", None))
        private_endpoint = _as_text(getattr(endpoints, "private_endpoint", None))
        endpoint_summary = (
            "public endpoint ready"
            if public_endpoint
            else "private endpoint ready"
            if private_endpoint
            else "endpoints pending"
        )
        operation_summary = self._latest_operation_summary(cluster_id) if cluster_id else None

        node_groups = self._list_node_groups(cluster_id) if cluster_id else []
        if not node_groups:
            node_group_summary = "node groups 0"
            alert_summary = ""
            note_summary = ""
        else:
            summaries: list[str] = []
            alerts: list[str] = []
            notes: list[str] = []
            for item in node_groups:
                group_meta = getattr(item, "metadata", None)
                group_status = getattr(item, "status", None)
                name = _as_text(getattr(group_meta, "name", None)) or _as_text(
                    getattr(group_meta, "id", None) or getattr(item, "id", None)
                )
                state = self._node_group_state_name(getattr(group_status, "state", None))
                ready = getattr(group_status, "ready_node_count", None)
                target = getattr(group_status, "target_node_count", None)
                if ready is not None and target is not None:
                    summaries.append(f"{name}:{state} {ready}/{target} ready")
                else:
                    summaries.append(f"{name}:{state}")
                alert, transient_notes = self._node_group_event_summaries(item, state_name=state)
                if alert:
                    alerts.append(alert)
                if transient_notes:
                    rendered_notes = "/".join(transient_notes)
                    note_text = f"{name}: {rendered_notes}"
                    if note_text not in notes:
                        notes.append(note_text)
            node_group_summary = "node groups " + ", ".join(summaries)
            alert_summary = f"; alerts {' | '.join(alerts[:2])}" if alerts else ""
            note_summary = f"; notes {' | '.join(notes[:2])}" if notes else ""

        cluster_label = self._target.cluster_name
        if cluster_id:
            cluster_label = f"{cluster_label} ({cluster_id})"
        parts = [
            f"mk8s {cluster_label}: {state_name}",
            endpoint_summary,
            node_group_summary,
        ]
        if operation_summary:
            parts.append(operation_summary)
        rendered = "; ".join(parts) + "."
        return f"{rendered}{note_summary}{alert_summary}"

    def close(self) -> None:
        self._sdk.sync_close()


class _PostgreSQLStatusPoller:
    def __init__(self, target: StatusWatcherTarget) -> None:
        self._target = target
        self._sdk = init_nebius_sdk(
            parent_id=target.parent_id,
            context="deployment status polling",
        )

        from nebius.api.nebius.msp.postgresql.v1alpha1 import (
            ClusterServiceClient,
            ListClustersRequest,
        )

        self._cluster_client = ClusterServiceClient(self._sdk)
        self._list_request = ListClustersRequest

    def _find_cluster(self):
        response = self._cluster_client.list(
            self._list_request(parent_id=self._target.parent_id)
        ).wait()
        items = _response_collection(response, "items", "clusters")
        for item in items:
            if _resource_metadata_name(item) == self._target.resource_name:
                return item
        return None

    def summary(self) -> str:
        cluster = self._find_cluster()
        if cluster is None:
            return (
                f"managed-postgresql '{self._target.resource_name}' is not visible yet in "
                f"project {self._target.parent_id}."
            )

        status = getattr(cluster, "status", None)
        cluster_id = _resource_metadata_id(cluster)
        phase = _enum_field_name(status, "phase", prefixes=("PHASE_",))
        state = _enum_field_name(status, "state", prefixes=("STATE_",))
        endpoints = getattr(status, "connection_endpoints", None)
        endpoint_parts: list[str] = []
        if _as_text(getattr(endpoints, "private_read_write", None)):
            endpoint_parts.append("private rw ready")
        if _as_text(getattr(endpoints, "public_read_write", None)):
            endpoint_parts.append("public rw ready")
        if not endpoint_parts:
            endpoint_parts.append("endpoints pending")

        operation_summary = (
            _latest_operation_summary(self._cluster_client.operation_service(), cluster_id)
            if cluster_id
            else None
        )
        cluster_label = self._target.resource_name
        if cluster_id:
            cluster_label = f"{cluster_label} ({cluster_id})"
        parts = [
            f"managed-postgresql {cluster_label}: {phase}",
            f"state {state}",
            ", ".join(endpoint_parts),
        ]
        if operation_summary:
            parts.append(operation_summary)
        return "; ".join(parts) + "."

    def close(self) -> None:
        self._sdk.sync_close()


class _FilesystemStatusPoller:
    def __init__(self, target: StatusWatcherTarget) -> None:
        self._target = target
        self._sdk = init_nebius_sdk(
            parent_id=target.parent_id,
            context="deployment status polling",
        )

        from nebius.api.nebius.compute.v1 import FilesystemServiceClient, ListFilesystemsRequest

        self._filesystem_client = FilesystemServiceClient(self._sdk)
        self._list_request = ListFilesystemsRequest

    def _find_filesystem(self):
        response = self._filesystem_client.list(
            self._list_request(parent_id=self._target.parent_id)
        ).wait()
        items = _response_collection(response, "items", "filesystems")
        for item in items:
            if _resource_metadata_name(item) == self._target.resource_name:
                return item
        return None

    def summary(self) -> str:
        filesystem = self._find_filesystem()
        if filesystem is None:
            return (
                f"sfs '{self._target.resource_name}' is not visible yet in "
                f"project {self._target.parent_id}."
            )

        status = getattr(filesystem, "status", None)
        filesystem_id = _resource_metadata_id(filesystem)
        state = _enum_field_name(status, "state")
        state_description = _as_text(getattr(status, "state_description", None))
        read_write_attachments = tuple(getattr(status, "read_write_attachments", []) or [])
        read_only_attachments = tuple(getattr(status, "read_only_attachments", []) or [])
        attachment_summary = (
            f"attachments rw:{len(read_write_attachments)} ro:{len(read_only_attachments)}"
        )
        if bool(getattr(status, "reconciling", False)):
            attachment_summary = f"{attachment_summary}; reconciling"

        operation_summary = (
            _latest_operation_summary(self._filesystem_client.operation_service(), filesystem_id)
            if filesystem_id
            else None
        )
        filesystem_label = self._target.resource_name
        if filesystem_id:
            filesystem_label = f"{filesystem_label} ({filesystem_id})"
        parts = [
            f"sfs {filesystem_label}: {state}",
            attachment_summary,
        ]
        if state_description:
            parts.append(state_description)
        if operation_summary:
            parts.append(operation_summary)
        return "; ".join(parts) + "."

    def close(self) -> None:
        self._sdk.sync_close()


class _ObjectStorageBucketStatusPoller:
    def __init__(self, target: StatusWatcherTarget) -> None:
        self._target = target
        self._sdk = init_nebius_sdk(
            parent_id=target.parent_id,
            context="deployment status polling",
        )

        from nebius.api.nebius.storage.v1 import BucketServiceClient, GetBucketByNameRequest

        self._bucket_client = BucketServiceClient(self._sdk)
        self._get_by_name_request = GetBucketByNameRequest

    def _find_bucket(self):
        with suppress(Exception):
            return self._bucket_client.get_by_name(
                self._get_by_name_request(
                    parent_id=self._target.parent_id,
                    name=self._target.resource_name,
                )
            ).wait()
        return None

    def summary(self) -> str:
        bucket = self._find_bucket()
        if bucket is None:
            return (
                f"object-storage '{self._target.resource_name}' is not visible yet in "
                f"project {self._target.parent_id}."
            )

        status = getattr(bucket, "status", None)
        bucket_id = _resource_metadata_id(bucket)
        state = _enum_field_name(status, "state", prefixes=("STATE_",))
        operation_summary = (
            _latest_operation_summary(self._bucket_client.operation_service(), bucket_id)
            if bucket_id
            else None
        )
        bucket_label = self._target.resource_name
        if bucket_id:
            bucket_label = f"{bucket_label} ({bucket_id})"
        parts = [f"object-storage {bucket_label}: {state}"]
        if operation_summary:
            parts.append(operation_summary)
        return "; ".join(parts) + "."

    def close(self) -> None:
        self._sdk.sync_close()


class _CompositeStatusPoller:
    def __init__(self, pollers: tuple[tuple[str, Any], ...]) -> None:
        self._pollers = pollers

    def summary(self) -> str:
        summaries: list[str] = []
        for label, poller in self._pollers:
            try:
                summaries.append(poller.summary())
            except Exception as exc:
                summaries.append(f"{label}: unavailable ({_shorten(str(exc), limit=96)})")
        return " | ".join(summary for summary in summaries if summary) or "API unavailable; heartbeat only"

    def close(self) -> None:
        for _label, poller in self._pollers:
            with suppress(Exception):
                poller.close()


def _mk8s_status_poller_from_target(target: StatusWatcherTarget) -> _Mk8sStatusPoller:
    return _Mk8sStatusPoller(
        Mk8sDeploymentTarget(project_id=target.parent_id, cluster_name=target.resource_name)
    )


_STATUS_POLLER_FACTORIES: dict[str, Callable[[StatusWatcherTarget], Any]] = {
    "nebius.mk8s.cluster": _mk8s_status_poller_from_target,
    "nebius.msp.postgresql.cluster": _PostgreSQLStatusPoller,
    "nebius.compute.filesystem": _FilesystemStatusPoller,
    "nebius.storage.bucket": _ObjectStorageBucketStatusPoller,
}


@dataclass
class TerraformApplyProgress:
    planned_add: int = 0
    planned_change: int = 0
    planned_destroy: int = 0
    total_planned: int | None = None
    completed: int = 0
    active: dict[str, str] = field(default_factory=dict)
    last_transition: str = "apply starting"
    final_summary: str | None = None

    def _record_planned_action(self, action: str) -> None:
        normalized = action.strip().lower()
        if normalized == "create":
            self.planned_add += 1
        elif normalized == "update":
            self.planned_change += 1
        elif normalized in {"delete", "destroy"}:
            self.planned_destroy += 1
        elif normalized == "replace":
            self.planned_destroy += 1
            self.planned_add += 1

    def update_from_event(self, event: Mapping[str, Any]) -> bool:
        event_type = _as_text(event.get("type"))
        if event_type == "planned_change":
            change = event.get("change")
            if isinstance(change, Mapping):
                self._record_planned_action(_as_text(change.get("action")))
            return False

        if event_type == "change_summary":
            changes = event.get("changes")
            if not isinstance(changes, Mapping):
                return False
            operation = _as_text(changes.get("operation"))
            add = int(changes.get("add", 0) or 0)
            change_count = int(changes.get("change", 0) or 0)
            destroy = int(changes.get("remove", 0) or 0)
            total = add + change_count + destroy
            if operation == "plan":
                self.planned_add = add
                self.planned_change = change_count
                self.planned_destroy = destroy
                self.total_planned = total
                self.last_transition = f"plan {add} add, {change_count} change, {destroy} destroy"
                return True
            if operation == "apply":
                self.final_summary = (
                    f"apply complete {add} add, {change_count} change, {destroy} destroy"
                )
                self.last_transition = self.final_summary
                if self.total_planned is None:
                    self.total_planned = total
                return True
            return False

        if event_type == "apply_start":
            hook = event.get("hook")
            if not isinstance(hook, Mapping):
                return False
            resource = hook.get("resource")
            if not isinstance(resource, Mapping):
                return False
            addr = _as_text(resource.get("addr"))
            action = _as_text(hook.get("action")) or "apply"
            if not addr:
                return False
            self.active[addr] = action
            self.last_transition = f"started {_shorten(addr)} ({action})"
            return True

        if event_type == "apply_complete":
            hook = event.get("hook")
            if not isinstance(hook, Mapping):
                return False
            resource = hook.get("resource")
            if not isinstance(resource, Mapping):
                return False
            addr = _as_text(resource.get("addr"))
            action = _as_text(hook.get("action")) or "apply"
            elapsed_seconds = hook.get("elapsed_seconds")
            if addr:
                self.active.pop(addr, None)
            self.completed += 1
            elapsed_label = (
                f" after {_format_elapsed(float(elapsed_seconds))}"
                if isinstance(elapsed_seconds, (int, float))
                else ""
            )
            self.last_transition = f"completed {_shorten(addr)} ({action}){elapsed_label}"
            return True

        if event_type == "diagnostic":
            diagnostic = event.get("diagnostic")
            if not isinstance(diagnostic, Mapping):
                return False
            severity = _as_text(diagnostic.get("severity")).lower()
            summary = _as_text(diagnostic.get("summary")) or "Terraform diagnostic"
            if severity not in {"warning", "error"}:
                return False
            self.last_transition = f"{severity}: {_shorten(summary)}"
            return True

        return False

    def summary(self) -> str:
        if self.final_summary:
            return self.final_summary

        parts: list[str] = []
        if self.total_planned is not None:
            parts.append(f"{self.completed}/{self.total_planned} complete")
        elif self.completed:
            parts.append(f"{self.completed} complete")
        else:
            parts.append("starting")

        if self.active:
            active_items = list(self.active.items())[:2]
            active_summary = ", ".join(
                f"{_shorten(addr)} ({action})" for addr, action in active_items
            )
            if len(self.active) > 2:
                active_summary += f", +{len(self.active) - 2} more"
            parts.append(f"active {active_summary}")

        if self.last_transition:
            parts.append(self.last_transition)
        return "; ".join(parts)


class DeploymentStatusReporter:
    def __init__(
        self,
        config: Any,
        *,
        emit: Callable[[str], None],
        status_watchers: list[Mapping[str, Any]] | None = None,
        poll_interval_seconds: float = 15.0,
        repeat_interval_seconds: float = 60.0,
    ) -> None:
        self._emit = emit
        self._poll_interval_seconds = poll_interval_seconds
        self._repeat_interval_seconds = repeat_interval_seconds
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._started_at = time.monotonic()
        self._last_message = ""
        self._last_emit_at = 0.0
        self._terraform = TerraformApplyProgress()
        self._thread: threading.Thread | None = None
        self._startup_notices: list[str] = []

        explicit_watchers = tuple(status_watchers or ())
        if explicit_watchers:
            initialized_pollers: list[tuple[str, Any]] = []
            labels: list[str] = []
            for raw in explicit_watchers:
                if not isinstance(raw, Mapping):
                    continue
                target = StatusWatcherTarget(
                    component_id=_as_text(raw.get("component_id")).lower(),
                    instance_id=(
                        _as_text(raw.get("instance_id")).lower()
                        or _as_text(raw.get("component_id")).lower()
                    ),
                    kind=_as_text(raw.get("kind")).lower(),
                    parent_id=_as_text(raw.get("parent_id")),
                    resource_name=_as_text(raw.get("resource_name")),
                )
                if not all(
                    (
                        target.component_id,
                        target.instance_id,
                        target.kind,
                        target.parent_id,
                        target.resource_name,
                    )
                ):
                    continue
                label = (
                    f"{component_instance_label(target.component_id, target.instance_id)} "
                    f"'{target.resource_name}'"
                )
                factory = _STATUS_POLLER_FACTORIES.get(target.kind)
                if factory is None:
                    self._startup_notices.append(
                        f"Nebius API status watcher kind '{target.kind}' is not supported for {label}; skipping."
                    )
                    continue
                try:
                    initialized_pollers.append((label, factory(target)))
                    labels.append(label)
                except Exception as exc:
                    self._startup_notices.append(
                        f"Nebius API status watcher unavailable for {label}; skipping. Reason: {exc}"
                    )
            if initialized_pollers:
                self._poller: _HeartbeatPoller | _Mk8sStatusPoller | _CompositeStatusPoller = (
                    _CompositeStatusPoller(tuple(initialized_pollers))
                )
                self._startup_notices.insert(
                    0,
                    "Watching Terraform + Nebius status for " + ", ".join(labels) + ".",
                )
            else:
                self._poller = _HeartbeatPoller()
                self._startup_notices.append(
                    "Nebius API status is unavailable; falling back to Terraform + elapsed heartbeat."
                )
        else:
            target = _mk8s_deployment_target(config)
            if target is None:
                self._poller = _HeartbeatPoller()
            else:
                try:
                    self._poller = _Mk8sStatusPoller(target)
                    self._startup_notices.append(
                        f"Watching Terraform + Nebius status for mk8s cluster '{target.cluster_name}' "
                        f"in project {target.project_id}."
                    )
                except Exception as exc:
                    self._poller = _HeartbeatPoller()
                    self._startup_notices.append(
                        "Nebius API status is unavailable; falling back to Terraform + elapsed heartbeat. "
                        f"Reason: {exc}"
                    )
        try:
            self._api_summary = self._poller.summary()
        except Exception as exc:
            self._poller.close()
            self._poller = _HeartbeatPoller()
            self._api_summary = self._poller.summary()
            self._startup_notices.append(
                "Nebius API initial status lookup failed; falling back to Terraform + elapsed heartbeat. "
                f"Reason: {exc}"
            )

    def _build_status_message(self, *, force: bool = False) -> str | None:
        with self._lock:
            elapsed = _format_elapsed(time.monotonic() - self._started_at)
            plain_message = (
                f"Status [{elapsed}] TF: {self._terraform.summary()} | API: {self._api_summary}"
            )
            message = (
                f"[bold white]Status[/bold white] [dim][{escape(elapsed)}][/dim]\n"
                f"[bold yellow]TF [/bold yellow] {escape(self._terraform.summary())}\n"
                f"[bold cyan]API[/bold cyan] {escape(self._api_summary)}"
            )
            now = time.monotonic()
            if (
                not force
                and plain_message == self._last_message
                and (now - self._last_emit_at) < self._repeat_interval_seconds
            ):
                return None
            self._last_message = plain_message
            self._last_emit_at = now
            return message

    def _emit_status(self, *, force: bool = False) -> None:
        message = self._build_status_message(force=force)
        if message:
            self._emit(message)

    def snapshot(self) -> str:
        with self._lock:
            elapsed = _format_elapsed(time.monotonic() - self._started_at)
            return f"Status [{elapsed}] TF: {self._terraform.summary()} | API: {self._api_summary}"

    def _run(self) -> None:
        while not self._stop_event.wait(self._poll_interval_seconds):
            try:
                api_summary = self._poller.summary()
            except Exception as exc:
                self._emit(
                    "Nebius API status reporter failed; continuing with Terraform + elapsed heartbeat: "
                    f"{exc}"
                )
                self._poller.close()
                self._poller = _HeartbeatPoller()
                api_summary = self._poller.summary()
            with self._lock:
                self._api_summary = api_summary
            self._emit_status()

    def start(self) -> DeploymentStatusReporter:
        for notice in self._startup_notices:
            self._emit(notice)
        self._emit_status(force=True)
        self._thread = threading.Thread(
            target=self._run,
            name="nebius-cxcli-deploy-status",
            daemon=True,
        )
        self._thread.start()
        return self

    def handle_terraform_event(self, event: Mapping[str, Any]) -> None:
        if self._terraform.update_from_event(event):
            self._emit_status(force=True)

    def close(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self._poll_interval_seconds + 1.0))
        self._poller.close()


@contextmanager
def deployment_status_reporting(
    config: Any,
    *,
    emit: Callable[[str], None],
    status_watchers: list[Mapping[str, Any]] | None = None,
    poll_interval_seconds: float = 15.0,
    repeat_interval_seconds: float = 60.0,
):
    reporter = DeploymentStatusReporter(
        config,
        emit=emit,
        status_watchers=status_watchers,
        poll_interval_seconds=poll_interval_seconds,
        repeat_interval_seconds=repeat_interval_seconds,
    ).start()
    try:
        yield reporter
    finally:
        reporter.close()
