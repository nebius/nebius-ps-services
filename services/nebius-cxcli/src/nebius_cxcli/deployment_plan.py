"""Pure desired-state planning for the shared deployment pipeline.

Plans describe semantic stages. Terraform binaries are per-attempt artifacts and
are never the recovery identity. Every stage is admitted before any retirement.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .deployment_state import digest
from .soperator_release import SoperatorVersion


class DeploymentAction(StrEnum):
    INSTALL = "install"
    RECONCILE = "reconcile"
    UPGRADE = "upgrade"
    NOOP = "noop"


class DeploymentStageKind(StrEnum):
    RETIRE = "retire"
    TRANSITION = "transition"
    GROW = "grow"
    RECONCILE = "reconcile"
    APPLICATIONS = "applications"


def _rows(config: Mapping[str, Any], scope: str) -> list[Mapping[str, Any]]:
    rows = config.get(scope, {}).get("charts" if scope == "apps" else "components", [])
    return [row for row in rows if isinstance(row, Mapping) and row.get("enabled", False)]


def soperator_target(config: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]] | None:
    rows = [row for row in _rows(config, "apps") if row.get("id") == "soperator"]
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError("Deploy supports exactly one Soperator target per project")
    row = rows[0]
    target = str(row.get("target_ref") or row.get("instance_id") or "").strip()
    if not target:
        raise ValueError("Soperator configuration has no target identity")
    return target, row


def _cluster_row(config: Mapping[str, Any], target: str) -> Mapping[str, Any]:
    rows = [
        row
        for row in _rows(config, "infra")
        if row.get("id") == "mk8s" and row.get("instance_id") == target
    ]
    if len(rows) > 1:
        raise ValueError("Soperator target has ambiguous infrastructure ownership")
    return rows[0] if rows else {}


def node_groups(config: Mapping[str, Any], target: str) -> dict[str, Any]:
    groups = _cluster_row(config, target).get("inputs", {}).get("node_groups", {})
    if not isinstance(groups, Mapping):
        raise ValueError("Soperator node_groups must be a mapping")
    return copy.deepcopy(
        {
            key: value
            for key, value in groups.items()
            if isinstance(value, Mapping) and value.get("enabled") is not False
        }
    )


def _count(group: Mapping[str, Any]) -> int:
    scaling = group.get("autoscaling", {})
    if isinstance(scaling, Mapping) and scaling and scaling.get("enabled") is not False:
        fixed = scaling.get("max_node_count")
    else:
        fixed = group.get("fixed_node_count")
    if fixed is None:
        fixed = group.get("node_count")
    if fixed is None:
        fixed = group.get("size", {}).get("fixed_node_count")
    if fixed is None:
        autoscaling = group.get("autoscaling", {})
        fixed = autoscaling.get("max_node_count") if isinstance(autoscaling, Mapping) else None
    if fixed is None or isinstance(fixed, bool) or not isinstance(fixed, int) or fixed < 0:
        raise ValueError("Node group has no admitted nonnegative capacity")
    return fixed


def changed_paths(before: object, after: object, prefix: str = "") -> tuple[str, ...]:
    if before == after:
        return ()
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        result: list[str] = []
        for key in sorted(set(before) | set(after)):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in before or key not in after:
                result.append(path)
            else:
                result.extend(changed_paths(before[key], after[key], path))
        return tuple(result)
    return (prefix or "$",)


def owned_config(config: Mapping[str, Any], target: str) -> dict[str, Any]:
    """All Soperator-owned inputs, including platform, integrations and storage."""
    app = soperator_target(config)
    cluster = _cluster_row(config, target)
    # Include storage and target-bound integration settings. Ordinary app rows
    # have independent field ownership and are accounted for by the full plan.
    return {
        "identity": config.get("client_info", {}).get("nebius", {}),
        "cluster": cluster,
        "storage": [row for row in _rows(config, "infra") if row.get("id") == "sfs"],
        "soperator": app[1] if app else None,
        "target": [
            row
            for row in config.get("deploy", {}).get("targets", [])
            if row.get("instance_id") == target
        ],
    }


def _replace_groups(
    config: Mapping[str, Any], target: str, groups: Mapping[str, Any]
) -> dict[str, Any]:
    result = copy.deepcopy(dict(config))
    row = _cluster_row(result, target)
    if not isinstance(row, dict):
        raise ValueError("Managed node-group changes require owned infrastructure")
    row.setdefault("inputs", {})["node_groups"] = copy.deepcopy(dict(groups))
    return result


@dataclass(frozen=True)
class DeploymentStage:
    name: DeploymentStageKind
    config: Mapping[str, Any]
    retired_groups: tuple[str, ...] = ()
    added_groups: tuple[str, ...] = ()

    def as_payload(self) -> dict[str, Any]:
        return {
            "name": self.name.value,
            "config": dict(self.config),
            "retiredGroups": list(self.retired_groups),
            "addedGroups": list(self.added_groups),
        }


@dataclass(frozen=True)
class DeploymentPlan:
    action: DeploymentAction
    target_ref: str
    source_release: str
    target_release: str
    changed_fields: tuple[str, ...]
    stages: tuple[DeploymentStage, ...]
    selected_targets: tuple[str, ...] = ()

    def as_payload(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "targetRef": self.target_ref,
            "sourceRelease": self.source_release,
            "targetRelease": self.target_release,
            "changedFields": list(self.changed_fields),
            "stages": [stage.as_payload() for stage in self.stages],
            "selectedTargets": list(self.selected_targets),
        }

    @property
    def identity(self) -> str:
        return digest(self.as_payload())


def plan_deployment(
    desired: Mapping[str, Any],
    *,
    accepted: Mapping[str, Any] | None,
    live_release: str | None,
    infrastructure_absent: bool,
) -> DeploymentPlan:
    """Classify a completely observed target; read failures must propagate upstream."""
    selected = soperator_target(desired)
    if selected is None:
        return DeploymentPlan(
            DeploymentAction.RECONCILE,
            "",
            "",
            "",
            (),
            (DeploymentStage(DeploymentStageKind.RECONCILE, desired),),
        )
    target, app = selected
    release = str(app.get("version") or "")
    SoperatorVersion.parse(release)
    if accepted is None:
        if live_release:
            raise ValueError(
                "Existing Soperator requires complete current release and topology observations"
            )
        return DeploymentPlan(
            DeploymentAction.INSTALL,
            target,
            "",
            release,
            ("$",),
            (DeploymentStage(DeploymentStageKind.RECONCILE, desired),),
        )
    old_target = soperator_target(accepted)
    if old_target is None or old_target[0] != target:
        raise ValueError("Changing Soperator target ownership is not supported by deploy")
    if not live_release:
        raise ValueError("Registered Soperator live release is absent or not observable")
    old_release = str(old_target[1].get("version") or "")
    if SoperatorVersion.parse(release) < SoperatorVersion.parse(live_release):
        raise ValueError("Soperator downgrades are not supported")
    for field in ("project_id", "tenant_id", "region_id"):
        if desired.get("client_info", {}).get("nebius", {}).get(field) != accepted.get(
            "client_info", {}
        ).get("nebius", {}).get(field):
            raise ValueError(f"Changing Soperator {field} ownership is not supported")
    old_cluster, new_cluster = _cluster_row(accepted, target), _cluster_row(desired, target)
    if bool(old_cluster) != bool(new_cluster):
        raise ValueError("Changing managed/onboarded ownership is not supported")
    if old_cluster.get("inputs", {}).get("cluster", {}).get("cluster_name") != new_cluster.get(
        "inputs", {}
    ).get("cluster", {}).get("cluster_name"):
        raise ValueError("Replacing the registered cluster is not supported")
    changes = changed_paths(owned_config(accepted, target), owned_config(desired, target))
    old_groups, new_groups = node_groups(accepted, target), node_groups(desired, target)
    replacement_keys = {
        key
        for key in old_groups.keys() & new_groups.keys()
        if any(
            old_groups[key].get(field) != new_groups[key].get(field)
            for field in (
                "name",
                "node_group_name",
                "platform",
                "platform_id",
                "preset",
                "resources",
                "gpu",
                "gpu_cluster_id",
                "gpu_cluster",
                "gpu_cluster_key",
                "reservation",
                "reservation_id",
                "allocation_policy",
                "subnet_id",
                "boot_disk",
                "boot_disk_type",
                "boot_disk_size_gib",
            )
        )
    }
    retired = tuple(
        sorted(
            key
            for key in old_groups
            if key not in new_groups
            or key in replacement_keys
            or _count(new_groups[key]) < _count(old_groups[key])
        )
    )
    added = tuple(
        sorted(
            key
            for key in new_groups
            if key not in old_groups
            or key in replacement_keys
            or _count(new_groups[key]) > _count(old_groups[key])
        )
    )
    platform_keys = (
        "version",
        "kubernetes_version",
        "k8s_version",
        "os",
        "os_image",
        "gpu_stack_preset",
        "gpu_drivers_preset",
    )
    platform_changed = any(
        any(before.get(key) != after.get(key) for key in platform_keys)
        for before, after in [
            (
                old_cluster.get("inputs", {}).get("cluster", {}),
                new_cluster.get("inputs", {}).get("cluster", {}),
            ),
            *[(old_groups[k], new_groups[k]) for k in old_groups.keys() & new_groups.keys()],
        ]
    )
    if not new_cluster:

        def desired_platform(config: Mapping[str, Any]) -> Any:
            return next(
                (
                    row.get("soperator_desired_platform")
                    for row in config.get("deploy", {}).get("targets", [])
                    if row.get("instance_id") == target
                ),
                None,
            )

        platform_changed = platform_changed or desired_platform(accepted) != desired_platform(
            desired
        )
    upgrading = live_release != release or old_release != release or platform_changed
    action = (
        DeploymentAction.UPGRADE
        if upgrading
        else DeploymentAction.RECONCILE
        if changes
        else DeploymentAction.NOOP
    )
    stages: list[DeploymentStage] = []
    remaining = copy.deepcopy(old_groups)
    for key in retired:
        if key not in new_groups or key in replacement_keys:
            del remaining[key]
        else:
            # Keep the old platform during retirement. Capacity fields change only.
            for capacity in ("fixed_node_count", "node_count", "size", "autoscaling"):
                if capacity in new_groups[key]:
                    remaining[key][capacity] = copy.deepcopy(new_groups[key][capacity])
                else:
                    remaining[key].pop(capacity, None)
    if retired:
        stages.append(
            DeploymentStage(
                DeploymentStageKind.RETIRE,
                _replace_groups(accepted, target, remaining),
                retired_groups=retired,
            )
        )
    intermediate = copy.deepcopy(new_groups)
    for key in added:
        if key not in old_groups or key in replacement_keys:
            del intermediate[key]
        else:
            for capacity in ("fixed_node_count", "node_count", "size", "autoscaling"):
                if capacity in remaining[key]:
                    intermediate[key][capacity] = copy.deepcopy(remaining[key][capacity])
                else:
                    intermediate[key].pop(capacity, None)
    if upgrading or any(field.startswith(("cluster.", "storage")) for field in changes):
        stages.append(
            DeploymentStage(
                DeploymentStageKind.TRANSITION,
                _replace_groups(desired, target, intermediate) if new_cluster else desired,
            )
        )
    if added:
        stages.append(DeploymentStage(DeploymentStageKind.GROW, desired, added_groups=added))
    stages.append(DeploymentStage(DeploymentStageKind.RECONCILE, desired))
    return DeploymentPlan(action, target, live_release, release, changes, tuple(stages))


def terraform_changes(plan: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    changes = plan.get("resource_changes", [])
    if not isinstance(changes, list):
        raise ValueError("Terraform plan has invalid resource_changes")
    result = []
    for resource in changes:
        if resource.get("mode") == "data":
            continue
        actions = resource.get("change", {}).get("actions")
        if not isinstance(actions, list):
            raise ValueError("Terraform resource change has no actions")
        if actions != ["no-op"]:
            result.append(resource)
    return tuple(result)


def assert_protected_terraform_scope(plan: Mapping[str, Any], *, target_ref: str) -> None:
    """Storage and cluster identities can never be replaced by ordinary deploy."""
    for resource in terraform_changes(plan):
        if "delete" not in resource["change"]["actions"]:
            continue
        resource_type = str(resource.get("type") or "")
        if (
            "filesystem" in resource_type
            or "disk" in resource_type
            or resource_type == "nebius_mk8s_v1_cluster"
        ):
            raise ValueError(
                "Deploy refuses protected storage or cluster deletion/replacement: "
                + str(resource.get("address"))
            )


def _known_target_matches(expected: Any, current: Any, unknown: Any) -> bool:
    if unknown is True:
        return True
    if isinstance(expected, Mapping):
        if not isinstance(current, Mapping):
            return False
        mask = unknown if isinstance(unknown, Mapping) else {}
        return all(
            _known_target_matches(value, current.get(key), mask.get(key))
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        if not isinstance(current, list) or len(expected) != len(current):
            return False
        masks = unknown if isinstance(unknown, list) else [None] * len(expected)
        return all(
            _known_target_matches(
                value, current[index], masks[index] if index < len(masks) else None
            )
            for index, value in enumerate(expected)
        )
    return expected == current


def assert_stage_plan(admitted: Mapping[str, Any], refreshed: Mapping[str, Any]) -> None:
    """Refresh may finish a subset after interruption, never expand frozen intent."""
    allowed = {row["address"]: row for row in terraform_changes(admitted)}
    for resource in terraform_changes(refreshed):
        prior = allowed.get(resource.get("address"))
        if prior is None or resource.get("type") != prior.get("type"):
            raise ValueError("Refreshed Terraform plan adds an unadmitted resource change")
        before, after = prior["change"], resource["change"]
        admitted_actions, actions = before["actions"], after["actions"]
        if actions != admitted_actions and not (
            "create" in admitted_actions and actions in (["update"], ["create"])
        ):
            raise ValueError("Refreshed Terraform plan changes the admitted action")
        if not _known_target_matches(
            before.get("after"), after.get("after"), before.get("after_unknown")
        ):
            raise ValueError("Refreshed Terraform plan changes the admitted desired state")
        # Unknowns may become known, but a new unknown must not hide a previously
        # admitted concrete value. Terraform must expose it before mutation.
        if _new_unknowns(after.get("after_unknown"), before.get("after_unknown")):
            raise ValueError("Refreshed Terraform plan introduces unadmitted unknown values")


def _new_unknowns(current: Any, admitted: Any) -> bool:
    if admitted is True:
        return False
    if current is True:
        return True
    if isinstance(current, Mapping):
        previous = admitted if isinstance(admitted, Mapping) else {}
        return any(_new_unknowns(value, previous.get(key)) for key, value in current.items())
    if isinstance(current, list):
        previous_list = admitted if isinstance(admitted, list) else []
        return any(
            _new_unknowns(value, previous_list[index] if index < len(previous_list) else None)
            for index, value in enumerate(current)
        )
    return False


def terraform_admission(plan: Mapping[str, Any], *, include_noop: bool = False) -> dict[str, Any]:
    """Persist semantic authority without copying Terraform's sensitive plaintext."""
    resources = []
    rows = plan.get("resource_changes", []) if include_noop else terraform_changes(plan)
    for row in rows:
        if row.get("mode") == "data":
            continue
        change = row["change"]
        resources.append(
            {
                "address": row["address"],
                "type": row["type"],
                "mode": row.get("mode", "managed"),
                "change": {
                    "actions": list(change["actions"]),
                    "after": _sensitive_identity(
                        change.get("after"), change.get("after_sensitive")
                    ),
                    "after_unknown": copy.deepcopy(change.get("after_unknown", {})),
                },
            }
        )
        if include_noop:
            from .deployment_dependencies import resource_identity

            resources[-1]["beforeIdentity"] = row.get("beforeIdentity") or resource_identity(
                change.get("before")
            )
    return {"resource_changes": resources}


