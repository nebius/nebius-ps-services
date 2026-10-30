"""Native node-template placement validation and guided creation."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from functools import partial
from typing import Any, cast

import yaml

from .provider_options import OptionChoice

TAINT_EFFECTS = {
    "NO_SCHEDULE": "NoSchedule",
    "PREFER_NO_SCHEDULE": "PreferNoSchedule",
    "NO_EXECUTE": "NoExecute",
}
_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,61}[A-Za-z0-9])?\Z")
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


def _key(value: object, path: str, *, node_label: bool) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{path} must be a string Kubernetes key")
    parts = value.split("/")
    if len(parts) > 2 or not _NAME.fullmatch(parts[-1]):
        raise ValueError(f"{path} must be a valid Kubernetes key (name up to 63 characters)")
    if len(parts) == 2 and (
        len(parts[0]) > 253 or not all(_DNS_LABEL.fullmatch(part) for part in parts[0].split("."))
    ):
        raise ValueError(f"{path} must use a DNS subdomain prefix of at most 253 characters")
    if node_label and any(reserved in value for reserved in ("kubernetes.io", "k8s.io")):
        raise ValueError(f"{path} uses a node label key ignored by Nebius")


def _value(value: object, path: str) -> None:
    if not isinstance(value, str) or (value != "" and not _NAME.fullmatch(value)):
        raise ValueError(f"{path} must be a string Kubernetes label value (up to 63 characters)")


def validate_node_placement(group: Mapping[str, Any], *, path: str) -> None:
    """Validate authored and wizard-generated native inputs, without coercion."""
    labels = group.get("node_labels", {})
    if not isinstance(labels, Mapping):
        raise ValueError(f"{path}.node_labels must be a string-to-string mapping")
    for key, value in labels.items():
        _key(key, f"{path}.node_labels key", node_label=True)
        _value(value, f"{path}.node_labels value")
    taints = group.get("taints", [])
    if not isinstance(taints, list):
        raise ValueError(f"{path}.taints must be a list of key/value/effect mappings")
    seen: set[tuple[str, str]] = set()
    for index, taint in enumerate(taints):
        field = f"{path}.taints[{index}]"
        if not isinstance(taint, Mapping) or set(taint) != {"key", "value", "effect"}:
            raise ValueError(f"{field} must contain exactly key, value and effect")
        _key(taint["key"], f"{field}.key", node_label=False)
        _value(taint["value"], f"{field}.value")
        effect = taint["effect"]
        if not isinstance(effect, str) or effect not in TAINT_EFFECTS:
            raise ValueError(f"{field}.effect must be one of {', '.join(TAINT_EFFECTS)}")
        pair = (taint["key"], effect)
        if pair in seen:
            raise ValueError(f"{field} repeats a taint key/effect pair")
        seen.add(pair)


def placement_pod_spec(group: Mapping[str, Any]) -> dict[str, Any]:
    """Build guidance only; never mutate workloads or deployment configuration."""
    spec: dict[str, Any] = {}
    if group.get("node_labels"):
        spec["nodeSelector"] = dict(group["node_labels"])
    if group.get("taints"):
        spec["tolerations"] = [
            {
                "key": t["key"],
                "operator": "Equal",
                "value": t["value"],
                "effect": TAINT_EFFECTS[t["effect"]],
            }
            for t in group["taints"]
        ]
    return spec


def prompt_node_placement(
    *,
    prefix: str,
    group_name: str,
    prompt: Callable[..., tuple[object, str]],
    emit: Callable[[str], Any],
) -> tuple[dict[str, Any], str]:
    """Return complete placement or an empty result on back/quit."""
    mode, status = prompt(
        f"{prefix}.placement.rule",
        "none",
        required=True,
        choices=[
            OptionChoice(value=v, label=label)
            for v, label in (
                ("none", "None (no custom placement rules)"),
                ("label", "Label"),
                ("taint", "Taint"),
                ("both", "Both (label and taint)"),
            )
        ],
    )
    if status != "ok":
        return {}, status
    if mode not in ("none", "label", "taint", "both"):
        raise ValueError("Unknown node placement rule")
    if mode == "none":
        return {}, "ok"

    def text_field(path: str, default: str, validate: Callable[[object], None]) -> tuple[str, str]:
        while True:
            value, control = prompt(path, default, type_hint="string", required=True)
            if control != "ok":
                return "", control
            try:
                validate(value)
            except ValueError as exc:
                emit(f"Invalid value: {exc}")
                continue
            return cast(str, value), "ok"

    result: dict[str, Any] = {}
    labels: dict[str, str] = {}
    if mode in ("label", "both"):
        emit("Node labels need matching Pod nodeSelector fields to constrain placement.")
        while True:
            path = f"{prefix}.node_labels.<new>"

            def label_key(value: object, path: str = path) -> None:
                _key(value, path + ".key", node_label=True)
                if value in labels:
                    raise ValueError(f"{path}.key repeats an existing label key")

            key, status = text_field(path + ".key", "", label_key)
            if status != "ok":
                return {}, status
            value, status = text_field(
                path + ".value", group_name, partial(_value, path=path + ".value")
            )
            if status != "ok":
                return {}, status
            labels[key] = value
            more, status = prompt(f"{prefix}.node_labels.add_another", False, type_hint="bool")
            if status != "ok":
                return {}, status
            if not more:
                break
        result["node_labels"] = labels
    if mode in ("taint", "both"):
        emit("Tolerations permit use of tainted nodes; they do not direct Pods to this group.")
        taints: list[dict[str, str]] = []
        while True:
            path = f"{prefix}.taints[{len(taints)}]"
            default_key, default_value = (
                next(iter(labels.items())) if labels and not taints else ("", group_name)
            )
            key, status = text_field(
                path + ".key",
                default_key,
                partial(_key, path=path + ".key", node_label=False),
            )
            if status != "ok":
                return {}, status
            value, status = text_field(
                path + ".value", default_value, partial(_value, path=path + ".value")
            )
            if status != "ok":
                return {}, status
            effect, status = prompt(
                path + ".effect",
                "NO_SCHEDULE",
                required=True,
                choices=[
                    OptionChoice(value=v, label=label)
                    for v, label in (
                        (
                            "NO_SCHEDULE",
                            "NoSchedule (block new Pods without a matching toleration)",
                        ),
                        ("PREFER_NO_SCHEDULE", "PreferNoSchedule (soft scheduling preference)"),
                        ("NO_EXECUTE", "NoExecute (also evict Pods without a matching toleration)"),
                    )
                ],
            )
            if status != "ok":
                return {}, status
            candidate = [*taints, {"key": key, "value": value, "effect": cast(str, effect)}]
            try:
                validate_node_placement({"taints": candidate}, path=prefix)
            except ValueError as exc:
                emit(f"Invalid value: {exc}")
                continue
            taints = candidate
            more, status = prompt(f"{prefix}.taints.add_another", False, type_hint="bool")
            if status != "ok":
                return {}, status
            if not more:
                break
        result["taints"] = taints
    validate_node_placement(result, path=prefix)
    emit(
        "Pod YAML example only (apply placement to your workload's Pod template):\n"
        + yaml.safe_dump({"spec": placement_pod_spec(result)}, sort_keys=False)
    )
    return result, "ok"
