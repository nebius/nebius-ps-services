"""Final fresh-install invariants, independent of CLI parsing and rendering."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .deploy_targets import enabled_cluster_target_refs
from .soperator_login_keys import explicit_root_keys
from .soperator_release import SoperatorReleaseSnapshot


def app_ids_on_soperator_targets(payload: Mapping[str, Any]) -> set[str]:
    """Identify app types whose enabled instances all belong to Soperator targets.

    Dependency discovery operates on app IDs. Mixed or ambiguous ownership must
    retain generic dependency rules, including during later config validation.
    """
    rows = [row for row in payload.get("apps", {}).get("charts", []) if row.get("enabled")]
    targets = set(enabled_cluster_target_refs(payload))

    def target(row: Mapping[str, Any]) -> str:
        instance = str(row.get("instance_id") or "").strip()
        derived = str(row.get("target_ref") or "").strip()
        if instance and derived and instance != derived:
            return ""
        resolved = instance or derived
        return resolved if resolved in targets else ""

    owned_targets = {target(row) for row in rows if row.get("id") == "soperator"} - {""}
    app_ids = {str(row.get("id")) for row in rows}
    return {
        app_id
        for app_id in app_ids
        if all(target(row) in owned_targets for row in rows if row.get("id") == app_id)
    }


def validate_soperator_install_configuration(
    payload: Mapping[str, Any], release: SoperatorReleaseSnapshot
) -> None:
    rows = [row for row in payload.get("apps", {}).get("charts", []) if row.get("enabled")]
    soperator = [row for row in rows if row.get("id") == "soperator"]
    if len(soperator) != 1 or soperator[0].get("version") != release.release:
        raise ValueError(
            "Soperator install must retain exactly one application at its frozen release."
        )
    if any(row.get("id") == "cert-manager" for row in rows):
        raise ValueError(
            "Soperator install uses upstream cert-manager; a standalone duplicate is invalid."
        )
    cert_manager = soperator[0].get("values", {}).get("certManager", {})
    if not isinstance(cert_manager, Mapping) or cert_manager.get("enabled", True) is not True:
        raise ValueError("Soperator install requires upstream certManager.enabled=true.")
    if explicit_root_keys(soperator[0]) is None:
        raise ValueError(
            "Soperator install requires an explicit root SSH key selection before saving. "
            "Select a public key or deliberately configure an empty key list."
        )
