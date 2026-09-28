"""Read-only application target observations, shared by apply and upgrade."""

from __future__ import annotations

import json
from contextlib import ExitStack, nullcontext
from typing import Any

from .compatibility_adapters import helm_constraint
from .compatibility_matrix import assess, digest, require_admitted
from .compatibility_schema import kubernetes_minor
from .deployment_recovery import deployment_preview
from .helm_readiness import SubprocessHelmCommandRunner, list_helm_releases
from .ordinary_apps import validate_ordinary_app_scope


def observe_application_targets(
    cli, config, paths, manifest, targets, *, plan=None, stack=None, kube_envs=None
):
    observations = {}
    # The preview owner prevents auth creation, key rotation and backend setup.
    with (
        deployment_preview(True),
        nullcontext(stack) if stack is not None else ExitStack() as stack,
    ):
        for target in targets or [None]:
            ref = str(target["target_ref"]) if target else ""
            resolved = dict(target) if target else None
            baseline = (
                validate_ordinary_app_scope(paths, manifest=manifest)
                if cli._payload_has_soperator_lifecycle(config)
                else None
            )
            if resolved and not resolved.get("cluster_id") and not resolved.get("kube_context"):
                if baseline is not None:
                    resolved["cluster_id"] = baseline["identities"][ref]["cluster_id"]
                elif resolved.get("cluster_id_output_name"):
                    resolved["cluster_id"] = cli.terraform_output_raw(
                        paths.infra_dir,
                        resolved["cluster_id_output_name"],
                        extra_env=cli._terraform_runtime_env(config),
                        initialize=False,
                    )
            env = cli._prepare_cluster_handoff_kube_env(
                config,
                paths,
                stack=stack,
                target=resolved,
                persist_local_kubeconfig=False,
                set_current_context=False,
                allow_terraform_output=False,
            )
            if env is None:
                raise ValueError("Application target requires an explicit cluster handoff")
            if kube_envs is not None:
                kube_envs[ref] = env
            runner = SubprocessHelmCommandRunner(extra_env=env)
            version = json.loads(runner(["kubectl", "version", "-o", "json"]).stdout)
            kubernetes = str(version.get("serverVersion", {}).get("gitVersion", ""))
            if not kubernetes_minor(kubernetes):
                raise ValueError("Application target did not return its Kubernetes version")
            uid = cli._read_kube_system_namespace_uid(
                kube_context=(env or {}).get(cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV, ""), extra_env=env
            )
            if not uid:
                raise ValueError("Application target did not return its immutable cluster identity")
            if baseline is not None and uid != baseline["identities"][ref]["kubernetes_uid"]:
                raise ValueError("Application target differs from the accepted cluster identity")
            frozen = manifest["render"]["compatibility"]
            proofs = {
                row["subject_sha256"]: row
                for row in frozen["receipts"]
                if row["evaluator"] == "exact_artifact_kube_version"
            }
            for subject in frozen["inventory"]:
                if (
                    subject["kind"] != "helm"
                    or (baseline is not None and subject["owner"] == "soperator")
                    or (target and subject["instance_id"] != ref)
                    or not subject.get("enabled", True)
                ):
                    continue
                expected = subject.get("kubernetes_minor")
                if expected and expected != kubernetes_minor(kubernetes):
                    raise ValueError(
                        "Live Kubernetes version differs from frozen application target; rerender required"
                    )
                proof = proofs.get(digest(subject))
                if proof:
                    artifact = proof["artifact"]
                    helm_constraint(artifact["metadata"], kubernetes)
                    for child in artifact.get("enabled_dependencies", []):
                        helm_constraint(child["metadata"], kubernetes)
            observation = {
                "kubernetes_version": kubernetes,
                "kubernetes_uid": uid,
                "cluster_id": (env or {}).get(cli.GRAFANA_TARGET_CLUSTER_ID_ENV, ""),
            }
            if plan is not None:
                releases = [
                    release
                    for release in list_helm_releases(
                        command_runner=runner, namespace=plan.namespace or "default"
                    )
                    if release.name == plan.release_name
                ]
                if len(releases) != 1:
                    raise ValueError(
                        "Chart upgrade requires one existing release with the selected identity"
                    )
                release = releases[0]
                observation["release"] = {
                    "name": release.name,
                    "namespace": release.namespace,
                    "chart_version": release.chart_version,
                    "revision": release.revision,
                }
            observations[ref] = observation
            if resolved:
                for item in manifest.get("deploy", {}).get("targets", []):
                    if item["target_ref"] == ref and resolved.get("cluster_id"):
                        item["cluster_id"] = resolved["cluster_id"]
    return observations


