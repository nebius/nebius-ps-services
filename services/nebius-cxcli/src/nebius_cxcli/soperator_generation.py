"""Bind reconfiguration to Soperator artifacts in an authenticated generation."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config_model import to_dynamic_payload
from .soperator_release import (
    SoperatorReleaseSnapshot,
    soperator_release_snapshot_from_payload,
    soperator_release_snapshot_path,
)

if TYPE_CHECKING:
    from .deployment_state import DeploymentGeneration
    from .paths import ProjectPaths


def require_current_active_soperator_snapshots(
    config: Mapping[str, Any], paths: ProjectPaths
) -> None:
    """Protect unfinished local authority before replacing config or generated files."""
    from .soperator_full_stack_upgrade import campaign_receipt_path, load_campaign_receipt
    from .soperator_operation import load_local_active_soperator_release_intent
    from .soperator_release import load_soperator_release_snapshot

    for row in to_dynamic_payload(config).get("apps", {}).get("charts", []):
        if row.get("id") != "soperator" or row.get("enabled") is not True:
            continue
        target = row["instance_id"]
        load_local_active_soperator_release_intent(paths=paths, target_ref=target)
        campaign = load_campaign_receipt(
            campaign_receipt_path(paths.project_dir, target_ref=target)
        )
        if campaign is not None and campaign.status != "complete":
            load_soperator_release_snapshot(
                soperator_release_snapshot_path(paths.reports_dir, target)
            )


@contextmanager
def use_generation_soperator_releases(
    generation: DeploymentGeneration, *, emit: Callable[[str], None]
) -> Iterator[Mapping[str, SoperatorReleaseSnapshot]]:
    """Use exact admitted snapshots; local reports and discovery are not substitutes."""
    from .soperator_release_artifacts import verify_soperator_release_artifacts
    from .soperator_release_resolver import (
        frozen_soperator_release_from_snapshot,
        use_frozen_soperator_release,
    )

    snapshots: dict[str, SoperatorReleaseSnapshot] = {}
    config = to_dynamic_payload(generation.manifest["runtime_config"])
    for row in config["apps"]["charts"]:
        if row.get("id") != "soperator" or row.get("enabled") is not True:
            continue
        target = row["instance_id"]
        path = soperator_release_snapshot_path(Path("reports"), target).as_posix()
        content = generation.files.get(path)
        if content is None:
            raise ValueError(f"Accepted deployment has no frozen Soperator snapshot for {target}")
        try:
            payload = json.loads(base64.b64decode(content, validate=True))
            if not isinstance(payload, dict) or not payload.get("snapshot_sha256"):
                raise ValueError("missing snapshot identity")
            snapshot = soperator_release_snapshot_from_payload(payload)
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(
                f"Accepted deployment has an invalid Soperator snapshot for {target}: {exc}"
            ) from exc
        if (
            snapshot.target_ref != target
            or snapshot.release != row.get("version")
            or snapshot.chart_oci_url("umbrella") != str(row.get("repo") or "").rstrip("/")
        ):
            raise ValueError(
                f"Accepted Soperator snapshot differs from configured release for {target}"
            )
        previous = snapshots.get(target)
        if previous is not None and previous != snapshot:
            raise ValueError(
                "Accepted deployment has conflicting snapshots for the same Soperator target"
            )
        snapshots[target] = snapshot

    with ExitStack() as scope:
        for snapshot in snapshots.values():
            emit(
                f"Verifying accepted Soperator {snapshot.release} artifacts before reconfiguration..."
            )
            frozen = frozen_soperator_release_from_snapshot(snapshot)
            verify_soperator_release_artifacts(snapshot, frozen.source)
            scope.enter_context(use_frozen_soperator_release(frozen))
            emit(f"Reusing the accepted Soperator {snapshot.release} artifact snapshot.")
        yield snapshots
