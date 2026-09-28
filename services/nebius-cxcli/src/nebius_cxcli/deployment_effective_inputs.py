"""Keep private effective application metadata bound to authenticated target inputs."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .deployment_applications import ApplicationJournal, target_bundle_digest
from .deployment_observation import target_documents
from .deployment_resolution import bind_generation_compatibility
from .deployment_state import DeploymentGeneration, _safe_relative, canonical_json
from .generated_manifest import GENERATED_MANIFEST_FILENAME
from .paths import ProjectPaths
from .project_bundle_transaction import ProjectBundleTransaction


def prepare_recovered_application_metadata(
    cli: Any,
    authored: DeploymentGeneration,
    paths: ProjectPaths,
    manifest: Mapping[str, Any],
    *,
    selected: Sequence[str],
    assert_authority: Callable[[], None],
) -> Mapping[str, Any]:
    """Bind private metadata only for exact checkpoint-owned application changes.

    A different stage manifest keeps its own strict admission. The authored
    generation and saved plan are never rewritten by this derived publication.
    """
    current = DeploymentGeneration.capture(paths, manifest)
    if current.manifest != authored.manifest or current.files == authored.files:
        return manifest
    journal = ApplicationJournal(
        paths.reports_dir / "deployment-applications.json",
        generation=authored.identity,
        selected=selected,
    )
    roots = {
        row["target_ref"]: _safe_relative(row["flux_dir"]).as_posix() + "/"
        for row in authored.manifest.get("deploy", {}).get("targets", [])
    }
    changed = {
        name
        for name in current.files.keys() | authored.files.keys()
        if current.files.get(name) != authored.files.get(name)
    }
    for name in changed:
        owners = [ref for ref, prefix in roots.items() if name.startswith(prefix)]
        if len(owners) != 1 or owners[0] not in selected:
            raise RuntimeError("Recovered application metadata has unexplained artifact changes")
    for ref, prefix in roots.items():
        if not any(name.startswith(prefix) for name in changed):
            continue
        entry = journal.entry(ref)
        if not entry or target_bundle_digest(current, ref) != entry["desiredBundle"]:
            raise RuntimeError("Recovered application bytes differ from their target checkpoint")
        resource_paths: set[str] = set()
        target_documents(current, ref, resource_paths=resource_paths)
        for name in changed:
            if not name.startswith(prefix):
                continue
            # The checkpoint binds resource documents, not arbitrary files or
            # kustomize transformations. Only existing declared resources may
            # carry this metadata-only recovery; additions/removals stay closed.
            if (
                name not in authored.files
                or name not in current.files
                or name not in resource_paths
            ):
                raise RuntimeError("Recovered application artifact has no checkpointed resource")
    assert_authority()
    bound = bind_generation_compatibility(cli, current, paths)
    updated = bound.manifest_for_paths(paths)
    path = paths.generated_dir / GENERATED_MANIFEST_FILENAME
    transaction = ProjectBundleTransaction(paths.project_dir)
    snapshot = transaction.snapshot_preimages([path])[path]
    if (
        snapshot.content is None
        or json.loads(snapshot.content) != manifest
        or DeploymentGeneration.capture(paths, manifest) != current
    ):
        raise RuntimeError("Recovered application inputs changed during metadata admission")
    assert_authority()
    transaction.commit(
        {path: canonical_json(updated) + b"\n"}, expected_preimages={path: snapshot.sha256}
    )
    return updated
