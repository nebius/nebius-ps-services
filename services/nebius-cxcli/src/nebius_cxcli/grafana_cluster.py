"""Accepted cluster admission and dashboard-only replay; never deploys applications."""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .app_mutation import app_mutation_scope, current_app_mutation_authority
from .grafana_api import GrafanaClient, GrafanaError, basic_auth
from .grafana_dashboards import content_digest, read_dashboard
from .grafana_import import execute_imports, prepare_imports, require_complete
from .grafana_project import declarations, grafana_row
from .grafana_runtime import (
    GRAFANA_TARGET_CLUSTER_ID_ENV,
    GRAFANA_TARGET_KUBE_CONTEXT_ENV,
    GrafanaReleaseSpec,
    _grafana_admin_credentials,
    _kubectl_json,
    grafana_api_endpoint,
    grafana_release_specs,
)
from .paths import ProjectPaths
from .runtime_config import to_plain_data


@contextmanager
def release_client(
    config: Any,
    *,
    target: str,
    env: Mapping[str, str],
    fence: Callable[[], object],
    progress: Callable[[str], None] = lambda _: None,
) -> Iterator[GrafanaClient]:
    from .observability import _grafana_cli_settings

    specs = grafana_release_specs(config, target_ref=target)
    if len(specs) != 1:
        raise ValueError("The selected target must have exactly one enabled Grafana release")
    spec = specs[0]
    progress("Reading Grafana access credentials")
    fence()
    try:
        credentials = _grafana_admin_credentials(spec, extra_env=env)
    except Exception:
        raise GrafanaError(
            "Cannot read the configured Grafana admin Secret; check Kubernetes access and Secret keys"
        ) from None
    if not credentials:
        raise GrafanaError(
            "Configured Grafana admin Secret is missing its password; install Grafana first"
        )
    progress("Opening Grafana connection")
    with (
        app_mutation_scope(fence),
        grafana_api_endpoint(spec, extra_env=env, loopback_only=True) as endpoint,
    ):
        client = GrafanaClient(
            endpoint,
            basic_auth(*credentials),
            org_id=_grafana_cli_settings().org_id,
            assert_authority=fence,
        )
        credentials = None
        progress("Checking Grafana authentication and API")
        client.connect()
        yield client


def _verify_release(
    spec: GrafanaReleaseSpec, expected: dict[str, Any], env: Mapping[str, str]
) -> None:
    meta = expected.get("metadata", {})
    release_spec = expected.get("spec", {})
    if (release_spec.get("targetNamespace") or meta.get("namespace")) != spec.namespace or (
        release_spec.get("releaseName") or meta.get("name")
    ) != spec.release_name:
        raise GrafanaError("Grafana release differs from the accepted application")
    admin = release_spec.get("values", {}).get("admin", {})
    if (
        admin.get("existingSecret") != spec.admin_secret_name
        or admin.get("userKey", "admin-user") != spec.admin_user_key
        or admin.get("passwordKey", "admin-password") != spec.admin_password_key
    ):
        raise GrafanaError("Grafana Secret binding differs from the accepted release")
    live = _kubectl_json(
        ["-n", str(meta["namespace"]), "get", "helmrelease", str(meta["name"]), "-o", "json"],
        extra_env=env,
    )
    owner = meta.get("annotations", {}).get("cxcli.nebius.com/app-owner")
    live_meta = live.get("metadata", {})
    if (
        not owner
        or live_meta.get("annotations", {}).get("cxcli.nebius.com/app-owner") != owner
        or live_meta.get("deletionTimestamp")
    ):
        raise GrafanaError("Grafana HelmRelease ownership differs from the accepted application")
    from .deployment_observation import _owned_matches

    def with_server_defaults(value: dict[str, Any]) -> dict[str, Any]:
        result = {"suspend": False, **copy.deepcopy(value)}
        if isinstance(result.get("chart", {}).get("spec"), dict):
            result["chart"]["spec"].setdefault("reconcileStrategy", "ChartVersion")
        if isinstance(result.get("uninstall"), dict):
            result["uninstall"].setdefault("deletionPropagation", "background")
        return result

    desired_spec = with_server_defaults(release_spec)
    live_spec = with_server_defaults(live.get("spec", {}))
    if (
        live_spec.get("suspend")
        or live_spec.get("kubeConfig")
        or not _owned_matches(desired_spec, live_spec, live_spec)
    ):
        raise GrafanaError("Grafana HelmRelease configuration differs from owned rendered intent")
    ready: dict[str, Any] = next(
        (
            item
            for item in live.get("status", {}).get("conditions", [])
            if item.get("type") == "Ready"
        ),
        {},
    )
    if ready.get("status") != "True" or ready.get(
        "observedGeneration", live.get("status", {}).get("observedGeneration")
    ) != live_meta.get("generation"):
        raise GrafanaError("Grafana HelmRelease is not ready at its current generation")
    service = _kubectl_json(
        ["-n", spec.namespace, "get", "service", spec.service_name, "-o", "json"], extra_env=env
    )
    annotations = service.get("metadata", {}).get("annotations", {})
    if (
        annotations.get("meta.helm.sh/release-name") != spec.release_name
        or annotations.get("meta.helm.sh/release-namespace") != spec.namespace
    ):
        raise GrafanaError("Grafana Service is not owned by the accepted Helm release")


