"""Save Grafana settings and run the ordinary current-input deployment pipeline."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .observability_routing import STORE_APPS

OBSERVABILITY_APPS = frozenset(
    {
        *STORE_APPS.values(),
        "grafana",
        "postgresql",
        "gateway-helm",
        "nebius-observability-agent",
        "opentelemetry-collector",
        "opentelemetry-logs",
        "prometheus-pushgateway",
    }
)


def _outside_scope(config: Mapping[str, Any], target: str) -> dict[str, Any]:
    from .config_model import to_dynamic_payload

    result = to_dynamic_payload(config)
    charts = []
    for row in result.get("apps", {}).get("charts", []):
        row_target = row.get("target_ref") or row.get("instance_id")
        if row_target == target and row.get("id") in OBSERVABILITY_APPS:
            continue
        if (
            row.get("id") in OBSERVABILITY_APPS
            and not row.get("enabled")
            and not row.get("target_ref")
            and row.get("instance_id") in {None, row.get("id")}
        ):
            # The starter catalog's disabled template becomes a target-owned row.
            continue
        if row_target == target and row.get("id") == "soperator":
            row.get("values", {}).pop("observability", None)
        charts.append(row)
    result.setdefault("apps", {})["charts"] = charts
    for row in result.get("deploy", {}).get("targets", []):
        if row.get("instance_id") == target:
            row.pop("observability", None)
    return result


def install_observability(
    config: Path,
    target: str,
    candidate: dict[str, Any],
    *,
    expected_bytes: bytes,
    emit: Callable[[str], None],
) -> None:
    from . import cli
    from .config_loader import load_config, normalize_runtime_config_payload
    from .config_model import to_dynamic_payload
    from .deployment_cli import deployment_execution
    from .observability_routing import native_defaults_scope
    from .paths import resolve_project_paths
    from .soperator_generation import require_current_active_soperator_snapshots

    paths = resolve_project_paths(config)
    with native_defaults_scope(emit):
        loaded = load_config(config, persist_normalized=False)
        source = to_dynamic_payload(loaded)
        candidate = to_dynamic_payload(candidate)
        normalize_runtime_config_payload(source, base_dir=config.parent)
        normalize_runtime_config_payload(candidate, base_dir=config.parent)
        if _outside_scope(source, target) != _outside_scope(candidate, target):
            raise RuntimeError(
                "Grafana selection changed configuration outside target observability"
            )
        emit(
            "Saving Grafana settings, rendering current configuration, and running normal deployment checks."
        )
        with deployment_execution(
            config=loaded,
            paths=paths,
            target_ref="project",
            operation_id="grafana install",
            bootstrap_backend=False,
        ):
            if config.read_bytes() != expected_bytes:
                raise RuntimeError(
                    "Config changed during Grafana selection; no settings were saved"
                )
            require_current_active_soperator_snapshots(source, paths)
            if candidate != source:
                cli._write_runtime_payload_config(config, candidate, expected_bytes=expected_bytes)
            cli.render_command(config_path=config, force=True)
            cli.deploy_command(config_path=config)
        emit(f"Grafana installation completed; desired settings are saved in {config}")
