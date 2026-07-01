"""Soperator worker NodeSet scale planning and live mutation helpers."""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .component_instances import normalize_component_token

SOPERATOR_SCALE_UP = "scale-up"
SOPERATOR_SCALE_DOWN = "scale-down"
SOPERATOR_SCALE_DIRECTIONS = frozenset({SOPERATOR_SCALE_UP, SOPERATOR_SCALE_DOWN})
SOPERATOR_SCALE_OWNERSHIP_MANAGED = "managed"
SOPERATOR_SCALE_OWNERSHIP_EXTERNAL = "external"

_NODESET_LABEL_KEYS = (
    "slurm.nebius.ai/nodeset-name",
    "slurm.nebius.ai/nodeset",
)
_NODE_GROUP_ID_LABEL_KEYS = ("nebius.com/node-group-id", "yandex.cloud/node-group-id")
_NODE_GROUP_NAME_LABEL_KEYS = ("nebius.com/node-group",)


class SoperatorScaleCommandResult(Protocol):
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class SoperatorScaleCommandRunner(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 120,
        check: bool = True,
    ) -> SoperatorScaleCommandResult: ...


@dataclass(frozen=True)
class SoperatorWorkerScaleRequest:
    ownership: str
    direction: str
    target_ref: str
    namespace: str
    nodeset: str
    to_workers: int
    kube_context: str | None = None
    cluster_id: str | None = None
    project_id: str | None = None
    worker_ordinals: tuple[int, ...] = ()


@dataclass(frozen=True)
class SoperatorNodeGroupScale:
    node_group_id: str
    node_group_name: str
    mode: str
    current_count: int | None
    min_count: int | None
    max_count: int | None
    desired_min_count: int | None
    desired_max_count: int | None
    desired_fixed_count: int | None


@dataclass(frozen=True)
class SoperatorWorkerScalePlan:
    request: SoperatorWorkerScaleRequest
    current_replicas: int
    desired_replicas: int
    ephemeral: bool
    current_active_ordinals: tuple[int, ...]
    desired_active_ordinals: tuple[int, ...]
    affected_ordinals: tuple[int, ...]
    affected_pods: tuple[str, ...]
    node_group: SoperatorNodeGroupScale | None
    warnings: tuple[str, ...]

    @property
    def mutates_nodeset_replicas(self) -> bool:
        return not self.ephemeral and self.current_replicas != self.desired_replicas

    @property
    def mutates_power_state(self) -> bool:
        return self.ephemeral and self.current_active_ordinals != self.desired_active_ordinals


def _text(value: object | None) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _items(value: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, Mapping):
        raw_items = value.get("items", value.get("node_groups", value.get("nodeGroups", [])))
        if isinstance(raw_items, Sequence) and not isinstance(
            raw_items, (str, bytes, bytearray)
        ):
            return tuple(item for item in raw_items if isinstance(item, Mapping))
        if value:
            return (value,)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(item for item in value if isinstance(item, Mapping))
    return ()


def _bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return _text(value).lower() in {"1", "true", "yes", "on"}


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        if isinstance(value, float):
            parsed = int(value) if value.is_integer() else -1
        else:
            parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _required_non_negative(value: int, *, field: str) -> int:
    if isinstance(value, bool) or int(value) < 0:
        raise RuntimeError(f"{field} must be a non-negative integer.")
    return int(value)


def _run(
    command_runner: SoperatorScaleCommandRunner,
    args: Sequence[str],
    *,
    input_text: str | None = None,
    timeout_seconds: int = 120,
    check: bool = True,
) -> SoperatorScaleCommandResult:
    result = command_runner(
        tuple(str(part) for part in args),
        input_text=input_text,
        timeout_seconds=timeout_seconds,
        check=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            f"Command failed during Soperator worker scale: {shlex.join(tuple(result.args))}"
            + (f"\n{detail}" if detail else "")
        )
    return result


def _kubectl_args(
    *,
    namespace: str | None,
    kube_context: str | None,
    args: Sequence[str],
) -> tuple[str, ...]:
    command = ["kubectl"]
    context = _text(kube_context)
    if context:
        command.extend(["--context", context])
    ns = _text(namespace)
    if ns:
        command.extend(["-n", ns])
    command.extend(str(part) for part in args)
    return tuple(command)


