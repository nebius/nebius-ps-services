"""Visible, target-scoped MysteryBox sync configuration for the shared wizard."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .cluster_config import require_cluster_row
from .component_instances import component_instance_id, component_type_id
from .deploy_targets import app_chart_target_ref, enabled_cluster_target_refs
from .mysterybox_eso import (
    DEFAULT_SYNC_NAMESPACE,
    EXTERNAL_SECRETS_APP_ID,
    mysterybox_eso_selected_target_refs,
    normalize_mysterybox_eso_project_settings,
)
from .provider_options import OptionChoice
from .runtime_validation import _validate_cluster_secrets

SYNC_QUESTION = "Sync SecretBox secrets to Kubernetes secrets using ESO?"


def _eso_row(payload: dict[str, Any], target: str) -> dict[str, Any] | None:
    rows = [
        row
        for row in payload.get("apps", {}).get("charts", [])
        if component_type_id(row) == EXTERNAL_SECRETS_APP_ID
        and (app_chart_target_ref(row) or component_instance_id(row)) == target
    ]
    if len(rows) > 1:
        raise ValueError(f"Multiple external-secrets entries for target '{target}'")
    return rows[0] if rows else None


@dataclass
class MysteryBoxSyncWizard:
    """Remember completed choices; revisiting a component permits editing them."""

    handled_targets: set[str] = field(default_factory=set)
    activated_targets: set[str] = field(default_factory=set)
    visited_components: set[str] = field(default_factory=set)

    def configure(
        self,
        payload: dict[str, Any],
        component: dict[str, Any],
        *,
        confirm: Callable[[str, bool], str],
        prompt: Callable[..., tuple[Any, bool]],
        is_back: Callable[[Any], bool],
        append_eso: Callable[[dict[str, Any], str], Any] | None,
        emit: Callable[[str], None],
    ) -> str:
        if component.get("inputs", {}).get("create_initial_versions") is False:
            return "continue"
        instance = component_instance_id(component)
        if not self.visited_components or instance in self.visited_components:
            emit(
                "Each target's sync setting applies to all ordinary MysteryBox secrets. "
                "WireGuard access secrets are excluded; unselected targets keep their settings."
            )
            targets = enabled_cluster_target_refs(payload)
            if not targets:
                while True:
                    decision = confirm(SYNC_QUESTION, False)
                    if decision in {"back", "quit"}:
                        return decision
                    if decision == "no":
                        break
                    emit(
                        "Configure a Kubernetes target first, or choose No for standalone secrets."
                    )
            else:
                while True:
                    selected = list(targets)
                    if len(targets) > 1:
                        selected, stopped = prompt(
                            "Kubernetes targets to configure for MysteryBox sync",
                            list(mysterybox_eso_selected_target_refs(payload)) or [targets[0]],
                            choices=[OptionChoice(value=t, label=t) for t in targets],
                            type_hint="list(string)",
                            required=True,
                        )
                        if stopped:
                            return "quit"
                        if is_back(selected):
                            return "back"
                        if (
                            not isinstance(selected, list)
                            or not selected
                            or any(target not in targets for target in selected)
                        ):
                            emit("Select at least one of the configured Kubernetes targets.")
                            continue
                    outcome = "continue"
                    for target in dict.fromkeys(selected):
                        outcome = self._configure_target(
                            payload,
                            target,
                            confirm=confirm,
                            prompt=prompt,
                            is_back=is_back,
                            append_eso=append_eso,
                            emit=emit,
                        )
                        if outcome != "continue":
                            break
                    if outcome == "back" and len(targets) > 1:
                        continue
                    if outcome != "continue":
                        return outcome
                    break
        self.visited_components.add(instance)
        active = mysterybox_eso_selected_target_refs(payload)
        emit(
            "MysteryBox synchronization enabled for: " + ", ".join(active)
            if active
            else "MysteryBox synchronization is disabled; collecting secret names and payload keys."
        )
        return "continue"

    def _configure_target(
        self,
        payload: dict[str, Any],
        target: str,
        *,
        confirm: Callable[[str, bool], str],
        prompt: Callable[..., tuple[Any, bool]],
        is_back: Callable[[Any], bool],
        append_eso: Callable[[dict[str, Any], str], Any] | None,
        emit: Callable[[str], None],
    ) -> str:
        while True:
            emit(f"Kubernetes target: {target}")
            decision = confirm(
                SYNC_QUESTION, target in mysterybox_eso_selected_target_refs(payload)
            )
            if decision in {"back", "quit"}:
                return decision
            candidate = copy.deepcopy(payload)
            row = require_cluster_row(candidate, target)
            if row.get("secrets") is None:
                row["secrets"] = {}
            if row["secrets"].get("mysterybox") is None:
                row["secrets"]["mysterybox"] = {}
            settings = row["secrets"]["mysterybox"]
            settings["enabled"] = decision == "yes"
            activated = False
            if decision == "yes":
                eso = _eso_row(candidate, target)
                if eso is None or not eso.get("enabled"):
                    if append_eso is None:
                        emit("The external-secrets component is unavailable in this catalog.")
                        continue
                    activation = confirm(
                        f"Add or enable External Secrets Operator for '{target}'?", True
                    )
                    if activation == "quit":
                        return "quit"
                    if activation != "yes":
                        continue
                    if eso is None:
                        append_eso(candidate, target)
                    else:
                        eso["enabled"] = True
                    activated = True
                while True:
                    namespaces, stopped = prompt(
                        f"Synchronization namespaces for {target}",
                        settings.get("sync_namespaces", [DEFAULT_SYNC_NAMESPACE]),
                        type_hint="list(string)",
                        required=True,
                    )
                    if stopped:
                        return "quit"
                    if is_back(namespaces):
                        break
                    try:
                        _validate_cluster_secrets(
                            {"mysterybox": {"enabled": True, "sync_namespaces": namespaces}},
                            field_label=f"{target}.secrets",
                            materialized=False,
                        )
                    except ValueError as exc:
                        emit(str(exc))
                        continue
                    settings["sync_namespaces"] = namespaces
                    break
                if is_back(namespaces):
                    continue
                normalize_mysterybox_eso_project_settings(candidate)
            # Commit only this target's completed changes; normalization of the
            # candidate must not alter any unselected target or existing chart.
            require_cluster_row(payload, target)["secrets"] = row["secrets"]
            if activated:
                original = _eso_row(payload, target)
                if original is None:
                    payload.setdefault("apps", {}).setdefault("charts", []).append(
                        _eso_row(candidate, target)
                    )
                else:
                    original["enabled"] = True
                self.activated_targets.add(target)
                emit(f"Selected apps:external-secrets@{target} for synchronization.")
            self.handled_targets.add(target)
            return "continue"
