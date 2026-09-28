"""Composition adapter for the existing single-maintenance Soperator campaign.

The public command authors desired targets; shared deploy invokes this service
with frozen admissions. CLI adapters remain injectable at the composition root.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .deployment_campaign import CampaignDeploymentHooks
from .deployment_preparation import prepared_deployment
from .paths import ProjectPaths
from .soperator_full_stack_upgrade import SoperatorUpgradeCampaignIntent
from .soperator_jail_observation import observe_protected_directories


@prepared_deployment()
def run_upgrade_campaign(
    config_path: Path,
    target_ref: str | None = None,
    to_chart_version: str | None = None,
    to_k8s_version: str | None = None,
    to_os: str | None = None,
    to_gpu_stack_preset: str | None = None,
    node_group_os: Sequence[str] | None = None,
    node_group_gpu_stack_preset: Sequence[str] | None = None,
    node_group_strategy: str | None = None,
    strategy_max_surge_count: int | None = None,
    drain_timeout: str | None = None,
    job_policy: str | None = None,
    cancel_job: Sequence[str] | None = None,
    requeue_job: Sequence[str] | None = None,
    job_wait_timeout: str = "0s",
    job_refresh_interval: str = "30s",
    dry_run: bool = False,
    interactive: bool = True,
    generated_context: tuple[Any, ProjectPaths, Mapping[str, Any]] | None = None,
    assert_parent_fence: Callable[[], object] | None = None,
    deployment_hooks: CampaignDeploymentHooks | None = None,
    admitted_intent: SoperatorUpgradeCampaignIntent | None = None,
    desired_jail_protection: str | None = None,
) -> SoperatorUpgradeCampaignIntent | None:
    from . import cli
    from .soperator_jail_protection import (
        apply_frozen_jail_protection,
        assert_protection_extension,
        freeze_jail_protection,
    )

    sdk: cli.Any | None = None
    upgrade_progress = cli.SoperatorUpgradeProgress(cli.progress_console)
    try:
        execute = not dry_run
        if execute:
            if assert_parent_fence is None:
                raise RuntimeError("Campaign execution requires the shared deployment fence")
            assert_parent_fence()
        cancel_job_ids = tuple(cancel_job or ())
        requeue_job_ids = tuple(requeue_job or ())
        if job_policy or cancel_job_ids or requeue_job_ids:
            cli._validate_soperator_upgrade_job_controls(
                job_policy=job_policy,
                cancel_job_ids=cancel_job_ids,
                requeue_job_ids=requeue_job_ids,
                interactive=interactive,
            )
        if dry_run and not interactive:
            missing_selectors = [
                flag
                for flag, value in (
                    ("--to-release", to_chart_version),
                    ("--to-k8s-version", to_k8s_version),
                    ("--to-os", to_os),
                    ("--to-gpu-stack-preset", to_gpu_stack_preset),
                )
                if not cli._non_empty_text(value)
            ]
            if missing_selectors:
                raise RuntimeError(
                    "Non-interactive Soperator upgrade requires "
                    + ", ".join(missing_selectors)
                    + "."
                )
        with upgrade_progress.phase(
            "source-config",
            "Loading Soperator upgrade source configuration",
            success="Soperator upgrade source configuration loaded",
        ):
            try:
                loaded_source_config_sha256 = cli._sha256_file(config_path)
            except OSError:
                raise RuntimeError(
                    "Soperator upgrade could not read configuration; "
                    "provide a readable config.yaml file."
                ) from None
            source_payload = cli._load_source_payload(config_path)
            if cli._sha256_file(config_path) != loaded_source_config_sha256:
                raise RuntimeError(
                    "soperator upgrade config changed while the source plan was being loaded; "
                    "rerun from the current config"
                )
        target, target_row, is_onboarded = cli._resolve_soperator_command_target(
            source_payload,
            target_ref=target_ref,
            interactive=interactive,
        )
        if generated_context is None:
            from .deployment_cli import hydrate_upgrade_jail_config

            source_payload = hydrate_upgrade_jail_config(
                config_path, source_payload, target.target_ref
            )
        with upgrade_progress.phase(
            "target-ownership",
            "Resolving registered target ownership and generated deployment context",
            success="Registered target ownership and deployment context resolved",
        ):
            generated_config, paths, manifest = (
                generated_context or cli._load_deploy_context_readonly(config_path)
            )
            selected_targets = cli._resolve_selected_deploy_targets(
                manifest,
                requested_target_ref=target.target_ref,
                all_targets=False,
            )
            if len(selected_targets) != 1:
                raise RuntimeError("Soperator upgrade could not resolve the exact deploy target")
            selected_target = selected_targets[0]
            managed_source_component: cli.Mapping[str, cli.Any] | None
            try:
                managed_source_component = cli.find_source_mk8s_component(
                    source_payload,
                    target.target_ref,
                )
            except ValueError:
                managed_source_component = None
            terraform_modules = tuple(
                module_name
                for module_name, (_component_id, instance_id) in sorted(
                    cli._generated_bundle_mk8s_module_index(manifest).items()
                )
                if instance_id == target.target_ref
            )
            selected_kind = cli._non_empty_text(selected_target.get("kind")).lower()
            selected_ownership = cli._non_empty_text(selected_target.get("ownership")).lower()
            selected_component = cli._non_empty_text(selected_target.get("component_id")).lower()
            if is_onboarded:
                if (
                    not isinstance(target_row, cli.Mapping)
                    or cli._non_empty_text(target_row.get("kind")).lower() != "external-mk8s"
                    or cli._non_empty_text(target_row.get("ownership")).lower() != "external"
                    or selected_kind != "external-mk8s"
                    or selected_ownership != "external"
                    or managed_source_component is not None
                    or terraform_modules
                ):
                    raise RuntimeError(
                        "Soperator onboarded ownership is contradictory or overlaps Terraform"
                    )
            elif (
                managed_source_component is None
                or selected_ownership != "managed"
                or selected_component != "mk8s"
                or len(terraform_modules) != 1
            ):
                raise RuntimeError(
                    "Soperator managed ownership requires one exact MK8s component and "
                    "Terraform module"
                )
        receipt_path = cli.campaign_receipt_path(paths.project_dir, target_ref=target.target_ref)
        loaded_campaign_receipt = cli.load_campaign_receipt(receipt_path) if execute else None
        campaign_receipt = loaded_campaign_receipt
        explicit_release_selector = (
            cli.normalize_soperator_release_selector(to_chart_version)
            if cli._non_empty_text(to_chart_version)
            else None
        )
        explicit_kubernetes_selector = cli._non_empty_text(to_k8s_version).lower()
        recovery_intent = (
            cli.campaign_intent_from_payload(campaign_receipt.intent)
            if campaign_receipt is not None
            else admitted_intent
        )
        if recovery_intent is not None:
            if (
                desired_jail_protection is not None
                and desired_jail_protection != recovery_intent.jail_protection
            ):
                raise RuntimeError("Desired jail protection differs from the admitted campaign")
            if campaign_receipt is not None:
                cli.assert_config_authority_current(
                    campaign_receipt.config_generations,
                    initial_config_sha256=recovery_intent.source_config_sha256,
                    initial_project_snapshot_sha256=(
                        recovery_intent.source_project_snapshot_sha256
                    ),
                    current_config_sha256=cli._sha256_file(config_path),
                    current_project_snapshot_sha256=cli.project_generation_snapshot_sha256(paths),
                    current_project_generation_sha256=cli.ProjectBundleTransaction(
                        paths.project_dir
                    ).current_generation_sha256(),
                )
            elif (
                cli._sha256_file(config_path) != recovery_intent.source_config_sha256
                or cli.project_generation_snapshot_sha256(paths)
                != recovery_intent.source_project_snapshot_sha256
            ):
                raise RuntimeError("Admitted campaign source generation changed before execution")
            if recovery_intent.target_ref != target.target_ref:
                raise RuntimeError(
                    "recovery-required: the active full-stack campaign belongs to another target"
                )
            if (
                explicit_release_selector is not None
                and explicit_release_selector != recovery_intent.requested_release_selector
            ):
                raise RuntimeError(
                    "recovery-required: --to-release differs from the frozen campaign"
                )
            if (
                explicit_kubernetes_selector
                and explicit_kubernetes_selector != recovery_intent.requested_kubernetes_selector
            ):
                raise RuntimeError(
                    "recovery-required: --to-k8s-version differs from the frozen campaign"
                )
            if job_policy and job_policy != recovery_intent.job_policy:
                raise RuntimeError(
                    "recovery-required: --job-policy differs from the frozen campaign"
                )
            frozen_option_pairs = {
                "--to-os": (to_os, recovery_intent.target_os),
                "--to-gpu-stack-preset": (
                    to_gpu_stack_preset,
                    recovery_intent.target_gpu_stack_preset,
                ),
                "--node-group-strategy": (
                    node_group_strategy,
                    recovery_intent.node_group_strategy,
                ),
                "--drain-timeout": (drain_timeout, recovery_intent.drain_timeout),
            }
            changed_options = [
                flag
                for flag, (supplied, frozen) in frozen_option_pairs.items()
                if cli._non_empty_text(supplied) and cli._non_empty_text(supplied) != frozen
            ]
            if changed_options:
                raise RuntimeError(
                    "recovery-required: supplied option(s) differ from the frozen campaign: "
                    + ", ".join(changed_options)
                )
            if (
                strategy_max_surge_count is not None
                and strategy_max_surge_count != recovery_intent.strategy_max_surge_count
            ):
                raise RuntimeError(
                    "recovery-required: --strategy-max-surge-count differs from the frozen campaign"
                )
            if cancel_job_ids and tuple(sorted(set(cancel_job_ids))) != (
                recovery_intent.cancel_job_ids
            ):
                raise RuntimeError(
                    "recovery-required: --cancel-job differs from the frozen campaign"
                )
            if requeue_job_ids and tuple(sorted(set(requeue_job_ids))) != (
                recovery_intent.requeue_job_ids
            ):
                raise RuntimeError(
                    "recovery-required: --requeue-job differs from the frozen campaign"
                )
            frozen_groups = {
                alias: group
                for group in recovery_intent.node_groups
                for alias in (group.key, group.provider_name, group.provider_id)
            }
            for flag, values, attribute in (
                ("--node-group-os", tuple(node_group_os or ()), "target_os"),
                (
                    "--node-group-gpu-stack-preset",
                    tuple(node_group_gpu_stack_preset or ()),
                    "target_drivers_preset",
                ),
            ):
                for alias, value in cli._parse_soperator_node_group_overrides(
                    values,
                    option_name=flag,
                ).items():
                    group = frozen_groups.get(alias)
                    if group is None or value != getattr(group, attribute):
                        raise RuntimeError(
                            f"recovery-required: {flag} differs from the frozen campaign"
                        )
            intent = recovery_intent
            cancel_job_ids = intent.cancel_job_ids
            requeue_job_ids = intent.requeue_job_ids
            job_wait_timeout = intent.job_wait_timeout
            job_refresh_interval = intent.job_refresh_interval
            resolved_job_policy = intent.job_policy
            policy = cli.validate_disruption_policy(intent.node_group_strategy)
            resolved_drain_timeout = cli.resolve_drain_timeout(policy, intent.drain_timeout)
            resolved_max_surge = intent.strategy_max_surge_count or 0
        else:
            missing_non_interactive = []
            if not cli._non_empty_text(to_chart_version):
                missing_non_interactive.append("--to-release")
            if not explicit_kubernetes_selector:
                missing_non_interactive.append("--to-k8s-version")
            if not cli._non_empty_text(to_os):
                missing_non_interactive.append("--to-os")
            if not cli._non_empty_text(to_gpu_stack_preset):
                missing_non_interactive.append("--to-gpu-stack-preset")
            if not interactive and missing_non_interactive:
                raise RuntimeError(
                    "Non-interactive Soperator upgrade requires "
                    + ", ".join(missing_non_interactive)
                    + "."
                )
            intent = None
            resolved_job_policy = cli._validate_soperator_upgrade_job_controls(
                job_policy=job_policy,
                cancel_job_ids=cancel_job_ids,
                requeue_job_ids=requeue_job_ids,
                interactive=interactive,
            )
            requested_strategy = cli._non_empty_text(node_group_strategy)
            if not requested_strategy and interactive:
                requested_strategy = cli._prompt_upgrade_choice(
                    "soperator.upgrade.node_group_strategy",
                    cli.DISRUPTION_POLICY_ALLOW_UNAVAILABLE,
                    choices=[
                        cli.OptionChoice(
                            value=cli.DISRUPTION_POLICY_ALLOW_UNAVAILABLE,
                            label="zero-surge  (no spare quota; one node unavailable per group)",
                            recommended=True,
                        ),
                        cli.OptionChoice(
                            value=cli.DISRUPTION_POLICY_SAFE,
                            label="safe-surge  (temporary spare nodes)",
                        ),
                        cli.OptionChoice(
                            value=cli.DISRUPTION_POLICY_FORCE_DELETE,
                            label="force-delete  (shorter finite drain timeout)",
                        ),
                    ],
                    missing="node-group rollout strategy",
                )
            policy = cli.validate_disruption_policy(
                requested_strategy or cli.DISRUPTION_POLICY_ALLOW_UNAVAILABLE
            )
            requested_drain_timeout = cli._non_empty_text(drain_timeout)
            if not requested_drain_timeout and interactive:
                requested_drain_timeout = str(
                    cli._prompt_upgrade_scalar(
                        "soperator.upgrade.drain_timeout",
                        "auto",
                        type_hint="str",
                        required=True,
                        missing="node drain timeout",
                    )
                )
            requested_drain_timeout = requested_drain_timeout or "auto"
            resolved_drain_timeout = cli.resolve_drain_timeout(
                policy,
                requested_drain_timeout,
            )
            resolved_max_surge = cli.resolve_strategy_max_surge_count(
                policy,
                strategy_max_surge_count,
            )

        with cli.ExitStack() as stack:
            with upgrade_progress.phase(
                "cluster-handoff",
                "Preparing the target Kubernetes handoff",
                success="Target Kubernetes handoff prepared",
            ):
                kube_env = dict(
                    cli._prepare_cluster_handoff_kube_env(
                        generated_config,
                        paths,
                        stack=stack,
                        target=selected_target,
                        persist_local_kubeconfig=False,
                        set_current_context=False,
                        allow_terraform_output=not is_onboarded,
                        require_renewable_auth=True,
                    )
                    or {}
                )
                cluster_id = cli._non_empty_text(
                    (kube_env or {}).get(cli.GRAFANA_TARGET_CLUSTER_ID_ENV)
                ) or cli._non_empty_text((target_row or {}).get("cluster_id"))
                kube_context = cli._non_empty_text(
                    (kube_env or {}).get(cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV)
                )
                if not cluster_id or not kube_context:
                    raise RuntimeError(
                        "Soperator full-stack upgrade requires immutable cluster and "
                        "Kubernetes context identity"
                    )
                kubernetes_uid = cli._read_kube_system_namespace_uid(
                    kube_context=kube_context,
                    extra_env=kube_env,
                )
                if not kubernetes_uid:
                    raise RuntimeError("Soperator upgrade could not read the Kubernetes UID")
            project_id = str(generated_config.client_info.nebius.project_id).strip()
            with upgrade_progress.phase(
                "provider-inventory",
                "Reading Nebius cluster and node-group inventory",
                success="Nebius cluster and node-group inventory read",
            ) as provider_phase:
                sdk = cli.init_nebius_sdk(
                    parent_id=project_id or None,
                    endpoint=cli._non_empty_text(cli.os.environ.get("NEBIUS_ENDPOINT")) or None,
                    context="Soperator full-stack upgrade",
                    prefer_operator_auth=True,
                )
                executor = cli.Mk8sKubernetesVersionExecutor(sdk)
                if is_onboarded:
                    cluster = executor.get_cluster(cluster_id)
                    source_component: cli.Mapping[str, cli.Any] | None = None
                else:
                    source_component = managed_source_component
                    if source_component is None:
                        raise RuntimeError("managed Soperator source component disappeared")
                    cluster_name = cli.source_mk8s_cluster_name(
                        source_component,
                        fallback=target.target_ref,
                    )
                    cluster = executor.get_cluster_by_name(
                        project_id=project_id,
                        name=cluster_name,
                    )
                    observed_cluster_id = cli._live_mk8s_cluster_id(
                        cluster,
                        cluster_name=cluster_name,
                    )
                    if observed_cluster_id != cluster_id:
                        raise RuntimeError(
                            "generated Kubernetes handoff and Nebius API resolved different "
                            "MK8s cluster identities"
                        )
                current_kubernetes_version = cli._cluster_control_plane_minor_version(
                    cluster,
                    cluster_id=cluster_id,
                )
                provider_phase.update("Listing Nebius node groups")
                raw_node_groups = tuple(executor.list_node_groups(cluster_id))
                if deployment_hooks is not None:
                    deployment_hooks.bind_source_inventory(
                        raw_node_groups,
                        source_component,
                        compatibility_lookup=executor.compatibility_choices,
                    )
                provider_phase.update(
                    f"Read {len(raw_node_groups)} Nebius node groups",
                    current=len(raw_node_groups),
                    total=len(raw_node_groups),
                )
            if recovery_intent is not None:
                if (
                    recovery_intent.cluster_id != cluster_id
                    or recovery_intent.kubernetes_uid != kubernetes_uid
                ):
                    raise RuntimeError(
                        "recovery-required: live cluster identity differs from the frozen "
                        "Soperator campaign"
                    )
                infrastructure_authority = cli.build_soperator_infrastructure_authority(
                    target_ref=target.target_ref,
                    source_target=target_row,
                    generated_target=selected_target,
                    managed_component_instance=(
                        target.target_ref if managed_source_component is not None else ""
                    ),
                    terraform_modules=terraform_modules,
                    cluster_id=cluster_id,
                    kubernetes_uid=kubernetes_uid,
                    node_group_ids=tuple(
                        group.provider_id for group in recovery_intent.node_groups
                    ),
                    registration_sha256=(
                        "sha256:"
                        + cli.soperator_registration_fingerprint(
                            source_payload,
                            target_ref=target.target_ref,
                        )
                        if is_onboarded
                        else ""
                    ),
                    provider_api_authorized=recovery_intent.provider_api_authorized,
                    require_mutation_authorization=False,
                )
                if (
                    infrastructure_authority.digest != recovery_intent.backend_authority_sha256
                    or infrastructure_authority.ownership != recovery_intent.ownership
                    or infrastructure_authority.backend != recovery_intent.backend
                ):
                    raise RuntimeError(
                        "recovery-required: Soperator infrastructure ownership or backend "
                        "differs from the frozen campaign"
                    )
            else:
                with upgrade_progress.phase(
                    "supported-kubernetes-versions",
                    "Reading provider-supported Kubernetes versions",
                    success="Provider-supported Kubernetes versions resolved",
                ):
                    supported_versions = tuple(executor.control_plane_versions())
                requested_kubernetes_selector = explicit_kubernetes_selector
                if not requested_kubernetes_selector:
                    requested_kubernetes_selector = cli._prompt_upgrade_choice(
                        "soperator.upgrade.kubernetes_version",
                        "latest",
                        choices=cli._soperator_kubernetes_selector_choices(
                            current_version=current_kubernetes_version,
                            supported_versions=supported_versions,
                        ),
                        missing="target Kubernetes endpoint",
                    )
                target_kubernetes_version, kubernetes_hops = cli.resolve_kubernetes_upgrade_path(
                    selector=requested_kubernetes_selector,
                    current_version=current_kubernetes_version,
                    supported_versions=supported_versions,
                )
                release_selector = cli._new_soperator_release_selector(
                    explicit_release_selector,
                    interactive=interactive,
                    command_name="upgrade",
                    option_name="--to-release",
                    progress=upgrade_progress,
                )
                with upgrade_progress.phase(
                    "installed-release",
                    "Inspecting the installed Soperator release",
                    success="Installed Soperator release inspected",
                ):
                    live_release = cli._live_soperator_release_for_reconcile(env=kube_env)
                    if not live_release:
                        raise RuntimeError(
                            "soperator upgrade requires an existing live Soperator release"
                        )
                with upgrade_progress.phase(
                    "release-freeze",
                    f"Resolving and verifying Soperator release {release_selector}",
                    success=f"Soperator release {release_selector} source verified",
                ) as release_phase:
                    verified_source = cli.resolve_soperator_source(
                        release_selector,
                        emit=release_phase.update,
                    )
                os_selector = cli._non_empty_text(to_os)
                if not os_selector:
                    os_selector = cli._prompt_upgrade_choice(
                        "soperator.upgrade.node_os",
                        "auto",
                        choices=[
                            cli.OptionChoice(
                                value="auto",
                                label=(
                                    "auto  (latest provider-compatible OS per node group; "
                                    "exact API values are shown in the plan)"
                                ),
                                recommended=True,
                            ),
                            cli.OptionChoice(value="keep", label="keep  (current OS per group)"),
                        ],
                        missing="target node OS",
                    )
                gpu_selector = cli._non_empty_text(to_gpu_stack_preset)
                if not gpu_selector:
                    gpu_selector = cli._prompt_upgrade_choice(
                        "soperator.upgrade.gpu_stack",
                        "auto",
                        choices=[
                            cli.OptionChoice(
                                value="auto",
                                label=(
                                    "auto  (latest compatible Nebius drivers preset per node "
                                    "group; exact API values are shown in the plan)"
                                ),
                                recommended=True,
                            ),
                            cli.OptionChoice(
                                value="keep",
                                label="keep  (current provider GPU stack per group)",
                            ),
                        ],
                        missing="target provider GPU stack",
                    )
                zero_size_policy = "skip-with-proof"
                with upgrade_progress.phase(
                    "provider-compatibility",
                    f"Resolving provider compatibility for {len(raw_node_groups)} node groups",
                    success=(
                        f"Provider compatibility resolved for {len(raw_node_groups)} node groups"
                    ),
                ):
                    if source_component is not None:
                        live_groups = cli.live_node_groups_from_sdk(
                            source_component=source_component,
                            live_node_groups=raw_node_groups,
                        )
                    else:
                        live_groups = tuple(
                            cli.replace(
                                cli.live_node_group_from_sdk(raw),
                                gpu=cli._non_empty_text(
                                    getattr(
                                        getattr(
                                            getattr(
                                                getattr(raw, "spec", None),
                                                "template",
                                                None,
                                            ),
                                            "resources",
                                            None,
                                        ),
                                        "platform",
                                        None,
                                    )
                                ).startswith("gpu-"),
                            )
                            for raw in raw_node_groups
                        )
                    if deployment_hooks is not None:
                        retired_ids = set(deployment_hooks.contract["retired_group_ids"])
                        live_groups = tuple(
                            group for group in live_groups if group.id not in retired_ids
                        )
                    node_groups, compatibility_rows = cli._soperator_full_stack_node_group_targets(
                        live_groups=live_groups,
                        source_kubernetes_version=current_kubernetes_version,
                        target_kubernetes_version=target_kubernetes_version,
                        kubernetes_hops=kubernetes_hops,
                        to_os=os_selector,
                        to_gpu_stack_preset=gpu_selector,
                        os_overrides=cli._parse_soperator_node_group_overrides(
                            tuple(node_group_os or ()),
                            option_name="--node-group-os",
                        ),
                        gpu_overrides=cli._parse_soperator_node_group_overrides(
                            tuple(node_group_gpu_stack_preset or ()),
                            option_name="--node-group-gpu-stack-preset",
                        ),
                        compatibility_lookup=executor.compatibility_choices,
                    )
                infrastructure_authority = cli.build_soperator_infrastructure_authority(
                    target_ref=target.target_ref,
                    source_target=target_row,
                    generated_target=selected_target,
                    managed_component_instance=(
                        target.target_ref if managed_source_component is not None else ""
                    ),
                    terraform_modules=terraform_modules,
                    cluster_id=cluster_id,
                    kubernetes_uid=kubernetes_uid,
                    node_group_ids=tuple(group.provider_id for group in node_groups),
                    registration_sha256=(
                        "sha256:"
                        + cli.soperator_registration_fingerprint(
                            source_payload,
                            target_ref=target.target_ref,
                        )
                        if is_onboarded
                        else ""
                    ),
                    provider_api_authorized=bool(
                        (execute or assert_parent_fence is not None) and is_onboarded
                    ),
                    require_mutation_authorization=execute,
                )
                checks_proposal = cli.freeze_checks_proposal(
                    cli._source_helm_chart_row(source_payload, target).get("values") or {}
                )
                previous_protection = freeze_jail_protection(
                    cli._source_helm_chart_row(source_payload, target).get("values") or {}
                )
                if desired_jail_protection is not None:
                    chart = cli._source_helm_chart_row(source_payload, target)
                    desired_values = apply_frozen_jail_protection(
                        chart.get("values") or {}, desired_jail_protection
                    )
                    assert_protection_extension(chart.get("values") or {}, desired_values)
                    chart["values"] = desired_values
                cli._configure_soperator_upgrade_persistent_paths(
                    source_payload=source_payload,
                    target=target,
                    ownership=infrastructure_authority.ownership,
                    interactive=interactive,
                    validate_values=lambda values: observe_protected_directories(
                        cli, values, kube_context=kube_context, extra_env=kube_env
                    ),
                )
                protection = freeze_jail_protection(
                    cli._source_helm_chart_row(source_payload, target)["values"]
                )
                chart = cli._source_helm_chart_row(source_payload, target)
                admission_values, _ = cli.apply_checks_proposal(
                    chart.get("values") or {}, checks_proposal
                )
                frozen_release = cli.freeze_soperator_release(
                    release_selector,
                    current_release=live_release,
                    source=verified_source,
                    request=cli.SoperatorArtifactRequest.deployment(
                        target.target_ref,
                        admission_values,
                        payload=source_payload,
                        post_render_patches=tuple(chart.get("post_render_patches") or ()),
                    ),
                )
                intent = cli.build_campaign_intent(
                    jail_protection=protection,
                    jail_protection_changed=protection != previous_protection,
                    target_ref=target.target_ref,
                    ownership=infrastructure_authority.ownership,
                    backend=infrastructure_authority.backend,
                    backend_authority_sha256=infrastructure_authority.digest,
                    provider_api_authorized=(infrastructure_authority.provider_api_authorized),
                    source_config_sha256=loaded_source_config_sha256,
                    source_project_snapshot_sha256=cli.project_generation_snapshot_sha256(paths),
                    cluster_id=cluster_id,
                    kubernetes_uid=kubernetes_uid,
                    requested_release_selector=release_selector,
                    source_release=live_release,
                    target_release=frozen_release.snapshot.release,
                    target_jail_cuda_version=frozen_release.snapshot.jail_cuda_version,
                    requested_kubernetes_selector=requested_kubernetes_selector,
                    source_kubernetes_version=current_kubernetes_version,
                    supported_kubernetes_versions=supported_versions,
                    target_os=os_selector,
                    target_gpu_stack_preset=gpu_selector,
                    node_group_strategy=policy,
                    strategy_max_surge_count=resolved_max_surge,
                    drain_timeout=requested_drain_timeout,
                    zero_size_gpu_validation=zero_size_policy,
                    job_policy=resolved_job_policy,
                    cancel_job_ids=cancel_job_ids,
                    requeue_job_ids=requeue_job_ids,
                    job_wait_timeout=job_wait_timeout,
                    job_refresh_interval=job_refresh_interval,
                    node_groups=node_groups,
                    checks_policy_proposal=checks_proposal,
                    checks_release_snapshot_sha256=frozen_release.snapshot.snapshot_sha256,
                    compatibility_rows=compatibility_rows,
                    deployment=deployment_hooks.contract if deployment_hooks is not None else None,
                )

            if intent is None:
                raise RuntimeError("Soperator upgrade campaign intent was not resolved")
            if intent.jail_protection:
                chart = cli._source_helm_chart_row(source_payload, target)
                chart["values"] = apply_frozen_jail_protection(
                    chart.get("values") or {}, intent.jail_protection
                )
                protected = chart["values"].get("jailPersistentMounts", [])
                cli.console.print(
                    "Protected jail folders: " + ", ".join(row["mountPath"] for row in protected)
                )
                retained = chart["values"]["jailRootfs"].get("retainedGenerations", [])
                if retained:
                    cli.console.print(
                        f"Retained rootFS generations: {len(retained)}; their storage will not be reclaimed automatically. "
                        "Reusing their logical slots requires fresh rootFS backing on the same filesystem."
                    )
            cli.assert_campaign_phase_inventory(
                intent,
                campaign_receipt,
                tuple(
                    cli._non_empty_text(getattr(getattr(group, "metadata", None), "id", None))
                    for group in raw_node_groups
                ),
                newly_owned_ids=deployment_hooks.newly_owned_ids()
                if deployment_hooks is not None
                else None,
            )
            cli._print_upgrade_plan_lines(
                cli._format_soperator_full_stack_campaign_plan(
                    intent,
                    dry_run=dry_run,
                    live_node_groups=raw_node_groups,
                )
            )
            if dry_run or recovery_intent is None:
                cli._run_common_soperator_release_upgrade(
                    config_path=config_path,
                    source_payload=cli.copy.deepcopy(source_payload),
                    target=target,
                    ownership=intent.ownership,
                    target_selector=intent.target_release,
                    target_snapshot_sha256=intent.checks_release_snapshot_sha256,
                    checks_policy_proposal=intent.checks_policy_proposal,
                    jail_protection=intent.jail_protection,
                    external_scheduling_evidence={
                        "mode": "parent-campaign",
                        "requiresFreshChecks": intent.requires_fresh_checks,
                    },
                    dry_run=True,
                    interactive=False,
                    job_policy=intent.job_policy,
                    cancel_job_ids=intent.cancel_job_ids,
                    requeue_job_ids=intent.requeue_job_ids,
                    job_wait_timeout=intent.job_wait_timeout,
                    job_refresh_interval=intent.job_refresh_interval,
                    upgrade_progress=upgrade_progress,
                )
                if dry_run:
                    if cli._sha256_file(config_path) != loaded_source_config_sha256:
                        raise RuntimeError(
                            "soperator upgrade config changed during target admission; "
                            "rerun from the current config"
                        )
                    return intent
            with cli.ExitStack() as campaign_stack:
                with upgrade_progress.phase(
                    "operation-authority",
                    "Acquiring exclusive campaign authority and proving prior writers quiescent",
                    success="Exclusive campaign authority acquired",
                ) as authority_progress:
                    campaign_stack.enter_context(
                        cli.SoperatorOperationLocalLock(
                            paths.project_dir / ".nebius-cxcli" / "config.lock"
                        )
                    )
                    if cli._sha256_file(config_path) != loaded_source_config_sha256:
                        raise RuntimeError(
                            "soperator upgrade config changed before the operation lock was "
                            "acquired; rerun to avoid overwriting newer configuration"
                        )
                    campaign_lease = campaign_stack.enter_context(
                        cli.SoperatorOperationLease(
                            kube_context=kube_context,
                            cluster_id=cluster_id,
                            operation_fingerprint=intent.digest,
                            extra_env=kube_env,
                            emit=authority_progress.milestone,
                        )
                    )
                namespace = cli._soperator_upgrade_live_slurmcluster_namespaces(extra_env=kube_env)[
                    0
                ]
                wait_timeout_seconds = cli._soperator_upgrade_duration_seconds(
                    intent.job_wait_timeout,
                    option_name="--job-wait-timeout",
                )
                refresh_interval_seconds = cli._soperator_upgrade_duration_seconds(
                    intent.job_refresh_interval,
                    option_name="--job-refresh-interval",
                )

                def _campaign_job_control_record(job_id: str) -> cli.SlurmJobControlRecord | None:
                    selected = cli._non_empty_text(job_id)
                    if not selected:
                        raise RuntimeError("Slurm job identity is empty")
                    result = cli._run_soperator_upgrade_login_command(
                        namespace,
                        "scontrol show job " + cli.shlex.quote(selected) + " -o",
                        kube_context=kube_context,
                        extra_env=kube_env,
                        timeout_seconds=120,
                        check=False,
                    )
                    return cli.slurm_job_control_record_from_query(
                        requested_job_id=selected,
                        returncode=result.returncode,
                        stdout=result.stdout,
                        stderr=result.stderr,
                    )

                campaign_config_store = cli.CampaignConfigTransitionStore(
                    path=receipt_path,
                    intent=intent,
                )
                campaign_controller_spool_store = cli.CampaignControllerSpoolMigrationStore(
                    path=receipt_path,
                    intent=intent,
                )
                from .soperator_full_stack_upgrade import CampaignNativeTransitionStore

                campaign_native_store = CampaignNativeTransitionStore(
                    path=receipt_path, intent=intent
                )

                def _assert_campaign_authority() -> cli.SoperatorLeaseAuthority:
                    if assert_parent_fence is None:
                        raise RuntimeError("Campaign deployment fence is missing")
                    assert_parent_fence()
                    cli.checkpoint_execution()
                    lease_authority = campaign_lease.assert_held()
                    receipt = cli.load_campaign_receipt(receipt_path)
                    cli.assert_campaign_phase_inventory(
                        intent,
                        receipt,
                        tuple(
                            cli._non_empty_text(
                                getattr(getattr(group, "metadata", None), "id", None)
                            )
                            for group in executor.list_node_groups(cluster_id)
                        ),
                        newly_owned_ids=deployment_hooks.newly_owned_ids()
                        if deployment_hooks is not None
                        else None,
                    )
                    if receipt is None or receipt.intent_sha256 != intent.digest:
                        raise cli.SoperatorSafetyPauseError(
                            "the Soperator campaign receipt authority is unavailable"
                        )
                    cli.assert_config_authority_current(
                        receipt.config_generations,
                        initial_config_sha256=intent.source_config_sha256,
                        initial_project_snapshot_sha256=(intent.source_project_snapshot_sha256),
                        current_config_sha256=cli._sha256_file(config_path),
                        current_project_snapshot_sha256=cli.project_generation_snapshot_sha256(
                            paths
                        ),
                        current_project_generation_sha256=cli.ProjectBundleTransaction(
                            paths.project_dir
                        ).current_generation_sha256(),
                    )
                    current_payload = cli._load_source_payload(config_path)
                    current_target = cli.soperator_registration_target(
                        current_payload,
                        target_ref=intent.target_ref,
                    )
                    if intent.ownership == "onboarded":
                        if (
                            not isinstance(current_target, cli.Mapping)
                            or cli._non_empty_text(current_target.get("kind")).lower()
                            != "external-mk8s"
                            or cli._non_empty_text(current_target.get("ownership")).lower()
                            != "external"
                            or "sha256:"
                            + cli.soperator_registration_fingerprint(
                                current_payload,
                                target_ref=intent.target_ref,
                            )
                            != infrastructure_authority.registration_sha256
                        ):
                            raise cli.SoperatorSafetyPauseError(
                                "onboarded Soperator registration authority changed"
                            )
                    else:
                        try:
                            cli.find_source_mk8s_component(current_payload, intent.target_ref)
                        except ValueError as exc:
                            raise cli.SoperatorSafetyPauseError(
                                "managed Soperator Terraform ownership changed"
                            ) from exc
                    return lease_authority

                campaign_checks: cli.SoperatorCampaignChecks | None = None

                def _campaign_checks() -> cli.SoperatorCampaignChecks:
                    nonlocal campaign_checks
                    if campaign_checks is not None:
                        return campaign_checks
                    frozen = cli.freeze_soperator_release(
                        intent.target_release,
                        snapshot_sha256=intent.checks_release_snapshot_sha256,
                        target_ref=intent.target_ref,
                    )
                    if frozen.snapshot.snapshot_sha256 != intent.checks_release_snapshot_sha256:
                        raise RuntimeError(
                            "campaign checks release snapshot changed after planning"
                        )
                    target_paths = cli._paths_for_target_flux_dir(paths, selected_target)

                    def _kubernetes(
                        args: list[str], document: cli.Mapping[str, cli.Any] | None
                    ) -> cli.Mapping[str, cli.Any]:
                        result = cli._run_soperator_upgrade_process(
                            ["kubectl", "--context", kube_context, *args],
                            input_text=cli.json.dumps(document) if document is not None else None,
                            extra_env=kube_env,
                            timeout_seconds=120,
                            check=True,
                        )
                        payload = cli._soperator_checks_kubernetes_payload(args, result.stdout)
                        if not isinstance(payload, cli.Mapping):
                            raise RuntimeError("invalid campaign checks Kubernetes evidence")
                        return payload

                    def _slurm(command: str) -> str:
                        return cli._run_soperator_upgrade_login_command(
                            namespace,
                            command,
                            kube_context=kube_context,
                            extra_env=kube_env,
                            timeout_seconds=120,
                        ).stdout

                    def _load_target() -> cli.Any:
                        snapshot = cli.load_soperator_release_snapshot(
                            cli.soperator_release_snapshot_path(
                                paths.reports_dir, intent.target_ref
                            )
                        )
                        if snapshot.snapshot_sha256 != frozen.snapshot.snapshot_sha256:
                            raise RuntimeError("campaign checks target release identity changed")
                        source = cli.ensure_soperator_release_source(snapshot)
                        values = cli._rendered_soperator_upstream_values(target_paths.flux_dir)
                        policy = cli.compile_checks_policy(cli.Path(source.source_dir), values)
                        workers = cli._soperator_upgrade_expected_static_slurm_nodes(values)
                        gpu_workers = cli._soperator_upgrade_expected_static_slurm_nodes(
                            {
                                "nodesets": {
                                    "overrideValues": {
                                        "nodesets": [
                                            row
                                            for row in values.get("nodesets", {})
                                            .get("overrideValues", {})
                                            .get("nodesets", [])
                                            if row.get("gpu", {}).get("enabled") is True
                                        ]
                                    }
                                }
                            }
                        )
                        return policy, workers, gpu_workers

                    main_workload_authority = cli.CampaignMainWorkloadAuthority(
                        path=receipt_path,
                        intent=intent,
                        assert_authority=_assert_campaign_authority,
                    )

                    def _native_checks_transition() -> cli.Any:
                        from .soperator_graph_transition import prepare_transition

                        _assert_campaign_authority()
                        return prepare_transition(
                            target_paths,
                            target={
                                "targetRef": intent.target_ref,
                                "clusterId": cluster_id,
                                "kubernetesUid": kubernetes_uid,
                            },
                            run=lambda args, **kwargs: cli._run_soperator_upgrade_process(
                                args, extra_env=kube_env, timeout_seconds=120, check=True, **kwargs
                            ),
                            stored=campaign_native_store.read(),
                            persist=campaign_native_store.write,
                            authority=_assert_campaign_authority,
                            snapshot=frozen.snapshot,
                            source_dir=cli.Path(frozen.source.source_dir),
                        )

                    def _apply_target(policy: cli.Any, context: cli.ChecksPhaseContext) -> None:
                        _assert_campaign_authority()
                        cli.apply_staged_soperator_release(
                            target_paths,
                            extra_env=kube_env,
                            checks_policy=policy,
                            checks_context=context,
                            native_transition=_native_checks_transition(),
                            freeze_main_workload_authority=main_workload_authority.freeze,
                            on_stage_progress=lambda current, total, names: cli.console.print(
                                f"Check policy stage {current}/{total}: " + ", ".join(names),
                                markup=False,
                            ),
                        )

                    def _recover_target_checks(target: cli.SoperatorChecksExecution) -> object:
                        from .soperator_checks_catchup_flux import recover_staged_checks

                        return recover_staged_checks(
                            target,
                            paths=target_paths,
                            source_dir=cli.Path(frozen.source.source_dir),
                            kube_context=kube_context,
                            extra_env=kube_env,
                            native_transition=_native_checks_transition(),
                            freeze_main_workload_authority=main_workload_authority.freeze,
                            on_stage_progress=lambda current, total, names: cli.console.print(
                                f"Recovering check policy stage {current}/{total}: "
                                + ", ".join(names),
                                markup=False,
                            ),
                        )

                    def _check_partition_preimages() -> tuple[cli.SlurmPartitionPauseRecord, ...]:
                        receipt = cli.load_campaign_receipt(receipt_path)
                        if receipt is None or receipt.intent_sha256 != intent.digest:
                            raise RuntimeError("campaign checks maintenance receipt disappeared")
                        snapshots = [
                            row["partitions"]
                            for row in receipt.maintenance_evidence.get("events", ())
                            if row.get("action") == "partition-preimage"
                        ]
                        if len(snapshots) != 1:
                            raise RuntimeError("campaign requires one complete partition preimage")
                        return cli.diagnostic_partition_preimages(snapshots[0])

                    reservation_name = cli._soperator_upgrade_maintenance_reservation_name(
                        intent.digest.removeprefix("sha256:")[:16]
                    )

                    def _checks_release_events() -> tuple[cli.Mapping[str, cli.Any], ...]:
                        receipt = cli.load_campaign_receipt(receipt_path)
                        if receipt is None or receipt.intent_sha256 != intent.digest:
                            raise RuntimeError("campaign checks release receipt changed")
                        summary = receipt.maintenance_evidence.get("summary", {})
                        if (
                            summary.get("namespace") != namespace
                            or summary.get("reservationName") != reservation_name
                        ):
                            raise RuntimeError("campaign checks release reservation scope changed")
                        return tuple(receipt.maintenance_evidence.get("events", ()))

                    def _observe_checks_release() -> cli.Mapping[str, cli.Any]:
                        policy, _workers, _gpu_workers = _load_target()
                        return (
                            _campaign_checks()
                            ._execution(policy, "target")
                            ._reservation(reservation_name)
                        )

                    checks_release = cli.CampaignChecksRelease(
                        owner=intent.digest,
                        reservation=reservation_name,
                        load_events=_checks_release_events,
                        record_event=lambda event: cli.record_campaign_maintenance_event(
                            path=receipt_path, intent=intent, event=event
                        ),
                        present=lambda: (
                            reservation_name
                            in cli._soperator_upgrade_reservation_names(
                                namespace=namespace, extra_env=kube_env
                            )
                        ),
                        observe=_observe_checks_release,
                        delete=lambda: cli._soperator_upgrade_delete_maintenance_reservation(
                            namespace=namespace,
                            reservation_name=reservation_name,
                            extra_env=kube_env,
                        ),
                        read_job=_campaign_job_control_record,
                        authority=_assert_campaign_authority,
                    )
                    campaign_checks = cli.SoperatorCampaignChecks(
                        operation_id=intent.digest,
                        reports_dir=paths.reports_dir,
                        cluster_name=str(
                            cli._rendered_soperator_upstream_values(target_paths.flux_dir)[
                                "slurmCluster"
                            ]["overrideValues"]["clusterName"]
                        ),
                        source_dir=cli.Path(
                            cli.resolve_soperator_source(intent.source_release).source.source_dir
                        ),
                        kubernetes=_kubernetes,
                        slurm=_slurm,
                        assert_authority=_assert_campaign_authority,
                        load_target=_load_target,
                        apply_target=_apply_target,
                        partition_preimages=_check_partition_preimages,
                        target_writers=lambda: cli._soperator_checks_target_writers(
                            target_paths.flux_dir
                        ),
                        reservation=reservation_name,
                        release_maintenance=checks_release.release,
                        verify_maintenance_released=checks_release.verify,
                        recover_target=_recover_target_checks,
                        emit=lambda message: cli.console.print(message, markup=False),
                    )
                    return campaign_checks

                def _enter_maintenance_impl(
                    record_event: cli.Callable[[cli.Mapping[str, cli.Any]], None],
                    existing_evidence: cli.Mapping[str, cli.Any],
                    *,
                    emit: cli.Callable[[str], None],
                ) -> cli.Mapping[str, cli.Any]:
                    from .soperator_graph_transition import (
                        campaign_transition_checkpoint,
                        prepare_generation_transition,
                        prepare_transition,
                    )

                    frozen = cli.freeze_soperator_release(
                        intent.target_release,
                        snapshot_sha256=intent.checks_release_snapshot_sha256,
                        target_ref=intent.target_ref,
                    )
                    witnessed = [
                        event.get("transition")
                        for event in existing_evidence.get("events", [])
                        if event.get("action") == "native-graph-admitted"
                    ]
                    if len(witnessed) > 1:
                        raise RuntimeError("Campaign native admission has conflicting witnesses")
                    arguments: dict[str, Any] = {
                        "target": {
                            "targetRef": intent.target_ref,
                            "clusterId": cluster_id,
                            "kubernetesUid": kubernetes_uid,
                        },
                        "run": lambda args, **kwargs: cli._run_soperator_upgrade_process(
                            args, extra_env=kube_env, timeout_seconds=120, check=True, **kwargs
                        ),
                        "stored": campaign_transition_checkpoint(existing_evidence),
                        "persist": lambda _value: None,
                        "authority": _assert_campaign_authority,
                        "snapshot": frozen.snapshot,
                        "source_dir": cli.Path(frozen.source.source_dir),
                    }
                    native_admission = (
                        prepare_generation_transition(
                            deployment_hooks.generation, intent.target_ref, **arguments
                        )
                        if deployment_hooks is not None
                        else prepare_transition(
                            cli._paths_for_target_flux_dir(paths, selected_target), **arguments
                        )
                    )
                    if native_admission is not None and not witnessed:
                        _assert_campaign_authority()
                        record_event(
                            {
                                "action": "native-graph-admitted",
                                "transition": native_admission.state,
                            }
                        )
                        existing_evidence = {
                            **existing_evidence,
                            "events": [
                                *existing_evidence.get("events", []),
                                {
                                    "action": "native-graph-admitted",
                                    "transition": native_admission.state,
                                },
                            ],
                        }
                    raw_existing_events = existing_evidence.get("events", ())
                    events = (
                        list(raw_existing_events)
                        if isinstance(raw_existing_events, cli.Sequence)
                        and not isinstance(raw_existing_events, (str, bytes))
                        else []
                    )
                    if not all(isinstance(event, cli.Mapping) for event in events):
                        raise RuntimeError(
                            "recovery-required: campaign maintenance events are invalid"
                        )

                    def _record_entry(event: cli.Mapping[str, cli.Any]) -> None:
                        record_event(event)
                        events.append(dict(event))

                    if intent.requires_fresh_checks:
                        if not any(event.get("action") == "partition-preimage" for event in events):
                            if any(
                                event.get("action") != "native-graph-admitted" for event in events
                            ):
                                raise RuntimeError(
                                    "campaign admission has no original complete partition preimage"
                                )
                            _record_entry(
                                {
                                    "action": "partition-preimage",
                                    "partitions": [
                                        cli.asdict(row)
                                        for row in cli._soperator_upgrade_partition_state_snapshot(
                                            namespace=namespace,
                                            kube_context=kube_context,
                                            extra_env=kube_env,
                                        )
                                    ],
                                }
                            )
                        _campaign_checks().enter(_record_entry)

                    existing_records = cli._soperator_maintenance_pause_records(
                        tuple(event for event in events if isinstance(event, cli.Mapping))
                    )
                    if existing_records:
                        emit(
                            "Revalidating the durable pause for "
                            f"{len(existing_records)} Slurm partitions"
                        )
                    reservation_name = cli._soperator_upgrade_maintenance_reservation_name(
                        intent.digest.removeprefix("sha256:")[:16]
                    )
                    for record in existing_records:
                        live = cli._soperator_upgrade_partition_state(
                            namespace=namespace,
                            partition=record.partition,
                            kube_context=kube_context,
                            extra_env=kube_env,
                        )
                        if cli._soperator_upgrade_partition_observation_matches(
                            live,
                            record=record.previous_record,
                            fingerprint=record.previous_record_fingerprint,
                        ):
                            continue
                        if (
                            record.applied_record
                            and cli._soperator_upgrade_partition_observation_matches(
                                live,
                                record=record.applied_record,
                                fingerprint=record.applied_record_fingerprint,
                            )
                        ):
                            continue
                        try:
                            recovered_record = record.with_applied_observation(live)
                        except ValueError as exc:
                            raise RuntimeError(
                                "recovery-required: campaign maintenance cannot prove the "
                                f"partial Slurm pause for partition {record.partition!r}"
                            ) from exc
                        _record_entry(
                            {
                                "at": cli.datetime.now(cli.UTC)
                                .isoformat(timespec="seconds")
                                .replace("+00:00", "Z"),
                                "action": "scheduling-pause-applied",
                                "partitions": [recovered_record.as_payload()],
                                "scope": "all-active",
                                "recovered": True,
                            }
                        )

                    applied_hold_identities: set[str] = set()
                    for event in events:
                        if cli._non_empty_text(event.get("action")) not in {
                            "requeue-hold-applied",
                            "requeue-hold-selected-applied",
                            "requeue-hold-all-applied",
                        }:
                            continue
                        payloads = event.get("job_control_postimages")
                        if not isinstance(payloads, cli.Sequence) or isinstance(
                            payloads, (str, bytes)
                        ):
                            continue
                        for payload in payloads:
                            if isinstance(payload, cli.Mapping):
                                applied_hold_identities.add(
                                    cli._non_empty_text(payload.get("identity_sha256"))
                                )
                    recovered_intents: set[str] = set()
                    for event in tuple(events):
                        action = cli._non_empty_text(event.get("action"))
                        if action not in {
                            "requeue-hold",
                            "requeue-hold-selected",
                            "requeue-hold-all",
                        }:
                            continue
                        payloads = event.get("job_control_preimages")
                        if not isinstance(payloads, cli.Sequence) or isinstance(
                            payloads, (str, bytes)
                        ):
                            continue
                        for payload in payloads:
                            if not isinstance(payload, cli.Mapping):
                                raise RuntimeError(
                                    "recovery-required: unfinished Slurm hold intent is invalid"
                                )
                            try:
                                preimage = cli.slurm_job_control_record_from_payload(payload)
                            except ValueError as exc:
                                raise RuntimeError(
                                    "recovery-required: unfinished Slurm hold identity is invalid"
                                ) from exc
                            identity = preimage.identity_sha256
                            if identity in applied_hold_identities or identity in recovered_intents:
                                continue
                            recovered_intents.add(identity)
                            live_job = _campaign_job_control_record(preimage.job_id)
                            if live_job is None:
                                _record_entry(
                                    {
                                        "at": cli.datetime.now(cli.UTC)
                                        .isoformat(timespec="seconds")
                                        .replace("+00:00", "Z"),
                                        "action": f"{action}-tombstone",
                                        "job_ids": [preimage.job_id],
                                        "reason": "job-disappeared-after-write-ahead-intent",
                                        "identity_sha256": identity,
                                    }
                                )
                                continue
                            if live_job.identity_sha256 != identity:
                                raise RuntimeError(
                                    "recovery-required: Slurm job identity changed after an "
                                    f"unfinished hold intent: {preimage.job_id}"
                                )
                            if not cli.slurm_job_control_is_held(live_job):
                                eligible, reason = cli.slurm_requeuehold_eligibility(live_job)
                                if not eligible:
                                    _record_entry(
                                        {
                                            "at": cli.datetime.now(cli.UTC)
                                            .isoformat(timespec="seconds")
                                            .replace("+00:00", "Z"),
                                            "action": f"{action}-wait-only",
                                            "job_ids": [preimage.job_id],
                                            "job_control_preimages": [live_job.as_payload()],
                                            "reason": reason,
                                            "recovered": True,
                                        }
                                    )
                                    continue
                                _assert_campaign_authority()
                                cli._soperator_upgrade_requeue_jobs(
                                    namespace,
                                    (preimage.job_id,),
                                    hold=True,
                                    kube_context=kube_context,
                                    extra_env=kube_env,
                                )
                                cli._soperator_upgrade_wait_for_requeued_jobs_to_leave_nodes(
                                    namespace=namespace,
                                    node_names=(),
                                    job_ids=(preimage.job_id,),
                                    timeout_seconds=wait_timeout_seconds,
                                    refresh_interval_seconds=refresh_interval_seconds,
                                    kube_context=kube_context,
                                    extra_env=kube_env,
                                    include_pending=False,
                                    all_jobs=True,
                                )
                                live_job = _campaign_job_control_record(preimage.job_id)
                            if (
                                live_job is None
                                or live_job.identity_sha256 != identity
                                or not cli.slurm_job_control_is_held(live_job)
                            ):
                                raise RuntimeError(
                                    "recovery-required: unfinished Slurm hold intent did not "
                                    f"converge for job {preimage.job_id}"
                                )
                            _record_entry(
                                {
                                    "at": cli.datetime.now(cli.UTC)
                                    .isoformat(timespec="seconds")
                                    .replace("+00:00", "Z"),
                                    "action": f"{action}-applied",
                                    "job_ids": [preimage.job_id],
                                    "job_control_postimages": [live_job.as_payload()],
                                    "recovered": True,
                                }
                            )
                    emit("Pausing active Slurm partitions and securing active jobs")
                    cli._handle_soperator_upgrade_running_jobs(
                        namespace=namespace,
                        policy=intent.job_policy,
                        cancel_job_ids=intent.cancel_job_ids,
                        requeue_job_ids=intent.requeue_job_ids,
                        wait_timeout_seconds=wait_timeout_seconds,
                        refresh_interval_seconds=refresh_interval_seconds,
                        checkpoint_id=intent.digest.removeprefix("sha256:")[:16],
                        kube_context=kube_context,
                        extra_env=kube_env,
                        drain_nodes=False,
                        slurm_scheduling_pause=True,
                        decision_recorder=_record_entry,
                        mutation_guard=_assert_campaign_authority,
                        all_active_partitions=True,
                        continue_until_clear=True,
                        job_control_reader=_campaign_job_control_record,
                    )
                    emit("Creating and verifying the operation-owned maintenance reservation")
                    live_reservations = cli._soperator_upgrade_reservation_names(
                        namespace=namespace,
                        extra_env=kube_env,
                    )
                    reservation_action = cli.maintenance_reservation_recovery_action(
                        events=tuple(event for event in events if isinstance(event, cli.Mapping)),
                        live_reservations=live_reservations,
                        reservation_name=reservation_name,
                        owner="Soperator full-stack campaign",
                    )
                    if reservation_action == "record-and-create":
                        _record_entry(
                            {
                                "at": cli.datetime.now(cli.UTC)
                                .isoformat(timespec="seconds")
                                .replace("+00:00", "Z"),
                                "action": "maintenance-reservation-intent",
                                "reservation_name": reservation_name,
                                "node_scope": "ALL",
                            }
                        )
                    if reservation_action in {"record-and-create", "create"}:
                        _assert_campaign_authority()
                        cli._soperator_upgrade_create_maintenance_reservation(
                            namespace=namespace,
                            reservation_name=reservation_name,
                            extra_env=kube_env,
                        )
                    live_reservations_after = cli._soperator_upgrade_reservation_names(
                        namespace=namespace,
                        extra_env=kube_env,
                    )
                    if reservation_name not in live_reservations_after:
                        raise RuntimeError(
                            "recovery-required: Soperator maintenance reservation did not converge"
                        )
                    if not any(
                        cli._non_empty_text(event.get("action"))
                        == "maintenance-reservation-applied"
                        and cli._non_empty_text(event.get("reservation_name")) == reservation_name
                        for event in events
                    ):
                        _record_entry(
                            {
                                "at": cli.datetime.now(cli.UTC)
                                .isoformat(timespec="seconds")
                                .replace("+00:00", "Z"),
                                "action": "maintenance-reservation-applied",
                                "reservation_name": reservation_name,
                                "node_scope": "ALL",
                                "recovered": reservation_name in live_reservations,
                            }
                        )

                    convergence_pass = 0
                    while True:
                        convergence_pass += 1
                        emit(f"Verifying the Slurm maintenance barrier (pass {convergence_pass})")
                        cli._handle_soperator_upgrade_running_jobs(
                            namespace=namespace,
                            policy=intent.job_policy,
                            cancel_job_ids=intent.cancel_job_ids,
                            requeue_job_ids=intent.requeue_job_ids,
                            wait_timeout_seconds=wait_timeout_seconds,
                            refresh_interval_seconds=refresh_interval_seconds,
                            checkpoint_id=intent.digest.removeprefix("sha256:")[:16],
                            kube_context=kube_context,
                            extra_env=kube_env,
                            drain_nodes=False,
                            slurm_scheduling_pause=True,
                            decision_recorder=_record_entry,
                            mutation_guard=_assert_campaign_authority,
                            all_active_partitions=True,
                            continue_until_clear=True,
                            job_control_reader=_campaign_job_control_record,
                        )
                        remaining_up = tuple(
                            state.name
                            for state in cli._soperator_upgrade_partition_state_snapshot(
                                namespace=namespace,
                                kube_context=kube_context,
                                extra_env=kube_env,
                            )
                            if cli.slurm_partition_state_token(state.state) == "UP"
                        )
                        remaining_jobs = cli._soperator_upgrade_affected_jobs(
                            namespace=namespace,
                            node_names=(),
                            kube_context=kube_context,
                            extra_env=kube_env,
                            include_pending=False,
                            all_jobs=True,
                        )
                        reservation_present = reservation_name in (
                            cli._soperator_upgrade_reservation_names(
                                namespace=namespace,
                                extra_env=kube_env,
                            )
                        )
                        if not reservation_present:
                            raise RuntimeError(
                                "recovery-required: the operation-owned maintenance reservation "
                                "disappeared during barrier convergence"
                            )
                        if not remaining_up and not remaining_jobs and reservation_present:
                            _record_entry(
                                {
                                    "at": cli.datetime.now(cli.UTC)
                                    .isoformat(timespec="seconds")
                                    .replace("+00:00", "Z"),
                                    "action": "maintenance-barrier-converged",
                                    "pass": convergence_pass,
                                    "reservation_name": reservation_name,
                                }
                            )
                            break
                        _record_entry(
                            {
                                "at": cli.datetime.now(cli.UTC)
                                .isoformat(timespec="seconds")
                                .replace("+00:00", "Z"),
                                "action": "maintenance-barrier-retrying",
                                "pass": convergence_pass,
                                "remaining_up_partitions": list(remaining_up),
                                "remaining_job_ids": [job.job_id for job in remaining_jobs],
                                "reservation_present": reservation_present,
                            }
                        )
                    if intent.requires_fresh_checks:
                        _campaign_checks().pause_source_passive()
                    return {
                        "namespace": namespace,
                        "reservationName": reservation_name,
                        "nodeScope": "ALL",
                    }

                def _enter_maintenance(
                    record_event: cli.Callable[[cli.Mapping[str, cli.Any]], None],
                    existing_evidence: cli.Mapping[str, cli.Any],
                ) -> cli.Mapping[str, cli.Any]:
                    with upgrade_progress.phase(
                        "maintenance-entry",
                        "Entering durable Slurm maintenance for the full-stack campaign",
                        success="Durable Slurm maintenance barrier established",
                    ) as maintenance_progress:

                        def _maintenance_milestone(message: str) -> None:
                            key = (
                                "maintenance-barrier"
                                if message.startswith("Verifying the Slurm maintenance barrier")
                                else message
                            )
                            maintenance_progress.milestone(message, key=key)

                        job_prompt_pause_token = cli._SOPERATOR_UPGRADE_JOB_PROMPT_PAUSE.set(
                            maintenance_progress.paused
                        )
                        try:
                            return _enter_maintenance_impl(
                                record_event,
                                existing_evidence,
                                emit=_maintenance_milestone,
                            )
                        finally:
                            cli._SOPERATOR_UPGRADE_JOB_PROMPT_PAUSE.reset(job_prompt_pause_token)

                def _restore_maintenance(
                    record_event: cli.Callable[[cli.Mapping[str, cli.Any]], None],
                    evidence: cli.Mapping[str, cli.Any],
                ) -> cli.Mapping[str, cli.Any]:
                    summary = evidence.get("summary")
                    if not isinstance(summary, cli.Mapping):
                        raise RuntimeError(
                            "recovery-required: campaign maintenance summary is invalid"
                        )
                    restore_namespace = cli._non_empty_text(summary.get("namespace"))
                    if restore_namespace != namespace:
                        raise RuntimeError(
                            "recovery-required: campaign maintenance namespace changed"
                        )
                    raw_events = evidence.get("events", ())
                    events = (
                        list(raw_events)
                        if isinstance(raw_events, cli.Sequence)
                        and not isinstance(raw_events, (str, bytes))
                        else []
                    )
                    if not all(isinstance(event, cli.Mapping) for event in events):
                        raise RuntimeError(
                            "recovery-required: campaign maintenance events are invalid"
                        )

                    def _record(action: str, **details: cli.Any) -> None:
                        event = {
                            "at": cli.datetime.now(cli.UTC)
                            .isoformat(timespec="seconds")
                            .replace("+00:00", "Z"),
                            "action": action,
                            **details,
                        }
                        record_event(event)
                        events.append(event)

                    def _has_event(action: str, **matches: str) -> bool:
                        return any(
                            cli._non_empty_text(event.get("action")) == action
                            and all(
                                cli._non_empty_text(event.get(key)) == value
                                for key, value in matches.items()
                            )
                            for event in events
                            if isinstance(event, cli.Mapping)
                        )

                    records = cli._soperator_maintenance_pause_records(
                        tuple(event for event in events if isinstance(event, cli.Mapping))
                    )
                    held_records = cli.applied_slurm_held_job_records(
                        tuple(event for event in events if isinstance(event, cli.Mapping))
                    )

                    def _assert_partitions_still_paused() -> None:
                        if intent.requires_fresh_checks:
                            _campaign_checks().verify_barrier()
                            return
                        for record in records:
                            live = cli._soperator_upgrade_partition_state(
                                namespace=namespace,
                                partition=record.partition,
                                kube_context=kube_context,
                                extra_env=kube_env,
                            )
                            if not record.applied_record or not (
                                cli._soperator_upgrade_partition_migration_observation_matches(
                                    live,
                                    record=record.applied_record,
                                    fingerprint=record.applied_record_fingerprint,
                                )
                            ):
                                raise RuntimeError(
                                    "recovery-required: Slurm partitions must remain paused "
                                    "until every operation-owned job hold is restored"
                                )

                    reservation_name = cli._non_empty_text(summary.get("reservationName"))
                    if (
                        intent.requires_fresh_checks
                        and _has_event(
                            "maintenance-reservation-delete-intent",
                            reservation_name=reservation_name,
                        )
                        and not _has_event(
                            "maintenance-reservation-delete-applied",
                            reservation_name=reservation_name,
                        )
                        and reservation_name
                        not in cli._soperator_upgrade_reservation_names(
                            namespace=namespace, extra_env=kube_env
                        )
                    ):
                        # The delete may have succeeded before its receipt write. Never
                        # recreate a released barrier or require that absent barrier to
                        # prove the already-completed diagnostic/restoration evidence.
                        _campaign_checks().finalize(already_released=True)
                        _record(
                            "maintenance-reservation-delete-applied",
                            reservation_name=reservation_name,
                        )
                    if intent.requires_fresh_checks and not _has_event(
                        "maintenance-reservation-delete-applied",
                        reservation_name=reservation_name,
                    ):
                        _campaign_checks().verify_handoff()

                    if intent.requires_fresh_checks:
                        _campaign_checks().authorize_admission()
                    released_job_count = 0
                    tombstone_count = 0
                    for held_record in held_records:
                        identity = held_record.identity_sha256
                        job_id = held_record.job_id
                        release_applied = _has_event(
                            "maintenance-held-job-release-applied",
                            job_id=job_id,
                            identity_sha256=identity,
                        )
                        live = _campaign_job_control_record(job_id)
                        if live is not None and live.identity_sha256 != identity:
                            raise RuntimeError(
                                "recovery-required: a Slurm job ID was reused before "
                                f"maintenance restoration: {job_id}"
                            )
                        if release_applied:
                            if live is not None and cli.slurm_job_control_is_held(live):
                                raise RuntimeError(
                                    "recovery-required: a restored Slurm job was held again "
                                    f"outside the campaign: {job_id}"
                                )
                            released_job_count += 1
                            if live is None:
                                tombstone_count += 1
                            continue

                        _assert_partitions_still_paused()
                        if not _has_event(
                            "maintenance-held-job-release-intent",
                            job_id=job_id,
                            identity_sha256=identity,
                        ):
                            _record(
                                "maintenance-held-job-release-intent",
                                job_id=job_id,
                                identity_sha256=identity,
                                job_control_postimages=[held_record.as_payload()],
                            )
                        if live is None:
                            _record(
                                "maintenance-held-job-release-applied",
                                job_id=job_id,
                                identity_sha256=identity,
                                disposition="satisfied-external-tombstone",
                            )
                            released_job_count += 1
                            tombstone_count += 1
                            continue
                        if cli.slurm_job_control_is_held(live):
                            _assert_campaign_authority()
                            cli._soperator_upgrade_release_jobs(
                                namespace,
                                (job_id,),
                                kube_context=kube_context,
                                extra_env=kube_env,
                            )
                            after = _campaign_job_control_record(job_id)
                            if after is not None and after.identity_sha256 != identity:
                                raise RuntimeError(
                                    "recovery-required: Slurm job identity changed during "
                                    f"maintenance restoration: {job_id}"
                                )
                            if after is not None and cli.slurm_job_control_is_held(after):
                                raise RuntimeError(
                                    "recovery-required: Slurm did not release the exact "
                                    f"operation-owned job hold: {job_id}"
                                )
                            live = after
                            disposition = "released"
                        else:
                            disposition = "satisfied-external"
                        _record(
                            "maintenance-held-job-release-applied",
                            job_id=job_id,
                            identity_sha256=identity,
                            disposition=disposition,
                            job_control_postimages=(
                                [live.as_payload()] if live is not None else []
                            ),
                        )
                        released_job_count += 1
                        if live is None:
                            tombstone_count += 1

                    reservation_name = cli._non_empty_text(summary.get("reservationName"))
                    if not reservation_name:
                        raise RuntimeError(
                            "recovery-required: campaign maintenance reservation is missing"
                        )
                    reservation_deleted = _has_event(
                        "maintenance-reservation-delete-applied",
                        reservation_name=reservation_name,
                    )
                    live_reservations = cli._soperator_upgrade_reservation_names(
                        namespace=namespace,
                        extra_env=kube_env,
                    )
                    if reservation_deleted and reservation_name in live_reservations:
                        raise RuntimeError(
                            "recovery-required: the operation-owned maintenance reservation "
                            "was recreated after its durable deletion"
                        )
                    if not reservation_deleted:
                        if not _has_event(
                            "maintenance-reservation-delete-intent",
                            reservation_name=reservation_name,
                        ):
                            _record(
                                "maintenance-reservation-delete-intent",
                                reservation_name=reservation_name,
                            )
                        _assert_campaign_authority()
                        if intent.requires_fresh_checks:
                            _campaign_checks().verify_barrier()
                        cli._soperator_upgrade_delete_maintenance_reservation(
                            namespace=namespace,
                            reservation_name=reservation_name,
                            extra_env=kube_env,
                        )
                        _record(
                            "maintenance-reservation-delete-applied",
                            reservation_name=reservation_name,
                        )

                    if intent.requires_fresh_checks:
                        _campaign_checks().finish_admission()
                        restored_partition_count = len(records)
                    else:
                        restored_partition_count = 0
                        for record in records:
                            fingerprint = record.previous_record_fingerprint
                            restored = _has_event(
                                "maintenance-partition-restore-applied",
                                partition=record.partition,
                                previous_record_fingerprint=fingerprint,
                            )
                            if not restored:
                                if not _has_event(
                                    "maintenance-partition-restore-intent",
                                    partition=record.partition,
                                    previous_record_fingerprint=fingerprint,
                                ):
                                    _record(
                                        "maintenance-partition-restore-intent",
                                        partition=record.partition,
                                        previous_record_fingerprint=fingerprint,
                                    )
                                _assert_campaign_authority()
                                cli._soperator_upgrade_restore_slurm_partitions(
                                    namespace=namespace,
                                    records=(record,),
                                    kube_context=kube_context,
                                    extra_env=kube_env,
                                    allow_topology_migration=True,
                                )
                                _record(
                                    "maintenance-partition-restore-applied",
                                    partition=record.partition,
                                    previous_record_fingerprint=fingerprint,
                                )
                            live_partition = cli._soperator_upgrade_partition_state(
                                namespace=namespace,
                                partition=record.partition,
                                kube_context=kube_context,
                                extra_env=kube_env,
                            )
                            if not cli._soperator_upgrade_partition_migration_observation_matches(
                                live_partition,
                                record=record.previous_record,
                                fingerprint=record.previous_record_fingerprint,
                            ):
                                raise RuntimeError(
                                    "recovery-required: Slurm partition restoration postcondition "
                                    f"is not exact for {record.partition}"
                                )
                            restored_partition_count += 1

                    if reservation_name in cli._soperator_upgrade_reservation_names(
                        namespace=namespace,
                        extra_env=kube_env,
                    ):
                        raise RuntimeError(
                            "recovery-required: the operation-owned maintenance reservation "
                            "remains after restoration"
                        )
                    if deployment_hooks is not None:
                        deployment_hooks.verify_desired(
                            kube_env=kube_env,
                            documents_projection=_campaign_checks().documents_projection()
                            if intent.requires_fresh_checks
                            else None,
                        )
                    return {
                        "partitionCount": restored_partition_count,
                        "releasedHeldJobCount": released_job_count,
                        "heldJobTombstoneCount": tombstone_count,
                        "reservationDeleted": reservation_name,
                        "fastSlurmReadiness": _fast_campaign_smoke(),
                    }

                print_release_plan_once = cli.single_use_soperator_upgrade_plan_printer(
                    cli._print_upgrade_plan_lines
                )

                def _release_segment() -> cli.CampaignSegmentResult:
                    _assert_campaign_authority()
                    from .soperator_campaign_handoff import completed_release_handoff

                    handoff = completed_release_handoff(
                        cli,
                        paths=paths,
                        target=selected_target,
                        intent=intent,
                        config_store=campaign_config_store,
                    )
                    if handoff is None:
                        cli._run_common_soperator_release_upgrade(
                            config_path=config_path,
                            source_payload=cli._load_source_payload(config_path),
                            target=target,
                            ownership=intent.ownership,
                            target_selector=intent.target_release,
                            target_snapshot_sha256=intent.checks_release_snapshot_sha256,
                            checks_policy_proposal=intent.checks_policy_proposal,
                            jail_protection=intent.jail_protection,
                            dry_run=False,
                            interactive=False,
                            job_policy=intent.job_policy,
                            cancel_job_ids=intent.cancel_job_ids,
                            requeue_job_ids=intent.requeue_job_ids,
                            job_wait_timeout=intent.job_wait_timeout,
                            job_refresh_interval=intent.job_refresh_interval,
                            supervise=False,
                            external_scheduling_evidence={
                                "mode": "parent-campaign",
                                "campaignIntentSha256": intent.digest,
                                "maintenanceOwner": "soperator-upgrade",
                                "requiresFreshChecks": intent.requires_fresh_checks,
                                "reservationName": cli._soperator_upgrade_maintenance_reservation_name(
                                    intent.digest.removeprefix("sha256:")[:16]
                                ),
                            },
                            external_controller_spool_migration_store=(
                                campaign_controller_spool_store
                            ),
                            external_native_transition_store=campaign_native_store,
                            config_transition_store=campaign_config_store,
                            config_transition_owner="soperator-upgrade",
                            config_transition_stage="release-admission",
                            assert_parent_authority=_assert_campaign_authority,
                            upgrade_progress=upgrade_progress,
                            print_plan=print_release_plan_once,
                        )
                        _assert_campaign_authority()
                        handoff = completed_release_handoff(
                            cli,
                            paths=paths,
                            target=selected_target,
                            intent=intent,
                            config_store=campaign_config_store,
                        )
                    if handoff is None:
                        raise RuntimeError("Release child completed without a sealed jail handoff")
                    return cli.CampaignSegmentResult(
                        evidence={
                            "release": intent.target_release,
                            "jailCudaVersion": intent.target_jail_cuda_version,
                            "jailHandoff": handoff,
                        },
                        irreversible_frontier=f"soperator-release:{intent.target_release}",
                    )

                def _terraform_managed_stage(version: str) -> cli.Mapping[str, object]:
                    rows = {
                        row.group_key: row
                        for row in intent.compatibility_rows
                        if row.kubernetes_version == version
                    }

                    def _apply_group(
                        group: cli.FrozenNodeGroupTarget,
                        row: cli.FrozenCompatibilityRow,
                    ) -> None:
                        _assert_campaign_authority()
                        cli._run_node_template_upgrade_suboperation(
                            config_path=config_path,
                            target_selector=f"infra:mk8s@{intent.target_ref}",
                            to_version=version,
                            to_os=row.os,
                            to_gpu_stack_preset=row.drivers_preset,
                            node_group=group.key,
                            disruption_policy=intent.node_group_strategy,
                            drain_timeout=intent.drain_timeout,
                            strategy_max_surge_count=resolved_max_surge,
                            config_transition_store=campaign_config_store,
                            config_transition_owner="soperator-upgrade",
                            config_transition_prefix=f"node-group:{group.key}:template:{version}",
                        )

                    cli.apply_frozen_node_group_rows(
                        node_groups=intent.node_groups,
                        rows=rows,
                        compatibility_lookup=lambda group, _row: executor.compatibility_choices(
                            target_version=version,
                            platform=group.platform,
                        ),
                        apply_group=_apply_group,
                    )
                    return {"backend": "terraform", "kubernetesVersion": version}

                def _onboarded_provider_api_stage(version: str) -> cli.Mapping[str, object]:
                    rows = {
                        row.group_key: row
                        for row in intent.compatibility_rows
                        if row.kubernetes_version == version
                    }
                    _assert_campaign_authority()
                    executor.update_control_plane_version(
                        cluster_id=cluster_id,
                        version=version,
                    )
                    executor.wait_cluster_version(cluster_id=cluster_id, version=version)

                    def _apply_group(
                        group: cli.FrozenNodeGroupTarget,
                        row: cli.FrozenCompatibilityRow,
                    ) -> None:
                        _assert_campaign_authority()
                        executor.update_node_group_template(
                            cluster_id=cluster_id,
                            node_group_id=group.provider_id,
                            version=version,
                            os=row.os,
                            drivers_preset=(row.drivers_preset if group.gpu else None),
                            strategy_policy=intent.node_group_strategy,
                            strategy_max_surge_count=resolved_max_surge,
                            drain_timeout=resolved_drain_timeout,
                        )

                    cli.apply_frozen_node_group_rows(
                        node_groups=intent.node_groups,
                        rows=rows,
                        compatibility_lookup=lambda group, _row: executor.compatibility_choices(
                            target_version=version,
                            platform=group.platform,
                        ),
                        apply_group=_apply_group,
                    )
                    return {"backend": "provider-api", "kubernetesVersion": version}

                infrastructure_backend = (
                    cli.TerraformManagedUpgradeBackend(
                        authority=infrastructure_authority,
                        apply_stage=_terraform_managed_stage,
                    )
                    if intent.backend == "terraform"
                    else cli.OnboardedProviderApiUpgradeBackend(
                        authority=infrastructure_authority,
                        apply_stage=_onboarded_provider_api_stage,
                    )
                )

                def _assert_frozen_provider_compatibility(version: str) -> None:
                    rows = {
                        row.group_key: row
                        for row in intent.compatibility_rows
                        if row.kubernetes_version == version
                    }
                    for group in intent.node_groups:
                        row = rows[group.key]
                        cli.assert_frozen_compatibility_row_supported(
                            group=group,
                            row=row,
                            choices=executor.compatibility_choices(
                                target_version=version,
                                platform=group.platform,
                            ),
                        )

                def _provider_node_template_segment(version: str) -> cli.CampaignSegmentResult:
                    with upgrade_progress.phase(
                        f"provider-upgrade-{version}",
                        f"Applying Kubernetes {version} and frozen node-group templates",
                        success=(f"Kubernetes {version} and frozen node-group templates applied"),
                    ):
                        _assert_campaign_authority()
                        _assert_frozen_provider_compatibility(version)
                        evidence = infrastructure_backend.apply_version(version)
                    return cli.CampaignSegmentResult(
                        evidence=dict(evidence),
                        irreversible_frontier=f"mk8s-requested:{version}",
                    )

                def _runtime_readiness(
                    version: str, readiness_intent: cli.SoperatorUpgradeCampaignIntent | None = None
                ) -> cli.CampaignSegmentResult:
                    runtime_intent = readiness_intent or intent
                    with upgrade_progress.sequence() as readiness_progress:
                        readiness_progress.start(
                            f"runtime-readiness-{version}",
                            f"Checking Kubernetes {version} control-plane readiness",
                        )
                        _assert_campaign_authority()
                        executor.wait_cluster_version(cluster_id=cluster_id, version=version)
                        rows = {
                            row.group_key: row
                            for row in runtime_intent.compatibility_rows
                            if row.kubernetes_version == version
                        }
                        node_group_evidence: list[dict[str, object]] = []
                        total_groups = len(runtime_intent.node_groups)
                        for index, group in enumerate(runtime_intent.node_groups, start=1):
                            readiness_progress.update(
                                f"Checking node group {group.key} at Kubernetes {version}",
                                current=index,
                                total=total_groups,
                            )
                            row = rows[group.key]
                            observation = executor.wait_node_group_node_template_adaptive(
                                cluster_id=cluster_id,
                                node_group_id=group.provider_id,
                                version=version,
                                os=row.os,
                                drivers_preset=(row.drivers_preset if group.gpu else None),
                            )
                            if (
                                observation.capacity_mode == "zero-capacity"
                                and group.gpu
                                and intent.zero_size_gpu_validation == "require-capacity"
                            ):
                                raise RuntimeError(
                                    f"GPU node group {group.key!r} is at zero desired capacity, "
                                    "but this recovered campaign requires live capacity proof."
                                )
                            node_group_evidence.append(
                                {
                                    "group": group.key,
                                    "providerId": group.provider_id,
                                    "providerName": group.provider_name,
                                    "gpu": group.gpu,
                                    "capacityMode": observation.capacity_mode,
                                    "targetNodeCount": observation.target_node_count,
                                    "readyNodeCount": observation.ready_node_count,
                                    "nodeCount": observation.node_count,
                                    "outdatedNodeCount": observation.outdated_node_count,
                                    "templateVerified": True,
                                    "runtimeEvidence": (
                                        "not-applicable-zero-capacity"
                                        if group.gpu
                                        and observation.capacity_mode == "zero-capacity"
                                        else "required"
                                        if group.gpu
                                        else "not-applicable-cpu"
                                    ),
                                }
                            )
                        readiness_progress.success(
                            f"Kubernetes {version} runtime ready — {total_groups} node groups"
                        )
                    zero_size_groups = [
                        str(item["group"])
                        for item in node_group_evidence
                        if item["capacityMode"] == "zero-capacity"
                    ]
                    return cli.CampaignSegmentResult(
                        evidence={
                            "kubernetesVersion": version,
                            "readyNodeGroups": len(runtime_intent.node_groups)
                            - len(zero_size_groups),
                            "zeroSizeDesiredStateOnly": zero_size_groups,
                            "nodeGroups": node_group_evidence,
                        }
                    )

                def _fast_campaign_smoke(*, verify_only: bool = False) -> cli.Any:
                    from .soperator_deployment_profile import auxiliary_checks_suspended
                    from .soperator_fast_readiness import (
                        active_workers,
                        smoke_groups,
                        verify_fast_readiness,
                    )

                    target_paths = cli._paths_for_target_flux_dir(paths, selected_target)
                    values = cli._rendered_soperator_upstream_values(target_paths.flux_dir)
                    if not auxiliary_checks_suspended(values):
                        return {"status": "not-applicable"}
                    _assert_campaign_authority()
                    policy, _, _ = _campaign_checks().load_target()

                    def _run(command: str, timeout: int) -> str:
                        return cli._run_soperator_upgrade_login_command(
                            namespace,
                            command,
                            kube_context=kube_context,
                            extra_env=kube_env,
                            timeout_seconds=timeout,
                        ).stdout

                    def _groups() -> list[dict[str, cli.Any]]:
                        result = cli._run_soperator_upgrade_process(
                            [
                                "kubectl",
                                "--context",
                                kube_context,
                                "-n",
                                namespace,
                                "get",
                                "nodesets.slurm.nebius.ai,nodesetpowerstates.slurm.nebius.ai",
                                "-o",
                                "json",
                            ],
                            extra_env=kube_env,
                            timeout_seconds=120,
                            check=True,
                        )
                        items = cli.json.loads(result.stdout).get("items", [])
                        workers = active_workers(
                            values,
                            [item for item in items if item.get("kind") == "NodeSet"],
                            [item for item in items if item.get("kind") == "NodeSetPowerState"],
                        )
                        return smoke_groups(
                            workers,
                            _run("scontrol show nodes -o", 120),
                            _run("scontrol show partition -o", 120),
                            values=values,
                        )

                    return verify_fast_readiness(
                        generation=intent.digest,
                        reports_dir=paths.reports_dir,
                        policy=policy,
                        groups=_groups,
                        run=_run,
                        assert_authority=_assert_campaign_authority,
                        verify_only=verify_only,
                        confirm_retirement=cli._confirm_explicit_action,
                    )

                def _final_readiness() -> cli.CampaignSegmentResult:
                    final_intent = (
                        deployment_hooks.final_intent(intent)
                        if deployment_hooks is not None
                        else intent
                    )
                    readiness = _runtime_readiness(intent.target_kubernetes_version, final_intent)
                    target_flux_paths = cli._paths_for_target_flux_dir(paths, selected_target)

                    def _refresh_sources() -> cli.Any:
                        with upgrade_progress.phase(
                            "final-source-refresh",
                            "Refreshing immutable Soperator release sources",
                            success="Immutable Soperator release sources refreshed",
                        ):
                            _assert_campaign_authority()
                            return cli.prepare_soperator_release_sources(
                                target_flux_paths,
                                extra_env=kube_env,
                            )

                    def _prove_release_graph() -> cli.Any:
                        with upgrade_progress.phase(
                            "final-release-graph",
                            "Proving the final Soperator release graph",
                            success="Final Soperator release graph proved Ready",
                        ) as graph_progress:
                            _assert_campaign_authority()
                            live_release = cli._live_soperator_release_for_reconcile(env=kube_env)
                            if live_release != intent.target_release:
                                raise RuntimeError(
                                    "Soperator final readiness observed release "
                                    f"{live_release or 'unknown'}, expected "
                                    f"{intent.target_release}."
                                )
                            return cli.wait_for_soperator_release_graph(
                                target_flux_paths,
                                extra_env=kube_env,
                                emit=graph_progress.update,
                                include_active_checks=not intent.requires_fresh_checks,
                            )

                    def _freeze_capacity() -> cli.Mapping[str, cli.Mapping[str, object]]:
                        with upgrade_progress.phase(
                            "final-capacity",
                            "Freezing final node-group capacity evidence",
                            success="Final node-group capacity evidence frozen",
                        ):
                            return cli.final_node_group_capacity_snapshot(
                                intent=final_intent,
                                live_node_groups=tuple(executor.list_node_groups(cluster_id)),
                            )

                    def _validate_runtime(
                        capacity: cli.Mapping[str, cli.Mapping[str, object]],
                    ) -> tuple[object, ...]:
                        active_gpu_group_keys = tuple(
                            group.key
                            for group in final_intent.node_groups
                            if group.gpu and capacity[group.key]["capacityMode"] == "ready-capacity"
                        )
                        zero_gpu_groups = tuple(
                            group.key
                            for group in final_intent.node_groups
                            if group.gpu and capacity[group.key]["capacityMode"] == "zero-capacity"
                        )
                        skipped_validation_kinds = (
                            {"mk8s_gpu_visibility"}
                            if any(group.gpu for group in final_intent.node_groups)
                            and not active_gpu_group_keys
                            else set()
                        )
                        active_gpu_groups = {
                            group.key: group.provider_id
                            for group in final_intent.node_groups
                            if group.gpu and group.key in active_gpu_group_keys
                        }
                        validation_outcomes = cli._run_target_upgrade_validations(
                            config_path=config_path,
                            target_ref=intent.target_ref,
                            kube_env=kube_env,
                            skip_kinds=skipped_validation_kinds,
                            gpu_node_groups=active_gpu_groups,
                        )
                        gpu_validation_reports = {
                            group: {
                                "reportFile": cli._non_empty_text(outcome.get("reportFile")),
                                "reportSha256": cli._non_empty_text(outcome.get("reportSha256")),
                                "selectedNodeCount": outcome.get("selectedNodeCount"),
                                "passedNodeCount": outcome.get("passedNodeCount"),
                            }
                            for group in active_gpu_group_keys
                            for outcome in validation_outcomes
                            if outcome.get("group") == group
                        }
                        return (
                            validation_outcomes,
                            active_gpu_group_keys,
                            zero_gpu_groups,
                            skipped_validation_kinds,
                            gpu_validation_reports,
                        )

                    (
                        source_receipts,
                        validation_result,
                        post_validation_capacity,
                    ) = cli.run_final_runtime_validation_boundary(
                        refresh_sources=_refresh_sources,
                        prove_release_graph=_prove_release_graph,
                        freeze_capacity=_freeze_capacity,
                        validate_runtime=_validate_runtime,
                    )
                    checks_evidence: cli.Mapping[str, cli.Any] = {"status": "unchanged"}
                    if intent.requires_fresh_checks:
                        current = cli.load_campaign_receipt(receipt_path)
                        if current is None:
                            raise RuntimeError("campaign checks receipt disappeared")
                        checks_evidence = _campaign_checks().finalize(
                            already_released=current.maintenance == "restored"
                        )
                        if _freeze_capacity() != post_validation_capacity:
                            raise RuntimeError("campaign capacity changed during check acceptance")
                        cli.wait_for_soperator_release_graph(
                            target_flux_paths,
                            extra_env=kube_env,
                            acceptance_exemptions=_campaign_checks().readiness_exemptions(),
                        )
                    desired_application_evidence = (
                        deployment_hooks.verify_desired(
                            kube_env=kube_env,
                            documents_projection=_campaign_checks().documents_projection()
                            if intent.requires_fresh_checks
                            else None,
                        )
                        if deployment_hooks is not None
                        else {}
                    )
                    if not isinstance(validation_result, tuple) or len(validation_result) != 5:
                        raise RuntimeError(
                            "Soperator final runtime validation returned invalid evidence"
                        )
                    (
                        validation_outcomes,
                        active_gpu_group_keys,
                        zero_gpu_groups,
                        skipped_validation_kinds,
                        gpu_validation_reports,
                    ) = validation_result
                    validation_kinds = tuple(
                        cli._non_empty_text(outcome.get("kind"))
                        for outcome in validation_outcomes
                        if isinstance(outcome, cli.Mapping)
                        and cli._non_empty_text(outcome.get("kind"))
                    )
                    current = cli.load_campaign_receipt(receipt_path)
                    if current is None:
                        raise RuntimeError("campaign readiness receipt disappeared")
                    fast_smoke = (
                        _fast_campaign_smoke(verify_only=True)
                        if current.maintenance == "restored"
                        else {"status": "pending-scheduling-restoration"}
                    )
                    return cli.CampaignSegmentResult(
                        evidence={
                            **dict(readiness.evidence),
                            "fastSlurmReadiness": fast_smoke,
                            "desiredApplication": desired_application_evidence,
                            "soperatorRelease": intent.target_release,
                            "jailCudaVersion": intent.target_jail_cuda_version,
                            "validationKinds": validation_kinds,
                            "validationOutcomes": validation_outcomes,
                            "skippedValidationKinds": tuple(sorted(skipped_validation_kinds)),
                            "gpuRuntimeEvidence": {
                                **{group: "passed" for group in active_gpu_group_keys},
                                **{
                                    group: "not-applicable-zero-capacity"
                                    for group in zero_gpu_groups
                                },
                            },
                            "gpuRuntimeValidationReports": gpu_validation_reports,
                            "finalCapacitySnapshot": post_validation_capacity,
                            "releaseSourceCount": len(source_receipts),
                            "releaseGraphReproved": True,
                            "upstreamChecks": dict(checks_evidence),
                            "cudaLayers": {
                                "providerGpuStack": [
                                    group.target_drivers_preset
                                    for group in final_intent.node_groups
                                    if group.target_drivers_preset
                                ],
                                "jailCuda": intent.target_jail_cuda_version,
                            },
                        }
                    )

                segment_executors: dict[str, cli.Callable[[], cli.CampaignSegmentResult]] = {
                    "soperator-release": _release_segment,
                    "final-readiness": _final_readiness,
                }
                if deployment_hooks is not None:
                    for deployment_segment in ("retire", "grow", "final-reconcile"):
                        if deployment_segment in intent.segments:
                            segment_executors[deployment_segment] = cli.partial(
                                deployment_hooks.execute,
                                deployment_segment,
                                intent=intent,
                                assert_authority=_assert_campaign_authority,
                                config_store=campaign_config_store,
                                kube_env=kube_env,
                                kube_context=kube_context,
                                namespace=namespace,
                            )
                for segment_name in intent.segments:
                    kind, separator, version = segment_name.partition(":")
                    if not separator:
                        continue
                    if kind in {"mk8s-hop", "node-templates"}:
                        segment_executors[segment_name] = cli.partial(
                            _provider_node_template_segment, version
                        )
                    elif kind == "runtime-readiness":
                        segment_executors[segment_name] = cli.partial(_runtime_readiness, version)
                parent_lease_token = cli._SOPERATOR_PARENT_OPERATION_LEASE.set(campaign_lease)
                try:

                    def _assert_campaign_fence() -> None:
                        _assert_campaign_authority()

                    def _run_parent_campaign_once() -> cli.Any:
                        try:
                            return cli.run_campaign(
                                path=receipt_path,
                                intent=intent,
                                segment_executors=segment_executors,
                                enter_maintenance=_enter_maintenance,
                                restore_maintenance=_restore_maintenance,
                                assert_fence=_assert_campaign_fence,
                                verify_maintenance=(
                                    (lambda segment: _campaign_checks().before_segment(segment))
                                    if intent.requires_fresh_checks
                                    else None
                                ),
                            )
                        except Exception as exc:
                            disposition = cli._soperator_upgrade_failure_disposition(
                                "full-stack-campaign",
                                exc,
                            )
                            if disposition is cli.SoperatorFailureDisposition.TERMINAL:
                                cli.record_campaign_supervisor_state(
                                    path=receipt_path,
                                    intent=intent,
                                    state="terminal-failed",
                                    attempt=0,
                                    disposition=disposition.value,
                                    failure_type=type(exc).__name__,
                                )
                            raise

                    def _record_parent_stop(
                        disposition: cli.SoperatorFailureDisposition, exc: BaseException
                    ) -> None:
                        current = cli.load_campaign_receipt(receipt_path)
                        cli.record_campaign_supervisor_state(
                            path=receipt_path,
                            intent=intent,
                            state=(
                                "terminal-failed"
                                if disposition is cli.SoperatorFailureDisposition.TERMINAL
                                else "recovery-required"
                            ),
                            attempt=0,
                            disposition=disposition.value,
                            current_segment=(
                                str(current.supervisor.get("current_segment") or "")
                                if current
                                else ""
                            ),
                            maintenance_state=current.maintenance if current else "",
                            failure_type=type(exc).__name__,
                        )
                        cli.progress_console.print(
                            "Soperator campaign stopped: "
                            f"{cli.escape(cli._soperator_upgrade_supervisor_failure_detail(exc))}. "
                            "Restore the original frozen generated bundle and execution controls, "
                            "then rerun nebius-cxcli deploy CONFIG_YAML. "
                            "Cluster controllers may still be reconciling."
                        )

                    completed = cli.execute_committed_soperator_upgrade(
                        _run_parent_campaign_once,
                        on_stop=_record_parent_stop,
                    )
                    _assert_campaign_authority()
                    cli.accept_ordinary_app_baseline(
                        paths,
                        identities={
                            intent.target_ref: {
                                "cluster_id": intent.cluster_id,
                                "kubernetes_uid": intent.kubernetes_uid,
                            }
                        },
                    )
                finally:
                    cli._SOPERATOR_PARENT_OPERATION_LEASE.reset(parent_lease_token)
            cli.console.print(
                "[green]Soperator full-stack upgrade completed[/green]: "
                f"{intent.target_ref} -> release {intent.target_release}, "
                f"Kubernetes {intent.target_kubernetes_version}; "
                f"maintenance {completed.maintenance}."
            )
            return intent
    finally:
        if sdk is not None:
            with cli.suppress(Exception):
                sdk.sync_close()
