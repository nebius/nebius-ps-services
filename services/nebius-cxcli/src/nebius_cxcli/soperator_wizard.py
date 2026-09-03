"""Dedicated install-time policy for the official upstream Soperator release."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources as importlib_resources
from typing import Any

import yaml

from .component_sources import (
    ComponentDefault,
    Mk8sGpuAppPolicy,
    _parse_component_defaults,
    _parse_mk8s_gpu_app_policy,
    _parse_wizard_fields,
)

SOPERATOR_WIZARD_SCHEMA = "nebius-cxcli.soperator-wizard.v1"
SOPERATOR_WIZARD_ID = "soperator-wizard"
SOPERATOR_WIZARD_FILENAME = "soperator_wizard.yaml"
SOPERATOR_PROFILE_NAMES = frozenset({"nebius-cpu-v1", "nebius-gpu-v1", "nebius-mixed-v1"})
_GO_DURATION_RE = re.compile(r"(?:\d+(?:\.\d+)?(?:ns|us|µs|ms|s|m|h))+")
_PLACEMENT_KINDS = {
    "platform-support",
    "slurm-service",
    "slurm-worker-nodeset",
}
_REMOVED_DOWNSTREAM_KEYS = frozenset(
    {"with-qos-preemption", "qosConfiguration", "schedulingConfig"}
)


@dataclass(frozen=True)
class SoperatorNodesetsProfileSettings:
    default: str = ""
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class SoperatorWizardSettings:
    release_namespace: str
    release_name: str
    release_timeout: str
    defaults: tuple[ComponentDefault, ...]
    wizard_fields: dict[str, dict[str, Any]]
    mk8s_gpu: Mk8sGpuAppPolicy
    nodesets: SoperatorNodesetsProfileSettings


def _as_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _validate_removed_downstream_keys(value: Any, *, field_label: str) -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            if key in _REMOVED_DOWNSTREAM_KEYS:
                raise ValueError(
                    f"{field_label} contains removed downstream-only field '{key}'; "
                    "the official nebius/soperator release is the only supported contract"
                )
            _validate_removed_downstream_keys(child, field_label=f"{field_label}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_removed_downstream_keys(child, field_label=f"{field_label}[{index}]")


def _validate_nodesets_profile(raw_profile: dict[str, Any], *, field_label: str) -> None:
    if "role_mapping" in raw_profile:
        raise ValueError(
            f"{field_label}.role_mapping is no longer supported; use {field_label}.placements"
        )
    placements = raw_profile.get("placements")
    if placements is not None:
        if not isinstance(placements, dict):
            raise ValueError(f"{field_label}.placements must be a mapping")
        for raw_name, raw_placement in placements.items():
            name = _as_text(raw_name)
            if not name:
                raise ValueError(f"{field_label}.placements entries must have non-empty names")
            if not isinstance(raw_placement, dict):
                raise ValueError(f"{field_label}.placements.{name} must be a mapping")
            for legacy_key in (
                "k8s_node_filter_name",
                "chart_filter_paths",
                "chart_affinity_paths",
            ):
                if legacy_key in raw_placement:
                    raise ValueError(
                        f"{field_label}.placements.{name}.{legacy_key} is no longer supported"
                    )
            kind = _as_text(raw_placement.get("kind"))
            if kind not in _PLACEMENT_KINDS:
                allowed = ", ".join(sorted(_PLACEMENT_KINDS))
                raise ValueError(f"{field_label}.placements.{name}.kind must be one of: {allowed}")
            bindings = raw_placement.get("soperator_value_bindings")
            if bindings is not None and not isinstance(bindings, dict):
                raise ValueError(
                    f"{field_label}.placements.{name}.soperator_value_bindings must be a mapping"
                )
    mk8s_profile = raw_profile.get("mk8s")
    if not isinstance(mk8s_profile, dict):
        return
    node_groups = mk8s_profile.get("node_groups")
    if isinstance(node_groups, dict):
        for raw_group_name, raw_group in node_groups.items():
            if isinstance(raw_group, dict) and "nodeset_name" in raw_group:
                group_name = _as_text(raw_group_name)
                raise ValueError(
                    f"{field_label}.mk8s.node_groups.{group_name}.nodeset_name is no longer "
                    "supported for service/support node groups; use placement_name"
                )
    worker_nodesets = mk8s_profile.get("worker_nodesets")
    if not isinstance(worker_nodesets, list):
        return
    for index, raw_worker in enumerate(worker_nodesets):
        if not isinstance(raw_worker, dict):
            continue
        if "node_group_key_prefix" in raw_worker:
            raise ValueError(
                f"{field_label}.mk8s.worker_nodesets[{index}].node_group_key_prefix is no "
                "longer supported; use node_group_prefix"
            )
        if "autoscaling_input" in raw_worker:
            raise ValueError(
                f"{field_label}.mk8s.worker_nodesets[{index}].autoscaling_input is no longer "
                "supported; worker autoscaling is controlled through "
                "inputs.soperator.worker_node_groups.<worker>.autoscaling"
            )
        for input_field, legacy_path in {
            "total_nodes_input": "soperator.worker_total_nodes",
            "nodes_per_group_input": "soperator.worker_nodes_per_group",
        }.items():
            if _as_text(raw_worker.get(input_field)) == legacy_path:
                raise ValueError(
                    f"{field_label}.mk8s.worker_nodesets[{index}].{input_field} must not use "
                    f"removed helper {legacy_path}; use a CPU/GPU shape-specific worker helper"
                )


def _parse_nodesets_profile_settings(
    raw: Any,
    *,
    field_label: str,
) -> SoperatorNodesetsProfileSettings:
    if not isinstance(raw, dict):
        raise ValueError(f"{field_label} must be a mapping")
    unknown = sorted(str(key) for key in raw if str(key) not in {"default", "profiles"})
    if unknown:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown))
    profiles_raw = raw.get("profiles")
    if not isinstance(profiles_raw, dict):
        raise ValueError(f"{field_label}.profiles must be a mapping")
    profiles: dict[str, dict[str, Any]] = {}
    for raw_name, raw_profile in profiles_raw.items():
        name = _as_text(raw_name)
        if not name or not isinstance(raw_profile, dict):
            raise ValueError(f"{field_label}.profiles entries must be named mappings")
        _validate_nodesets_profile(raw_profile, field_label=f"{field_label}.profiles.{name}")
        profiles[name] = copy.deepcopy(raw_profile)
    if set(profiles) != SOPERATOR_PROFILE_NAMES:
        expected = ", ".join(sorted(SOPERATOR_PROFILE_NAMES))
        raise ValueError(f"{field_label}.profiles must define exactly: {expected}")
    default = _as_text(raw.get("default"))
    if default not in profiles:
        raise ValueError(f"{field_label}.default references unknown profile '{default}'")
    return SoperatorNodesetsProfileSettings(default=default, profiles=profiles)


def parse_soperator_wizard_payload(payload: Any) -> SoperatorWizardSettings:
    if not isinstance(payload, dict):
        raise ValueError("soperator_wizard root must be a mapping")
    expected_root = {"schema", "id", "release", "wizard", "defaults", "cli"}
    unknown = sorted(str(key) for key in payload if str(key) not in expected_root)
    missing = sorted(expected_root - set(payload))
    if unknown or missing:
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unknown:
            details.append("unsupported: " + ", ".join(unknown))
        raise ValueError("soperator_wizard root fields are invalid (" + "; ".join(details) + ")")
    if payload.get("schema") != SOPERATOR_WIZARD_SCHEMA:
        raise ValueError(f"soperator_wizard.schema must be '{SOPERATOR_WIZARD_SCHEMA}'")
    if payload.get("id") != SOPERATOR_WIZARD_ID:
        raise ValueError(f"soperator_wizard.id must be '{SOPERATOR_WIZARD_ID}'")
    _validate_removed_downstream_keys(payload, field_label="soperator_wizard")

    release = payload.get("release")
    if not isinstance(release, dict):
        raise ValueError("soperator_wizard.release must be a mapping")
    unknown_release = sorted(
        str(key) for key in release if str(key) not in {"namespace", "name", "timeout"}
    )
    if unknown_release:
        raise ValueError(
            "soperator_wizard.release has unsupported field(s): " + ", ".join(unknown_release)
        )
    namespace = _as_text(release.get("namespace"))
    name = _as_text(release.get("name"))
    timeout = _as_text(release.get("timeout"))
    if not namespace or not name:
        raise ValueError("soperator_wizard.release namespace and name must be non-empty")
    if not _GO_DURATION_RE.fullmatch(timeout):
        raise ValueError("soperator_wizard.release.timeout must be a Go-style duration")

    cli = payload.get("cli")
    if not isinstance(cli, dict):
        raise ValueError("soperator_wizard.cli must be a mapping")
    unknown_cli = sorted(
        str(key) for key in cli if str(key) not in {"mk8s_gpu_policy", "soperator_nodesets_profile"}
    )
    if unknown_cli:
        raise ValueError("soperator_wizard.cli has unsupported field(s): " + ", ".join(unknown_cli))
    nodesets = _parse_nodesets_profile_settings(
        cli.get("soperator_nodesets_profile"),
        field_label="soperator_wizard.cli.soperator_nodesets_profile",
    )
    return SoperatorWizardSettings(
        release_namespace=namespace,
        release_name=name,
        release_timeout=timeout,
        defaults=_parse_component_defaults(
            payload.get("defaults"),
            field_label="soperator_wizard",
        ),
        wizard_fields=_parse_wizard_fields(
            payload.get("wizard"),
            field_label="soperator_wizard",
        ),
        mk8s_gpu=_parse_mk8s_gpu_app_policy(
            cli.get("mk8s_gpu_policy"),
            field_label="soperator_wizard.cli.mk8s_gpu_policy",
        ),
        nodesets=nodesets,
    )


@lru_cache(maxsize=1)
def soperator_wizard_settings() -> SoperatorWizardSettings:
    resource = importlib_resources.files("nebius_cxcli").joinpath(SOPERATOR_WIZARD_FILENAME)
    try:
        payload = yaml.safe_load(resource.read_text(encoding="utf-8")) or {}
    except (FileNotFoundError, OSError) as exc:
        raise FileNotFoundError(
            "Bundled Soperator wizard settings are missing from the installed package"
        ) from exc
    return parse_soperator_wizard_payload(payload)


def reset_soperator_wizard_settings_cache() -> None:
    soperator_wizard_settings.cache_clear()
