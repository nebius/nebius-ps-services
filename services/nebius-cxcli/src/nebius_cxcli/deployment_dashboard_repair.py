"""Admit the exact dashboard delivery repair beneath shared deploy authority."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from .deployment_applications import target_bundle_digest
from .deployment_bundle_files import target_files
from .deployment_install_repair import InstallInputTransition
from .deployment_state import DeploymentGeneration
from .soperator_adapter import (
    SOPERATOR_MONITORING_DASHBOARDS_POST_FLUX_DIGESTS,
    render_soperator_monitoring_dashboard_documents,
)
from .soperator_install_render_repair import (
    DASHBOARD_FILE,
    REPAIR_REASON,
    _file_hashes,
    dashboard_repair_candidate,
    validate_dashboard_render_delta,
)
from .soperator_receipt_io import read_owner_only_json
from .soperator_release import load_soperator_release_snapshot, soperator_release_snapshot_path
from .soperator_release_source import ensure_soperator_release_source


def dashboard_input_transition(
    executor: Any,
    *,
    current: DeploymentGeneration,
    desired: DeploymentGeneration,
    target_ref: str,
    entry: Mapping[str, Any],
) -> InstallInputTransition | None:
    """Validate locally; only the existing cluster owner may authorize publication."""
    desired_bundle = target_bundle_digest(desired, target_ref)
    if not entry or entry["desiredBundle"] == desired_bundle:
        return None
    before, after = target_files(current, target_ref), target_files(desired, target_ref)
    if DASHBOARD_FILE not in after:
        return None
    paths = executor.cli._paths_for_target_flux_dir(
        executor.paths, executor._selected_target(target_ref)
    )
    saved_path = paths.reports_dir / f"soperator-install-render-repair-{target_ref}.json"
    if DASHBOARD_FILE in before and not saved_path.exists():
        return None
    if entry.get("status") != "executing" or entry.get("inputRepair") or entry.get("evidence"):
        raise RuntimeError("Dashboard input repair requires its unfinished predecessor")
    if saved_path.exists():
        saved = read_owner_only_json(saved_path, label="Dashboard render repair")
        if (
            not isinstance(saved, Mapping)
            or saved.get("schema") != REPAIR_REASON
            or saved.get("targetRef") != target_ref
            or saved.get("replacementFiles") != _file_hashes(before)
            or not isinstance(saved.get("predecessorFiles"), Mapping)
        ):
            raise RuntimeError("Dashboard render transaction differs from its receipt")
        before = {
            name: base64.b64decode(content, validate=True)
            for name, content in saved["predecessorFiles"].items()
        }
        if _file_hashes(before) != saved.get("previousFiles"):
            raise RuntimeError("Dashboard predecessor bytes lost their identity")
    root = (
        next(
            row["flux_dir"]
            for row in current.manifest["deploy"]["targets"]
            if row["target_ref"] == target_ref
        ).rstrip("/")
        + "/"
    )

    def generation(files: Mapping[str, bytes]) -> DeploymentGeneration:
        return replace(
            current,
            files={
                **{name: data for name, data in current.files.items() if not name.startswith(root)},
                **{root + name: base64.b64encode(data).decode() for name, data in files.items()},
            },
        )

    if target_bundle_digest(generation(before), target_ref) != entry["desiredBundle"]:
        raise RuntimeError("Dashboard recovery lost its application bundle predecessor")
    snapshot = load_soperator_release_snapshot(
        soperator_release_snapshot_path(paths.reports_dir, target_ref)
    )
    chart = snapshot.charts.get("monitoringDashboards")
    if chart is None or chart.digest not in SOPERATOR_MONITORING_DASHBOARDS_POST_FLUX_DIGESTS:
        raise RuntimeError("Dashboard input repair requires a reviewed frozen chart digest")
    source = ensure_soperator_release_source(snapshot)
    dashboards = render_soperator_monitoring_dashboard_documents(
        {"observability": {"enabled": True}},
        release=snapshot,
        source_root=Path(source.source_dir),
    )
    validate_dashboard_render_delta(
        before,
        after,
        release=snapshot.release,
        chart_digest=chart.digest,
        expected_dashboards=dashboards,
    )
    candidate = dashboard_repair_candidate(
        before, release=snapshot.release, chart_digest=chart.digest, dashboards=dashboards
    )
    # The cluster owner writes the closed candidate, not the fresh render. Prove
    # that those exact bytes carry the desired application identity as well.
    if target_bundle_digest(generation(candidate), target_ref) != desired_bundle:
        raise RuntimeError("Dashboard repair candidate differs from the desired bundle")
    return InstallInputTransition(
        entry["desiredBundle"],
        desired_bundle,
        _file_hashes(before),
        _file_hashes(candidate),
        REPAIR_REASON,
    )