def validate_observed_applications(manifest, observations, *, ordinary=False):
    """Use actual API-server patches for native constraints and support checks."""
    import copy

    frozen = manifest["render"]["compatibility"]
    proofs = {
        row["subject_sha256"]: row
        for row in frozen["receipts"]
        if row["evaluator"] == "exact_artifact_kube_version"
    }
    inventory = []
    for original in frozen["inventory"]:
        ref = original["instance_id"].split("/", 1)[0]
        observation = observations.get(ref, observations.get(""))
        if (
            not observation
            or original["kind"] == "terraform"
            or not original.get("enabled", True)
            or (ordinary and original["owner"] == "soperator")
        ):
            continue
        subject = copy.deepcopy(original)
        actual = observation["kubernetes_version"]
        subject.update(kubernetes_version=actual, kubernetes_minor=kubernetes_minor(actual))
        inventory.append(subject)
        if original["kind"] == "helm":
            proof = proofs.get(digest(original))
            if proof:
                artifact = proof["artifact"]
                helm_constraint(artifact["metadata"], actual)
                for child in artifact.get("enabled_dependencies", []):
                    helm_constraint(child["metadata"], actual)
    report = assess(inventory, matrix=manifest["render"]["inputs"]["matrix"])
    require_admitted(report)
    return report


def require_same_upgrade_target(expected, actual):
    for ref, previous in expected.items():
        current = actual.get(ref, {})
        if any(previous.get(key) != current.get(key) for key in ("kubernetes_uid", "cluster_id")):
            raise ValueError("Chart upgrade retry changed immutable cluster identity")
        if any(
            previous.get("release", {}).get(key) != current.get("release", {}).get(key)
            for key in ("name", "namespace")
        ):
            raise ValueError("Chart upgrade retry changed release identity")


