"""Derive application intent from frozen inputs and authoritative Terraform outputs."""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any

from .compatibility_artifacts import bind_flux_artifacts, frozen_chart_inputs
from .deployment_state import DeploymentGeneration, _safe_relative
from .frozen_catalog import use_frozen_catalog
from .generated_manifest import runtime_config_from_manifest
from .paths import ProjectPaths
from .soperator_generation import use_generation_soperator_releases


def publish_application_inputs(
    cli: Any,
    generation: DeploymentGeneration,
    paths: ProjectPaths,
    *,
    current: DeploymentGeneration,
    selected: Sequence[str],
    assert_authority: Callable[[], None],
) -> Mapping[str, Any]:
    """Atomically publish effective target bytes and their compatibility evidence."""
    from .deployment_state import canonical_json
    from .generated_manifest import GENERATED_MANIFEST_FILENAME
    from .project_bundle_transaction import ProjectBundleTransaction

    roots = {}
    for target in generation.manifest.get("deploy", {}).get("targets", []):
        ref, raw = target["target_ref"], target["flux_dir"]
        root = _safe_relative(raw)
        if ref in roots or root.parts[0] != "flux":
            raise RuntimeError("Application publication target is missing or ambiguous")
        roots[ref] = raw + "/"
    if len(set(selected)) != len(selected) or set(selected) - roots.keys():
        raise RuntimeError("Application publication selection is missing or ambiguous")
    for ref in selected:
        if any(
            ref != other and (prefix.startswith(roots[ref]) or roots[ref].startswith(prefix))
            for other, prefix in roots.items()
        ):
            raise RuntimeError("Application publication targets overlap")
    flux_prefixes = tuple(roots[ref] for ref in selected)
    prefixes = flux_prefixes + tuple(
        _safe_relative(f"grafana_dashboards/{ref}").as_posix() + "/" for ref in selected
    )
    before = {k: v for k, v in current.files.items() if k.startswith(prefixes)}
    after = {k: v for k, v in generation.files.items() if k.startswith(prefixes)}
    if any(prefix + "kustomization.yaml" not in after for prefix in flux_prefixes):
        raise RuntimeError("Application publication target bundle is incomplete")
    updates = {
        paths.generated_dir / _safe_relative(name): base64.b64decode(content, validate=True)
        for name, content in after.items()
    }
    removals = [paths.generated_dir / _safe_relative(name) for name in before.keys() - after.keys()]
    if not updates and not removals:
        return current.manifest_for_paths(paths)
    composite = replace(
        current,
        files={**{k: v for k, v in current.files.items() if k not in before}, **after},
    )
    assert_authority()
    bound = bind_generation_compatibility(cli, composite, paths)
    manifest = bound.manifest_for_paths(paths)
    manifest_path = paths.generated_dir / GENERATED_MANIFEST_FILENAME
    updates[manifest_path] = canonical_json(manifest) + b"\n"
    transaction = ProjectBundleTransaction(paths.project_dir)
    snapshots = transaction.snapshot_preimages([*updates, *removals])
    for path, snapshot in snapshots.items():
        if path == manifest_path:
            import json

            if snapshot.content is None or json.loads(
                snapshot.content
            ) != current.manifest_for_paths(paths):
                raise RuntimeError("Application manifest changed before publication")
            continue
        name = path.relative_to(paths.generated_dir).as_posix()
        expected = base64.b64decode(before[name], validate=True) if name in before else None
        if snapshot.content != expected:
            raise RuntimeError("Application files changed before publication")
    if DeploymentGeneration.capture(paths, current.manifest_for_paths(paths)) != current:
        raise RuntimeError("Application generation changed before publication")
    assert_authority()
    transaction.commit(
        updates,
        removals=removals,
        expected_preimages={path: snapshot.sha256 for path, snapshot in snapshots.items()},
    )
    return manifest


def resolved_application_generation(
    cli: Any,
    generation: DeploymentGeneration,
    paths: ProjectPaths,
    *,
    initialize_terraform: bool = True,
    target_ref: str = "",
    render_inputs: Mapping[str, Any] | None = None,
) -> DeploymentGeneration:
    """Rebuild in a disposable cache; never trust mutable execution manifests."""
    with (
        use_frozen_catalog(generation.manifest.get("render", {}).get("inputs", {})),
        frozen_chart_inputs(
            generation.manifest.get("render", {}).get("compatibility", {}).get("chart_inputs", {})
        ),
    ):
        return _resolve(
            cli,
            generation,
            paths,
            initialize_terraform=initialize_terraform,
            target_ref=target_ref,
            render_inputs=render_inputs,
        )


