"""Runtime prerequisites shared by dedicated Soperator install and upgrade."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .paths import ProjectPaths
from .runtime_config import to_plain_data
from .soperator_backup_runtime import (
    ensure_soperator_backup_runtime_secrets,
    soperator_backup_enabled_for_target,
)
from .soperator_checks_preflight import _rendered_soperator_upstream_values
from .soperator_release import load_soperator_release_snapshot, soperator_release_snapshot_path
from .soperator_release_artifacts import render_soperator_consumers, soperator_consumer_namespaces
from .soperator_release_source import ensure_soperator_release_source
from .soperator_sssd_runtime import ensure_soperator_sssd_runtime, sssd_objects
from .soperator_values import soperator_rows


def ensure_soperator_runtime_before_flux(
    config: Any,
    *,
    paths: ProjectPaths,
    extra_env: dict[str, str] | None,
    target_ref: str = "",
    prompt: bool = False,
    emit: Callable[[str], None] | None = None,
    assert_authority: Callable[[], object] | None = None,
) -> None:
    payload = to_plain_data(config)
    if not soperator_rows(payload):
        return
    if not soperator_backup_enabled_for_target(config, target_ref=target_ref) and not sssd_objects(
        config, target_ref=target_ref, namespaces={"slurmCluster": "", "nodesets": ""}
    ):
        return
    if assert_authority is None:
        raise RuntimeError("Soperator runtime writes require dedicated lifecycle authority")
    snapshot = load_soperator_release_snapshot(
        soperator_release_snapshot_path(
            paths.reports_dir,
            target_ref or paths.path_project_folder,
        )
    )
    source = ensure_soperator_release_source(snapshot)
    consumers = render_soperator_consumers(
        snapshot, source, _rendered_soperator_upstream_values(paths.flux_dir)
    )
    namespaces = soperator_consumer_namespaces(snapshot, consumers)
    if soperator_backup_enabled_for_target(config, target_ref=target_ref):
        if "backupConfig" not in namespaces:
            raise ValueError("Soperator backup has no rendered consumer namespace")
        ensure_soperator_backup_runtime_secrets(
            config,
            namespace=namespaces["backupConfig"],
            assert_authority=assert_authority,
            extra_env=extra_env,
            target_ref=target_ref,
            prompt=prompt,
            emit=emit,
        )
    ensure_soperator_sssd_runtime(
        config,
        target_ref=target_ref,
        namespaces=namespaces,
        extra_env=extra_env,
        assert_authority=assert_authority,
        emit=emit,
        prompt=prompt,
    )