@dataclass
class ClusterSession:
    config: Any
    paths: ProjectPaths
    client: GrafanaClient
    identity: dict[str, Any]
    target: str
    env: Mapping[str, str]
    source: list[bytes]
    fence: Callable[[], None]
    active: bool = False

    def accept_publication(self, expected: bytes) -> None:
        self.source[0] = expected
        self.fence()


@contextmanager
def cluster_session(
    config_path: Path,
    target: str,
    *,
    mutating: bool,
    progress: Callable[[str], None] = lambda _: None,
    pause: Any = None,
    discovery: ClusterSession | None = None,
) -> Iterator[ClusterSession]:
    from . import cli
    from .deployment_observation import target_documents
    from .deployment_state import DeploymentGeneration
    from .generated_manifest import load_generated_manifest
    from .ordinary_apps import (
        _validate_live_lifecycle_complete,
        assert_accepted_deployment,
        validate_ordinary_app_scope,
    )
    from .soperator_operation_lock import SoperatorOperationLease, SoperatorOperationLocalLock

    progress("Loading project configuration")
    source = [config_path.read_bytes()]
    config, paths = cli._load_context_readonly(config_path)
    if paths.config_path.read_bytes() != source[0]:
        raise GrafanaError("Configuration changed while loading dashboard destination")
    payload = to_plain_data(config)
    grafana_row(payload, target)
    manifest = load_generated_manifest(paths.generated_dir)
    operation = hashlib.sha256(f"grafana-import:{target}".encode()).hexdigest()
    with ExitStack() as stack:

        def authority() -> None:
            pass

        if mutating:
            progress("Acquiring project operation lease")
            lease = stack.enter_context(
                cli._deployment_execution(
                    config=config,
                    paths=paths,
                    target_ref=target,
                    operation_id=operation,
                    bootstrap_backend=False,
                    pause=pause,
                )
            )
            authority = lease.assert_held
            stack.enter_context(
                SoperatorOperationLocalLock(paths.project_dir / ".nebius-cxcli" / "config.lock")
            )
        progress("Checking accepted deployment state")
        baseline = validate_ordinary_app_scope(paths, manifest=manifest)
        if paths.config_path.read_bytes() != source[0]:
            raise GrafanaError("Configuration changed during dashboard admission")
        state = assert_accepted_deployment(
            config, paths, manifest, baseline, [target], assert_held=authority
        )
        record = state.read()
        if record is None or record.value.get("active") is not None:
            raise GrafanaError(
                "Complete the active deployment operation before managing dashboards"
            )
        identity = baseline.get("identities", {}).get(target)
        if (
            not isinstance(identity, dict)
            or not identity.get("cluster_id")
            or not identity.get("kubernetes_uid")
        ):
            raise GrafanaError("Grafana access requires an accepted immutable cluster identity")
        if discovery is not None and (
            not mutating
            or not discovery.active
            or discovery.target != target
            or discovery.paths.config_path != paths.config_path
            or discovery.source[0] != source[0]
            or any(discovery.identity.get(key) != value for key, value in identity.items())
        ):
            raise GrafanaError(
                "Grafana destination or configuration changed after discovery; rerun the import"
            )
        # Ordinary apply does not advance deployment acceptance. Its ready, owned
        # live release is checked against this exact rendered application below.
        generation = DeploymentGeneration.capture(paths, manifest)
        specs = grafana_release_specs(config, target_ref=target)
        if len(specs) != 1:
            raise GrafanaError("Select exactly one Grafana release")
        spec = specs[0]
        expected = [
            doc
            for doc in target_documents(generation, target).values()
            if doc.get("kind") == "HelmRelease"
            and doc.get("metadata", {}).get("name") == spec.release_name
            and doc.get("metadata", {}).get("namespace") == spec.namespace
        ]
        if len(expected) != 1:
            raise GrafanaError("Grafana is missing from the rendered application generation")
        import json

        owner = hashlib.sha256(
            json.dumps([payload.get("client_info", {}), target], sort_keys=True).encode()
        ).hexdigest()
        if (
            expected[0].get("metadata", {}).get("annotations", {}).get("cxcli.nebius.com/app-owner")
            != owner
        ):
            raise GrafanaError("Rendered Grafana release belongs to a different project or target")
        selected = next(
            (
                dict(item)
                for item in payload.get("deploy", {}).get("targets", [])
                if item.get("instance_id") == target
            ),
            None,
        )
        if selected is None:
            raise GrafanaError("Target does not exist in the configured deployment")
        selected.pop("kube_context", None)
        selected["cluster_id"] = identity["cluster_id"]
        env: Mapping[str, str] | None
        if discovery is not None:
            progress("Rechecking Kubernetes identity")
            # The outer discovery context owns the renewable auth and temporary
            # kubeconfig. Reuse only transport setup, never admission or authority.
            env = discovery.env
        else:
            progress("Connecting to Kubernetes")
            env = cli._prepare_cluster_handoff_kube_env(
                config,
                paths,
                stack=stack,
                target=selected,
                persist_local_kubeconfig=False,
                set_current_context=False,
                allow_terraform_output=False,
                require_renewable_auth=True,
            )
        if (
            not env
            or env.get(GRAFANA_TARGET_CLUSTER_ID_ENV) != identity["cluster_id"]
            or cli._read_kube_system_namespace_uid(
                kube_context=str(env.get(GRAFANA_TARGET_KUBE_CONTEXT_ENV) or ""), extra_env=env
            )
            != identity["kubernetes_uid"]
        ):
            raise GrafanaError("Grafana handoff differs from the accepted cluster identity")
        if mutating:
            progress("Acquiring cluster operation lease")
        cluster_lease = (
            stack.enter_context(
                SoperatorOperationLease(
                    kube_context=env[GRAFANA_TARGET_KUBE_CONTEXT_ENV],
                    cluster_id=identity["cluster_id"],
                    operation_fingerprint=operation,
                    extra_env=env,
                )
            )
            if mutating
            else None
        )

        def fence() -> None:
            authority()
            if cluster_lease is not None:
                cluster_lease.assert_held()
            if paths.config_path.read_bytes() != source[0]:
                raise GrafanaError("Configuration changed during dashboard operation")

        progress("Verifying Grafana deployment")
        fence()
        state.assert_held = fence
        _validate_live_lifecycle_complete(identity, env, state=state)
        _verify_release(spec, expected[0], env)
        with release_client(
            config, target=target, env=env, fence=fence, progress=progress
        ) as client:
            binding = {
                **identity,
                "release": spec.release_name,
                "namespace": spec.namespace,
                "organization": client.org_id,
            }
            session = ClusterSession(config, paths, client, binding, target, env, source, fence)
            session.active = True
            try:
                yield session
            finally:
                session.active = False