def _resolve(
    cli: Any,
    generation: DeploymentGeneration,
    paths: ProjectPaths,
    *,
    initialize_terraform: bool = True,
    render_inputs: Mapping[str, Any] | None = None,
    target_ref: str = "",
    project_only: bool = False,
) -> DeploymentGeneration:
    config = runtime_config_from_manifest(generation.manifest)
    outputs = (
        dict(render_inputs)
        if render_inputs is not None
        else resolve_application_outputs(
            cli, config, paths, initialize_terraform=initialize_terraform
        )
    )
    if render_inputs is not None:
        # Saved inputs use the same deterministic bindings as freshly read outputs.
        cli.ensure_nfs_csi_app_rows(config)
        cli.materialize_mysterybox_eso_app_values(config, component_output_values=outputs)
    if outputs is None and not target_ref:
        return generation
    staged = cli.staged_generated_paths(paths)
    staged.generated_dir.rmdir()
    try:
        manifest = generation.materialize(staged)
        # Rendering covers the whole generation, even for a selected stage target.
        # Re-resolving any unchanged chart tag can select different package bytes.
        with use_generation_soperator_releases(generation, emit=lambda _: None):
            cli.render_flux(config, staged, component_output_values=outputs or {})
        bind_flux_artifacts(
            staged, manifest.get("render", {}).get("compatibility", {}).get("chart_inputs", {})
        )
        from .compatibility_execution import freeze_compatibility
        from .config_model import to_dynamic_payload
        from .deployment_state import digest
        from .runtime_config import to_plain_data

        resolved = DeploymentGeneration.capture(staged, manifest)
        result = replace(
            generation,
            files=resolved.files,
            manifest={
                **generation.manifest,
                "runtime_config": to_plain_data(config),
            },
        )
        if project_only:
            from .deployment_jail_state import overlay_accepted_target

            # Freeze compatibility only after restoring every byte outside the
            # selected target. The report must describe the returned bundle.
            result = overlay_accepted_target(generation, result, target_ref)
            cli.reset_generated_bundle(staged)
            result.materialize(staged)
        resolved_compatibility = freeze_compatibility(
            runtime_config_from_manifest(result.manifest), staged
        )
        return replace(
            result,
            manifest={
                **result.manifest,
                "render": {
                    **result.manifest.get("render", {}),
                    "source_config_sha256": digest(
                        to_dynamic_payload(result.manifest["runtime_config"])
                    ),
                    "compatibility": resolved_compatibility,
                    "resolved_compatibility": resolved_compatibility["report"],
                    "application_inputs": outputs or {},
                },
            },
        )
    finally:
        cli.reset_generated_bundle(staged)


def project_jail_generation(
    cli: Any,
    generation: DeploymentGeneration,
    paths: ProjectPaths,
    *,
    target_ref: str,
    jail_protection: str,
    render_inputs: Mapping[str, Any],
) -> DeploymentGeneration:
    """Derive once from frozen inputs; replay uses the sealed returned bytes."""
    from .deployment_jail_state import with_jail_protection

    projected = replace(
        generation,
        manifest={
            **generation.manifest,
            "runtime_config": with_jail_protection(
                generation.manifest["runtime_config"], target_ref, jail_protection
            ),
        },
    )
    with (
        use_frozen_catalog(generation.manifest.get("render", {}).get("inputs", {})),
        frozen_chart_inputs(
            generation.manifest.get("render", {}).get("compatibility", {}).get("chart_inputs", {})
        ),
    ):
        return _resolve(
            cli,
            projected,
            paths,
            render_inputs=render_inputs,
            target_ref=target_ref,
            initialize_terraform=False,
            project_only=True,
        )


def resolve_application_outputs(
    cli, config, paths, *, include_soperator_handoffs=True, initialize_terraform=True
):
    """Share the complete declared nonsecret app-output closure with chart preparation."""
    specs = cli._dedupe_component_output_specs(
        [
            *cli._required_runtime_component_output_specs(
                config, include_soperator_handoffs=include_soperator_handoffs
            ),
            *cli.mysterybox_eso_terraform_output_specs(config),
            *cli.nfs_csi_terraform_output_specs(config),
        ]
    )
    if not specs:
        return None
    # Only declared non-secret app inputs are persisted by the existing renderer.
    outputs = cli._runtime_component_output_values(
        config, paths, required_specs=specs, initialize_terraform=initialize_terraform
    )
    cli.ensure_nfs_csi_app_rows(config)
    cli.materialize_mysterybox_eso_app_values(config, component_output_values=outputs)
    return outputs


def bind_generation_compatibility(
    cli: Any, generation: DeploymentGeneration, paths: ProjectPaths
) -> DeploymentGeneration:
    """Rebind composed accepted configuration to its exact existing application bytes."""
    from .compatibility_execution import freeze_compatibility

    staged = cli.staged_generated_paths(paths)
    staged.generated_dir.rmdir()
    try:
        generation.materialize(staged)
        with (
            use_frozen_catalog(generation.manifest.get("render", {}).get("inputs", {})),
            frozen_chart_inputs(
                generation.manifest.get("render", {})
                .get("compatibility", {})
                .get("chart_inputs", {})
            ),
        ):
            frozen = freeze_compatibility(runtime_config_from_manifest(generation.manifest), staged)
        return replace(
            generation,
            manifest={
                **generation.manifest,
                "render": {**generation.manifest.get("render", {}), "compatibility": frozen},
            },
        )
    finally:
        cli.reset_generated_bundle(staged)