def _kubectl_json(
    command_runner: SoperatorScaleCommandRunner,
    *,
    namespace: str | None,
    kube_context: str | None,
    args: Sequence[str],
    required: bool = True,
) -> Mapping[str, Any]:
    result = _run(
        command_runner,
        _kubectl_args(namespace=namespace, kube_context=kube_context, args=args),
        timeout_seconds=120,
        check=False,
    )
    if result.returncode != 0:
        if required:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(
                "Failed to read live Soperator worker scale state"
                + (f": {detail}" if detail else "")
            )
        return {}
    try:
        parsed = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"kubectl returned invalid JSON during Soperator worker scale: {exc}") from exc
    return parsed if isinstance(parsed, Mapping) else {}


def _kubectl_items(
    command_runner: SoperatorScaleCommandRunner,
    *,
    namespace: str | None,
    kube_context: str | None,
    args: Sequence[str],
    required: bool = True,
) -> tuple[Mapping[str, Any], ...]:
    return _items(
        _kubectl_json(
            command_runner,
            namespace=namespace,
            kube_context=kube_context,
            args=args,
            required=required,
        )
    )


def _metadata_name(item: Mapping[str, Any]) -> str:
    return _text(_mapping(item.get("metadata")).get("name"))


def _metadata_labels(item: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(_mapping(item.get("metadata")).get("labels"))


def _nodeset_name_from_pod(item: Mapping[str, Any]) -> str:
    labels = _metadata_labels(item)
    for key in _NODESET_LABEL_KEYS:
        value = _text(labels.get(key))
        if value:
            return value
    return ""


def _pod_ordinal(pod_name: str, nodeset: str) -> int | None:
    prefix = f"{nodeset}-"
    if not pod_name.startswith(prefix):
        return None
    match = re.search(r"-(\d+)$", pod_name)
    if not match:
        return None
    return int(match.group(1))


def _active_ordinals_from_power_state(power_state: Mapping[str, Any]) -> tuple[int, ...]:
    raw_active = _mapping(power_state.get("spec")).get("activeNodes", [])
    if not isinstance(raw_active, Sequence) or isinstance(raw_active, (str, bytes, bytearray)):
        return ()
    parsed = sorted({value for item in raw_active if (value := _int_or_none(item)) is not None})
    return tuple(parsed)


def _node_group_id(payload: Mapping[str, Any]) -> str:
    metadata = _mapping(payload.get("metadata"))
    return _text(metadata.get("id")) or _text(payload.get("id"))


def _node_group_name(payload: Mapping[str, Any]) -> str:
    metadata = _mapping(payload.get("metadata"))
    return _text(metadata.get("name")) or _text(payload.get("name"))


def _node_group_autoscaling(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    spec = _mapping(payload.get("spec"))
    return _mapping(spec.get("autoscaling"))


def _node_group_status(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(payload.get("status"))


def _node_group_count(payload: Mapping[str, Any]) -> int | None:
    spec = _mapping(payload.get("spec"))
    status = _node_group_status(payload)
    for value in (
        spec.get("fixed_node_count"),
        spec.get("fixedNodeCount"),
        status.get("target_node_count"),
        status.get("targetNodeCount"),
        status.get("node_count"),
        status.get("nodeCount"),
    ):
        parsed = _int_or_none(value)
        if parsed is not None:
            return parsed
    return None


def _node_group_scale_mode(payload: Mapping[str, Any]) -> str:
    autoscaling = _node_group_autoscaling(payload)
    if autoscaling:
        return "autoscaling"
    return "fixed"


def _node_group_min_max(payload: Mapping[str, Any]) -> tuple[int | None, int | None]:
    autoscaling = _node_group_autoscaling(payload)
    min_count = None
    max_count = None
    for key in ("min_node_count", "minNodeCount"):
        min_count = _int_or_none(autoscaling.get(key))
        if min_count is not None:
            break
    for key in ("max_node_count", "maxNodeCount"):
        max_count = _int_or_none(autoscaling.get(key))
        if max_count is not None:
            break
    return min_count, max_count


def _list_nebius_node_groups(
    command_runner: SoperatorScaleCommandRunner,
    *,
    cluster_id: str,
) -> tuple[Mapping[str, Any], ...]:
    if not _text(cluster_id):
        return ()
    result = _run(
        command_runner,
        [
            "nebius",
            "mk8s",
            "node-group",
            "list",
            "--parent-id",
            cluster_id,
            "--format",
            "json",
            "--all",
        ],
        timeout_seconds=180,
        check=False,
    )
    if result.returncode != 0:
        return ()
    try:
        parsed = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"nebius mk8s node-group list returned invalid JSON: {exc}") from exc
    return _items(parsed)


def _pod_node_names_by_ordinal(
    command_runner: SoperatorScaleCommandRunner,
    *,
    namespace: str,
    kube_context: str | None,
    nodeset: str,
) -> dict[int, str]:
    selectors = [f"{_NODESET_LABEL_KEYS[0]}={nodeset}", f"{_NODESET_LABEL_KEYS[1]}={nodeset}"]
    pods: tuple[Mapping[str, Any], ...] = ()
    for selector in selectors:
        pods = _kubectl_items(
            command_runner,
            namespace=namespace,
            kube_context=kube_context,
            args=["get", "pods", "-l", selector, "-o", "json", "--request-timeout=20s"],
            required=False,
        )
        if pods:
            break
    by_ordinal: dict[int, str] = {}
    for pod in pods:
        name = _metadata_name(pod)
        if _nodeset_name_from_pod(pod) not in {"", nodeset}:
            continue
        ordinal = _pod_ordinal(name, nodeset)
        node_name = _text(_mapping(pod.get("spec")).get("nodeName"))
        if ordinal is not None and node_name:
            by_ordinal[ordinal] = node_name
    return by_ordinal


def _node_group_tokens_for_nodes(
    command_runner: SoperatorScaleCommandRunner,
    *,
    kube_context: str | None,
    node_names: Sequence[str],
) -> tuple[set[str], set[str]]:
    selected = {name for name in node_names if name}
    if not selected:
        return set(), set()
    nodes = _kubectl_items(
        command_runner,
        namespace=None,
        kube_context=kube_context,
        args=["get", "nodes", "-o", "json", "--request-timeout=20s"],
        required=False,
    )
    ids: set[str] = set()
    names: set[str] = set()
    for node in nodes:
        if _metadata_name(node) not in selected:
            continue
        labels = _metadata_labels(node)
        for key in _NODE_GROUP_ID_LABEL_KEYS:
            value = _text(labels.get(key))
            if value:
                ids.add(value)
        for key in _NODE_GROUP_NAME_LABEL_KEYS:
            value = _text(labels.get(key))
            if value:
                names.add(value)
    return ids, names


def _select_node_group(
    command_runner: SoperatorScaleCommandRunner,
    *,
    request: SoperatorWorkerScaleRequest,
    pod_node_names: Mapping[int, str],
) -> tuple[SoperatorNodeGroupScale | None, tuple[str, ...]]:
    cluster_id = _text(request.cluster_id)
    warnings: list[str] = []
    groups = _list_nebius_node_groups(command_runner, cluster_id=cluster_id)
    if not groups:
        if cluster_id:
            warnings.append(
                "Could not read Nebius MK8s node groups; dry-run can continue, "
                "--execute will fail closed before mutation."
            )
        return None, tuple(warnings)
    node_group_ids, node_group_names = _node_group_tokens_for_nodes(
        command_runner,
        kube_context=request.kube_context,
        node_names=tuple(pod_node_names.values()),
    )
    candidates: list[Mapping[str, Any]] = []
    if node_group_ids:
        candidates = [group for group in groups if _node_group_id(group) in node_group_ids]
    if not candidates and node_group_names:
        candidates = [group for group in groups if _node_group_name(group) in node_group_names]
    if not candidates:
        normalized_nodeset = normalize_component_token(request.nodeset)
        candidates = [
            group
            for group in groups
            if normalize_component_token(_node_group_name(group)) == normalized_nodeset
        ]
    if len(candidates) != 1:
        warnings.append(
            "Could not uniquely map worker NodeSet to one Nebius MK8s node group; "
            "--execute will fail closed before mutation."
        )
        return None, tuple(warnings)
    group = candidates[0]
    mode = _node_group_scale_mode(group)
    min_count, max_count = _node_group_min_max(group)
    current_count = _node_group_count(group)
    desired_fixed: int | None = None
    desired_min: int | None = None
    desired_max: int | None = None
    if mode == "autoscaling":
        desired_max = max_count
        if request.to_workers == 0:
            desired_min = 0
        elif min_count is not None and min_count > request.to_workers:
            desired_min = request.to_workers
        elif request.direction == SOPERATOR_SCALE_UP and min_count is not None:
            desired_min = min_count
        if max_count is not None and request.to_workers > max_count:
            raise RuntimeError(
                f"Requested {request.to_workers} worker(s) but node group "
                f"{_node_group_name(group) or _node_group_id(group)} autoscaling max is {max_count}."
            )
    else:
        desired_fixed = request.to_workers
    return (
        SoperatorNodeGroupScale(
            node_group_id=_node_group_id(group),
            node_group_name=_node_group_name(group),
            mode=mode,
            current_count=current_count,
            min_count=min_count,
            max_count=max_count,
            desired_min_count=desired_min,
            desired_max_count=desired_max,
            desired_fixed_count=desired_fixed,
        ),
        tuple(warnings),
    )


def build_worker_scale_plan(
    command_runner: SoperatorScaleCommandRunner,
    request: SoperatorWorkerScaleRequest,
) -> SoperatorWorkerScalePlan:
    if request.direction not in SOPERATOR_SCALE_DIRECTIONS:
        raise RuntimeError(
            "Soperator worker scale direction must be one of: "
            + ", ".join(sorted(SOPERATOR_SCALE_DIRECTIONS))
        )
    _required_non_negative(request.to_workers, field="--to-workers")
    namespace = _text(request.namespace) or "soperator"
    nodeset_name = normalize_component_token(request.nodeset)
    if not nodeset_name:
        raise RuntimeError("--nodeset is required.")
    nodeset = _kubectl_json(
        command_runner,
        namespace=namespace,
        kube_context=request.kube_context,
        args=["get", "nodeset", nodeset_name, "-o", "json", "--request-timeout=20s"],
    )
    spec = _mapping(nodeset.get("spec"))
    current_replicas = _int_or_none(spec.get("replicas"))
    if current_replicas is None:
        raise RuntimeError(f"Live NodeSet {nodeset_name!r} does not expose spec.replicas.")
    ephemeral = _bool(spec.get("ephemeralNodes"), default=False)
    if request.direction == SOPERATOR_SCALE_DOWN and request.to_workers > current_replicas:
        raise RuntimeError(
            f"scale-down target {request.to_workers} is greater than current replicas {current_replicas}."
        )
    current_active_ordinals: tuple[int, ...] = tuple(range(current_replicas))
    desired_active_ordinals: tuple[int, ...] = tuple(range(request.to_workers))
    desired_replicas = request.to_workers
    if ephemeral:
        if request.to_workers > current_replicas:
            raise RuntimeError(
                f"Requested {request.to_workers} active worker(s), but NodeSet {nodeset_name} "
                f"maximum replicas is {current_replicas}."
            )
        power_state = _kubectl_json(
            command_runner,
            namespace=namespace,
            kube_context=request.kube_context,
            args=[
                "get",
                "nodesetpowerstate",
                nodeset_name,
                "-o",
                "json",
                "--request-timeout=20s",
            ],
            required=False,
        )
        current_active_ordinals = _active_ordinals_from_power_state(power_state)
        if not current_active_ordinals and request.to_workers > 0:
            initial = _int_or_none(spec.get("initialNumberEphemeralNodes")) or 0
            current_active_ordinals = tuple(range(min(initial, current_replicas)))
        desired_active_ordinals = tuple(range(request.to_workers))
        desired_replicas = current_replicas
    if request.worker_ordinals:
        selected = tuple(sorted(set(request.worker_ordinals)))
        out_of_range = [
            ordinal for ordinal in selected if ordinal < 0 or ordinal >= current_replicas
        ]
        if out_of_range:
            raise RuntimeError(
                "--worker-ordinal selections must be valid ordinals for the live NodeSet."
            )
        if request.direction == SOPERATOR_SCALE_DOWN:
            missing_active = [
                ordinal for ordinal in selected if ordinal not in current_active_ordinals
            ]
            if missing_active:
                raise RuntimeError("--worker-ordinal selections must be active workers.")
            desired_active_ordinals = tuple(
                ordinal for ordinal in current_active_ordinals if ordinal not in selected
            )
            if len(desired_active_ordinals) != request.to_workers:
                raise RuntimeError(
                    "--worker-ordinal selections must leave exactly --to-workers active workers."
                )
            affected_ordinals = selected
        else:
            desired_active_ordinals = tuple(sorted(set(current_active_ordinals).union(selected)))
            affected_ordinals = tuple(
                ordinal
                for ordinal in desired_active_ordinals
                if ordinal not in current_active_ordinals
            )
            if len(desired_active_ordinals) != request.to_workers:
                raise RuntimeError(
                    "--worker-ordinal selections must produce exactly --to-workers active workers."
                )
            if not ephemeral:
                raise RuntimeError("Non-ephemeral worker scale-up uses replicas; omit --worker-ordinal.")
    else:
        if request.direction == SOPERATOR_SCALE_DOWN:
            affected_ordinals = tuple(
                ordinal for ordinal in current_active_ordinals if ordinal not in desired_active_ordinals
            )
        else:
            affected_ordinals = tuple(
                ordinal for ordinal in desired_active_ordinals if ordinal not in current_active_ordinals
            )
    if not ephemeral and request.worker_ordinals and request.direction == SOPERATOR_SCALE_DOWN:
        tail = tuple(range(request.to_workers, current_replicas))
        if tuple(sorted(request.worker_ordinals)) != tail:
            raise RuntimeError(
                "Non-ephemeral worker scale currently supports tail ordinal removal only. "
                "Omit --worker-ordinal or select exactly the highest ordinals being removed."
            )
    pod_node_names = _pod_node_names_by_ordinal(
        command_runner,
        namespace=namespace,
        kube_context=request.kube_context,
        nodeset=nodeset_name,
    )
    node_group, warnings = _select_node_group(
        command_runner,
        request=SoperatorWorkerScaleRequest(
            ownership=request.ownership,
            direction=request.direction,
            target_ref=request.target_ref,
            namespace=namespace,
            nodeset=nodeset_name,
            to_workers=request.to_workers,
            kube_context=request.kube_context,
            cluster_id=request.cluster_id,
            project_id=request.project_id,
            worker_ordinals=request.worker_ordinals,
        ),
        pod_node_names=pod_node_names,
    )
    affected_pods = tuple(f"{nodeset_name}-{ordinal}" for ordinal in affected_ordinals)
    return SoperatorWorkerScalePlan(
        request=SoperatorWorkerScaleRequest(
            ownership=request.ownership,
            direction=request.direction,
            target_ref=request.target_ref,
            namespace=namespace,
            nodeset=nodeset_name,
            to_workers=request.to_workers,
            kube_context=request.kube_context,
            cluster_id=request.cluster_id,
            project_id=request.project_id,
            worker_ordinals=request.worker_ordinals,
        ),
        current_replicas=current_replicas,
        desired_replicas=desired_replicas,
        ephemeral=ephemeral,
        current_active_ordinals=current_active_ordinals,
        desired_active_ordinals=desired_active_ordinals,
        affected_ordinals=affected_ordinals,
        affected_pods=affected_pods,
        node_group=node_group,
        warnings=warnings,
    )


def worker_scale_plan_lines(plan: SoperatorWorkerScalePlan) -> tuple[str, ...]:
    request = plan.request
    lines = [
        f"Soperator worker scale plan: {request.direction} {request.nodeset} to {request.to_workers} worker(s).",
        f"Target: {request.target_ref or 'external'}; namespace: {request.namespace}.",
        f"NodeSet mode: {'ephemeral' if plan.ephemeral else 'non-ephemeral'}; current replicas: {plan.current_replicas}.",
    ]
    if plan.ephemeral:
        lines.append(
            "NodeSetPowerState active ordinals: "
            + f"{list(plan.current_active_ordinals)} -> {list(plan.desired_active_ordinals)}."
        )
    else:
        lines.append(f"NodeSet replicas: {plan.current_replicas} -> {plan.desired_replicas}.")
    if plan.affected_ordinals:
        lines.append("Affected worker ordinals: " + ", ".join(str(item) for item in plan.affected_ordinals) + ".")
    else:
        lines.append("No worker pod ordinal changes are required.")
    if plan.node_group is not None:
        group = plan.node_group
        label = group.node_group_name or group.node_group_id
        if group.mode == "autoscaling":
            lines.append(
                f"MK8s node group {label}: autoscaling min/max "
                f"{group.min_count}/{group.max_count} -> "
                f"{group.desired_min_count if group.desired_min_count is not None else group.min_count}/"
                f"{group.desired_max_count if group.desired_max_count is not None else group.max_count}."
            )
        else:
            lines.append(
                f"MK8s node group {label}: fixed count {group.current_count} -> {group.desired_fixed_count}."
            )
    for warning in plan.warnings:
        lines.append("WARNING: " + warning)
    if not plan.ephemeral and request.to_workers == 0:
        lines.append(
            "WARNING: scale-to-zero for non-ephemeral workers is maintenance mode; "
            "service roles remain, but this worker set cannot run jobs until scaled back up."
        )
    return tuple(lines)


def execute_worker_scale_plan(
    command_runner: SoperatorScaleCommandRunner,
    plan: SoperatorWorkerScalePlan,
    *,
    adjust_node_group: bool,
) -> tuple[str, ...]:
    request = plan.request
    actions: list[str] = []
    if adjust_node_group and _text(request.cluster_id) and plan.node_group is None:
        raise RuntimeError(
            "Could not uniquely map the worker NodeSet to one Nebius MK8s node group; "
            "refusing to mutate worker pods without a matching host-capacity update."
        )

    def _apply_kubernetes_scale() -> None:
        if plan.ephemeral and plan.mutates_power_state:
            payload = json.dumps({"spec": {"activeNodes": list(plan.desired_active_ordinals)}})
            _run(
                command_runner,
                _kubectl_args(
                    namespace=request.namespace,
                    kube_context=request.kube_context,
                    args=[
                        "patch",
                        "nodesetpowerstate",
                        request.nodeset,
                        "--type=merge",
                        "-p",
                        payload,
                    ],
                ),
                timeout_seconds=120,
            )
            actions.append(
                "Patched NodeSetPowerState "
                f"{request.nodeset} activeNodes to {list(plan.desired_active_ordinals)}."
            )
        if not plan.ephemeral and plan.mutates_nodeset_replicas:
            payload = json.dumps({"spec": {"replicas": plan.desired_replicas}})
            _run(
                command_runner,
                _kubectl_args(
                    namespace=request.namespace,
                    kube_context=request.kube_context,
                    args=[
                        "patch",
                        "nodeset",
                        request.nodeset,
                        "--type=merge",
                        "-p",
                        payload,
                    ],
                ),
                timeout_seconds=120,
            )
            actions.append(
                f"Patched NodeSet {request.nodeset} replicas to {plan.desired_replicas}."
            )

    def _apply_node_group_scale() -> None:
        if not adjust_node_group or plan.node_group is None:
            return
        group = plan.node_group
        if group.mode == "autoscaling" and group.desired_min_count is not None:
            args = [
                "nebius",
                "mk8s",
                "node-group",
                "update",
                group.node_group_id,
                "--autoscaling-min-node-count",
                str(group.desired_min_count),
            ]
            if group.desired_max_count is not None:
                args.extend(["--autoscaling-max-node-count", str(group.desired_max_count)])
            args.extend(["--format", "json", "--timeout", "45m"])
            _run(command_runner, args, timeout_seconds=3000)
            actions.append(
                f"Updated MK8s node group {group.node_group_name or group.node_group_id} autoscaling bounds."
            )
        elif group.mode == "fixed" and group.desired_fixed_count is not None:
            _run(
                command_runner,
                [
                    "nebius",
                    "mk8s",
                    "node-group",
                    "update",
                    group.node_group_id,
                    "--fixed-node-count",
                    str(group.desired_fixed_count),
                    "--format",
                    "json",
                    "--timeout",
                    "45m",
                ],
                timeout_seconds=3000,
            )
            actions.append(
                f"Updated MK8s node group {group.node_group_name or group.node_group_id} fixed count."
            )

    if request.direction == SOPERATOR_SCALE_UP:
        _apply_node_group_scale()
        _apply_kubernetes_scale()
    else:
        _apply_kubernetes_scale()
        _apply_node_group_scale()
    if not actions:
        actions.append("No live worker scale mutation was required.")
    return tuple(actions)