def replay_dashboards(
    config: Any,
    paths: ProjectPaths,
    *,
    target: str,
    env: Mapping[str, str],
    verify_only: bool = False,
) -> bool:
    """Replay frozen render assets after readiness, or observe them without writes."""
    from .grafana_project import assert_catalog_ownership

    payload = to_plain_data(config)
    rows = [
        row
        for row in payload.get("apps", {}).get("charts", [])
        if row.get("instance_id") == target and row.get("enabled") and row.get("dashboard_imports")
    ]
    if not rows:
        return True
    entries = declarations(payload, target, replay_only=True)
    if not entries:
        return True
    assert_catalog_ownership(payload, target, {item["uid"] for item in entries})
    sources = []
    for entry in entries:
        path = (
            paths.generated_dir
            / "grafana_dashboards"
            / target
            / "cxcli-api-imports"
            / f"{entry['uid']}.json"
        )
        dashboard = read_dashboard(path)
        if (
            dashboard["uid"] != entry["uid"]
            or content_digest(dashboard, entry.get("folder_uid", "")) != entry["sha256"]
        ):
            raise GrafanaError("Frozen Grafana dashboard asset differs from declared intent")
        sources.append((dashboard, entry.get("folder_uid", "")))
    fence = current_app_mutation_authority()
    if fence is None and not verify_only:
        raise GrafanaError("Dashboard replay requires the active deployment mutation authority")
    fence = fence or (lambda: None)
    with release_client(config, target=target, env=env, fence=fence) as client:
        if verify_only:
            for dashboard, _folder in sources:
                resource = client.get(dashboard["uid"])
                if resource is None:
                    return False
                client.assert_unmanaged(resource)
            return True
        plans = []
        for dashboard, folder in sources:
            resource = client.get(dashboard["uid"])
            if resource is not None:
                client.assert_unmanaged(resource)
                continue
            plans.extend(prepare_imports(client, [dashboard], folder=folder, overwrite=False))
        require_complete(execute_imports(client, plans))
    return True


