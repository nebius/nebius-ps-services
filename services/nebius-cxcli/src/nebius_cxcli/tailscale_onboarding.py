"""Pure administrator request and approval binding; no cloud, API or credential I/O."""

from __future__ import annotations

from typing import Any

from .tailscale_config import VERSION, digest, grants, networks


def intent(row: dict[str, Any]) -> dict[str, Any]:
    cfg, inputs = row["tailscale"], row.get("inputs", {})
    onboarding = cfg.get("onboarding", {})
    return {
        "component": row["instance_id"],
        "infrastructure": {
            k: inputs.get(k) for k in ("parent_id", "name", "subnet_id", "network_id")
        },
        "replicas": inputs.get("replicas", 2),
        "version": inputs.get("tailscale_version", VERSION),
        "tailnet": cfg.get("tailnet", ""),
        "tags": sorted(cfg.get("tags", [])),
        "routes": networks(cfg["routes"]),
        "policy": {
            "rules": cfg.get("policy", {}).get("rules", []),
            "tag_owners": cfg.get("policy", {}).get("tag_owners", []),
            "auto_approve": cfg.get("policy", {}).get("auto_approve", True),
        },
        "approval": cfg.get("approval", "external"),
        "enrollment_mode": cfg.get("auth", {}).get("mode", "oauth"),
        "ownership": {k: onboarding.get(k, "") for k in ("owner", "purpose", "environment")},
        "admin_requirements": onboarding.get("admin_requirements", []),
    }


def fingerprint(row: dict[str, Any]) -> str:
    return digest(intent(row))


def missing(row: dict[str, Any]) -> list[str]:
    value = intent(row)
    result = [key for key in ("tailnet", "tags") if not value[key]]
    result.extend("onboarding." + k for k in ("owner", "purpose") if not value["ownership"][k])
    if not value["policy"]["rules"]:
        result.append("policy.rules")
    return result


def assert_accepted(row: dict[str, Any]) -> None:
    accepted = row["tailscale"].get("onboarding", {}).get("accepted", {})
    if missing(row):
        raise ValueError(
            "Complete the administrator request before activation: " + ", ".join(missing(row))
        )
    if accepted.get("fingerprint") != fingerprint(row) or not accepted.get("reference"):
        raise ValueError(
            "Administrator handoff is missing or stale; run tailscale onboarding configure"
        )


def assert_activation(row: dict[str, Any]) -> None:
    """Admission shared by deployment and commands that resume or change access."""
    if row["tailscale"].get("stage") != "active":
        raise ValueError("Tailscale access requires active stage after administrator handoff")
    assert_accepted(row)


def request(row: dict[str, Any], *, infrastructure: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = row["tailscale"]
    value = intent(row)
    scopes = ["auth_keys", "devices:core", "devices:routes:read"]
    if value["approval"] == "api":
        scopes[-1] = "devices:routes"
    ref = cfg.get("auth", {}).get("secretstash", {})
    return {
        "schema": 1,
        "status": "DRAFT" if missing(row) else "READY_FOR_ADMIN",
        "intent_sha256": fingerprint(row),
        "intent": value,
        "missing": missing(row),
        "request": {
            "approve_tags": value["tags"],
            "oauth_scopes": scopes,
            "access_grants": grants(cfg),
            "route_auto_approvers": {r: value["tags"] for r in value["routes"]}
            if value["approval"] == "external" and value["policy"]["auto_approve"]
            else {},
            "confirm": [
                "Exact tailnet and gateway tags",
                "Access grants and route approval",
                "Device approval and Tailnet Lock signing requirements",
                "Credential delivery and rotation owner",
            ],
            "scope_note": "These scopes do not imply every API operation is restricted to two gateway devices.",
        },
        "credential_delivery": {
            "storage": "SecretStash" if ref else "protected environment/file reference",
            **{
                k: ref[k]
                for k in ("project_id", "secret_id", "component_instance", "secret_name")
                if k in ref
            },
            "payload_fields": ["client_id", "client_secret"] if ref else [],
            "reader_role": "mysterybox.payload-viewer on the exact secret" if ref else "",
            "note": "Tailnet administration and Nebius reader permissions have separate owners. Never paste secrets into the request.",
        },
        "infrastructure": infrastructure
        or {
            "status": "NOT_VERIFIED",
            "note": "Resource IDs are available after infrastructure deployment.",
        },
        "next": "Send this request to the administrators; then run tailscale onboarding configure, render and deploy.",
    }


def markdown(value: dict[str, Any]) -> str:
    import json

    return (
        "# Tailscale administrator handoff\n\n"
        + value["status"]
        + "\n\n"
        + (
            "Review the exact request below. Return the approved intent fingerprint, approval reference "
            "and a protected credential location. Coordinate any Nebius IAM permissions with the cloud owner.\n\n"
        )
        + "```json\n"
        + json.dumps(value, indent=2)
        + "\n```\n"
    )
