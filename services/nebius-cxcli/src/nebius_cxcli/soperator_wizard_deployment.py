"""Persist explicit deployment intent only for completed, selected managed targets."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import typer

from .component_instances import component_instance_id
from .deploy_targets import app_chart_target_ref
from .soperator_config_materialization import (
    _SOPERATOR_TARGET_MODE_MANAGED,
    _soperator_target_mode_by_target,
)
from .soperator_deployment_profile import FAST_DEV_TEST, STANDARD, deployment_profile
from .soperator_values import mark_explicit_value, soperator_rows


def _prompt_fast_deploy_profile(default: bool | None = True) -> bool:
    """Choose a profile; creation supplies a seed or requires an explicit answer."""
    typer.echo("Yes: Fast Dev/Test — reduced diagnostics with service and Slurm smoke readiness.")
    typer.echo("No: Standard — native diagnostic defaults and valid configured overrides.")
    return typer.confirm("Use fast deploy (Dev/Test only)?", default=default)


def reconcile_wizard_deployment(
    payload: dict[str, Any],
    *,
    target_refs: set[str],
    choose: Callable[[], bool] | None = None,
) -> dict[str, str]:
    modes = _soperator_target_mode_by_target(payload)
    notices = {}
    for row in soperator_rows(payload):
        target = app_chart_target_ref(row) or component_instance_id(row)
        if target not in target_refs or modes.get(target) != _SOPERATOR_TARGET_MODE_MANAGED:
            continue
        values = row.setdefault("values", {})
        profile = deployment_profile(values)
        if "deploymentProfile" not in values:
            profile = FAST_DEV_TEST if choose is None or choose() else STANDARD
        values["deploymentProfile"] = profile
        mark_explicit_value(row, ("deploymentProfile",))
        notices[target] = profile
    return notices