def _sensitive_identity(value: Any, mask: Any) -> Any:
    if mask is True:
        return {"$sensitiveDigest": digest(value)}
    if isinstance(value, Mapping):
        masks = mask if isinstance(mask, Mapping) else {}
        return {key: _sensitive_identity(item, masks.get(key)) for key, item in value.items()}
    if isinstance(value, list):
        masks_list = mask if isinstance(mask, list) else []
        return [
            _sensitive_identity(item, masks_list[index] if index < len(masks_list) else None)
            for index, item in enumerate(value)
        ]
    return value


def plan_with_observed_drift(
    plan: DeploymentPlan, terraform: Mapping[str, Any], *, owned_modules: set[str]
) -> DeploymentPlan:
    """Put observed owned-infrastructure drift inside maintenance before applying it."""
    from dataclasses import replace

    if not plan.target_ref or plan.action is DeploymentAction.INSTALL:
        return plan
    drift = [
        str(row.get("address", ""))
        for row in terraform_changes(terraform)
        if any(
            str(row.get("address", "")).startswith(f"module.{module}.") for module in owned_modules
        )
    ]
    if not drift or any(stage.name is not DeploymentStageKind.RECONCILE for stage in plan.stages):
        return plan
    desired = plan.stages[-1].config
    return replace(
        plan,
        action=DeploymentAction.RECONCILE,
        changed_fields=tuple(
            sorted(set(plan.changed_fields) | {f"live:{address}" for address in drift})
        ),
        stages=(DeploymentStage(DeploymentStageKind.TRANSITION, desired), *plan.stages),
    )