def execute_admitted_flux_apply(
    cli,
    config,
    paths,
    manifest,
    *,
    target_ref,
    all_targets,
    job_policy,
    cancel_job,
    requeue_job,
    job_wait_timeout,
    job_refresh_interval,
    kube_envs,
):
    """Effects for a private generation whose complete selected scope was admitted."""
    paths.reports_dir.mkdir(parents=True, exist_ok=True)
    cli.write_inventory(config, paths, validations=cli._manifest_deploy_validations(manifest))
    manifest_targets = cli._manifest_deploy_targets(manifest)
    grafana_statuses: list[dict[str, Any]] = []
    resolved_job_policy = cli._soperator_runtime_job_policy(job_policy)
    selected_cancel_job_ids = tuple(cancel_job or ())
    selected_requeue_job_ids = tuple(requeue_job or ())
    job_wait_timeout_seconds = cli._soperator_upgrade_duration_seconds(
        job_wait_timeout,
        option_name="--job-wait-timeout",
    )
    job_refresh_interval_seconds = cli._soperator_upgrade_duration_seconds(
        job_refresh_interval,
        option_name="--job-refresh-interval",
    )
    if manifest_targets:
        selected_targets = cli._resolve_selected_deploy_targets(
            manifest,
            requested_target_ref=target_ref,
            all_targets=all_targets,
        )
        for target in selected_targets:
            target_ref_value = str(target["target_ref"])
            target_paths = cli._paths_for_target_flux_dir(paths, target)
            if len(selected_targets) > 1:
                cli.console.print(f"[bold]Target {target_ref_value}[/bold]")
            with cli.ExitStack():
                kube_env = kube_envs[target_ref_value]
                cli._report_cluster_nodes_status(
                    extra_env=kube_env, emit=lambda message: cli.console.print(message)
                )
                cli._ensure_mysterybox_eso_runtime_before_flux(
                    config,
                    extra_env=kube_env,
                    target_ref=target_ref_value,
                )
                cli._ensure_grafana_runtime_before_flux(
                    config,
                    extra_env=kube_env,
                    target_ref=target_ref_value,
                )
                cli._ensure_soperator_notifier_runtime_before_flux(
                    config,
                    extra_env=kube_env,
                    target_ref=target_ref_value,
                    externally_managed_secret_keys=cli._mysterybox_eso_rendered_secret_keys(
                        target_paths
                    ),
                )
                cli._ensure_soperator_runtime_before_flux(
                    config,
                    paths=target_paths,
                    extra_env=kube_env,
                    target_ref=target_ref_value,
                )
                cli._apply_rendered_flux_with_soperator_job_policy(
                    config,
                    target_paths,
                    command_name="flux apply",
                    target_ref=target_ref_value,
                    extra_env=kube_env,
                    job_policy=resolved_job_policy,
                    cancel_job_ids=selected_cancel_job_ids,
                    requeue_job_ids=selected_requeue_job_ids,
                    job_wait_timeout_seconds=job_wait_timeout_seconds,
                    job_refresh_interval_seconds=job_refresh_interval_seconds,
                )
                grafana_statuses.extend(
                    cli._collect_grafana_status_after_flux(
                        config,
                        extra_env=kube_env,
                        target_ref=target_ref_value,
                    )
                )
                cli._warn_if_flux_gitops_not_bootstrapped(
                    config,
                    target_paths,
                    extra_env=kube_env,
                    target_ref=target_ref_value,
                )
            cli.console.print(f"Flux applied from {target_paths.flux_dir}")
    else:
        cli._report_cluster_nodes_status(
            extra_env=kube_envs.get(""), emit=lambda message: cli.console.print(message)
        )
        cli._ensure_mysterybox_eso_runtime_before_flux(
            config,
            extra_env=kube_envs.get(""),
        )
        cli._ensure_grafana_runtime_before_flux(config, extra_env=kube_envs.get(""))
        cli._ensure_soperator_notifier_runtime_before_flux(
            config,
            extra_env=kube_envs.get(""),
            externally_managed_secret_keys=cli._mysterybox_eso_rendered_secret_keys(paths),
        )
        cli._ensure_soperator_runtime_before_flux(config, paths=paths, extra_env=kube_envs.get(""))
        cli._apply_rendered_flux_with_soperator_job_policy(
            config,
            paths,
            command_name="flux apply",
            target_ref="",
            extra_env=kube_envs.get(""),
            job_policy=resolved_job_policy,
            cancel_job_ids=selected_cancel_job_ids,
            requeue_job_ids=selected_requeue_job_ids,
            job_wait_timeout_seconds=job_wait_timeout_seconds,
            job_refresh_interval_seconds=job_refresh_interval_seconds,
        )
        grafana_statuses.extend(
            cli._collect_grafana_status_after_flux(config, extra_env=kube_envs.get(""))
        )
        cli._warn_if_flux_gitops_not_bootstrapped(
            config,
            paths,
            extra_env=kube_envs.get(""),
        )
        cli.console.print(f"Flux applied from {paths.flux_dir}")
    if grafana_statuses:
        cli.write_grafana_status(
            paths,
            grafana_statuses,
            preserve_existing=bool(
                manifest_targets
                and selected_targets
                and len(selected_targets) < len(manifest_targets)
            ),
        )
        cli.write_inventory(config, paths, validations=cli._manifest_deploy_validations(manifest))