def preflight_dashboard_ownership(config: Any, *, target: str, env: Mapping[str, str]) -> None:
    """Reject file ownership before Helm can remove a linked provisioning file."""
    import json

    payload = to_plain_data(config)
    if not any(
        row.get("instance_id") == target and row.get("enabled") and row.get("dashboard_imports")
        for row in payload.get("apps", {}).get("charts", [])
    ):
        return
    entries = declarations(payload, target, replay_only=True)
    if not entries:
        return
    fence = current_app_mutation_authority()
    if fence is None:
        raise GrafanaError("Dashboard ownership preflight requires deployment authority")
    fence()
    # A new cluster has no HelmRelease CRD yet. Existing Helm ownership is
    # independently checked by the application deployment before adoption.
    crd = _kubectl_json(
        ["get", "crd", "helmreleases.helm.toolkit.fluxcd.io", "-o", "json", "--ignore-not-found"],
        extra_env=env,
    )
    if not crd:
        return
    specs = grafana_release_specs(config, target_ref=target)
    if len(specs) != 1:
        raise GrafanaError("Dashboard declarations require exactly one Grafana release")
    spec = specs[0]
    live = _kubectl_json(
        [
            "-n",
            spec.namespace,
            "get",
            "helmrelease",
            spec.release_name,
            "-o",
            "json",
            "--ignore-not-found",
        ],
        extra_env=env,
    )
    if not live:
        return
    owner = hashlib.sha256(
        json.dumps([payload.get("client_info", {}), target], sort_keys=True).encode()
    ).hexdigest()
    if live.get("metadata", {}).get("annotations", {}).get("cxcli.nebius.com/app-owner") != owner:
        raise GrafanaError("Grafana release belongs to another project or target")
    _verify_release(spec, live, env)
    with release_client(config, target=target, env=env, fence=fence) as client:
        for entry in entries:
            resource = client.get(entry["uid"])
            if resource is not None:
                client.assert_unmanaged(resource)
