"""Read live chart ownership without consulting a previous deployment journal."""

from __future__ import annotations

import json

from .grafana_database import app_owner
from .ordinary_apps import resource_documents, resource_identity


def assert_live_release_inventory(cli, config, *, target_ref, flux_dir, kube_env):
    """Local apply does not prune omitted chart releases; require explicit removal."""
    context = kube_env.get(cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV)
    crd = cli._run_soperator_upgrade_kubectl(
        "default",
        [
            "get",
            "crd",
            "helmreleases.helm.toolkit.fluxcd.io",
            "-o",
            "name",
            "--ignore-not-found=true",
        ],
        kube_context=context,
        extra_env=kube_env,
        check=True,
    )
    if not crd.stdout.strip():
        return
    response = cli._run_soperator_upgrade_kubectl(
        "default",
        ["get", "helmreleases.helm.toolkit.fluxcd.io", "-A", "-o", "json"],
        kube_context=context,
        extra_env=kube_env,
        check=True,
    )
    payload = json.loads(response.stdout)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise RuntimeError("Live application release inventory is unavailable")
    owner = app_owner(config, target_ref)
    if (flux_dir / "ordinary").is_dir():
        flux_dir = flux_dir / "ordinary"
    desired = {
        resource_identity(doc)
        for doc in resource_documents(flux_dir)
        if doc.get("kind") == "HelmRelease"
    }
    for live in payload["items"]:
        metadata = live.get("metadata", {})
        if metadata.get("annotations", {}).get("cxcli.nebius.com/app-owner") != owner:
            continue
        if not metadata.get("uid") or live.get("kind") != "HelmRelease":
            raise RuntimeError("Owned application release identity is incomplete")
        if resource_identity(live) not in desired:
            raise RuntimeError(
                "Current artifacts omit an installed application release owned by this target: "
                f"{metadata.get('namespace', '')}/{metadata['name']}. "
                "Deploy does not uninstall omitted releases; remove the release explicitly first."
            )
