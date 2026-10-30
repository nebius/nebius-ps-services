"""Explicit metadata-only OAuth containers; provisioning never stores payloads."""

from typing import Any

from .component_sources import component_output_root_name
from .runtime_config import to_plain_data


def dependencies(config: Any) -> dict[str, dict[str, Any]]:
    rows = to_plain_data(config).get("infra", {}).get("components", [])
    result = {}
    for row in rows:
        if row.get("id") != "tailscale-gw" or not row.get("enabled"):
            continue
        ref = row.get("tailscale", {}).get("auth", {}).get("secretstash", {})
        if not ref.get("component_instance"):
            continue
        found = [r for r in rows if r.get("instance_id") == ref["component_instance"]]
        if len(found) != 1 or found[0].get("id") != "mysterybox" or not found[0].get("enabled"):
            raise ValueError("Tailscale requires its exact enabled SecretStash component")
        target = found[0]
        inputs = target.get("inputs", {})
        if (
            inputs.get("create_initial_versions") is not False
            or inputs.get("parent_id") != ref["project_id"]
        ):
            raise ValueError(
                "Tailscale SecretStash dependency must be metadata-only in the declared project"
            )
        secrets = inputs.get("secrets", [])
        if (
            len([s for s in secrets if s.get("name") == ref["secret_name"]]) != 1
            or "payload_values" in inputs
            or any(set(s) - {"name", "description", "labels"} for s in secrets)
        ):
            raise ValueError(
                "Tailscale OAuth container must declare exact secret metadata without payloads"
            )
        result[row["instance_id"]] = target
    return result


def materialize(
    payload: dict[str, Any], *, selected_instances: set[str] | None = None
) -> list[str]:
    rows = payload.get("infra", {}).get("components", [])
    added = []
    for row in list(rows):
        if (
            row.get("id") != "tailscale-gw"
            or not row.get("enabled")
            or (
                selected_instances is not None
                and row.get("instance_id") not in selected_instances
                and "tailscale-gw" not in selected_instances
            )
        ):
            continue
        cfg = row.setdefault("tailscale", {})
        cfg.setdefault("stage", "infrastructure")
        ref = cfg.get("auth", {}).get("secretstash", {})
        instance = ref.get("component_instance")
        if not instance or any(r.get("instance_id") == instance for r in rows):
            continue
        rows.append(
            {
                "id": "mysterybox",
                "instance_id": instance,
                "enabled": True,
                "inputs": {
                    "parent_id": ref["project_id"],
                    "create_initial_versions": False,
                    "secrets": [{"name": ref["secret_name"]}],
                },
            }
        )
        added.append(instance)
    return added


def resolve_secret(ref: dict[str, Any], raw_outputs: dict[str, Any]) -> str:
    values = raw_outputs.get(
        component_output_root_name(ref["component_instance"], "secret_ids"), {}
    ).get("value", {})
    identity = values.get(ref["secret_name"])
    if (
        not isinstance(identity, str)
        or not identity
        or ref.get("secret_id") not in {None, identity}
    ):
        raise RuntimeError("SecretStash linked container identity is missing or changed")
    return identity


def assert_removal_allowed(payload: dict[str, Any], removed: set[str]) -> None:
    for owner, dependency in dependencies(payload).items():
        if dependency["instance_id"] in removed and owner not in removed:
            raise ValueError(
                "Cannot remove the OAuth SecretStash container while its Tailscale gateway remains"
            )


def skipped_new_dependencies(payload: dict, skipped: set[str], added: set[str]) -> set[str]:
    links = dependencies(payload)
    return {
        target["instance_id"]
        for owner, target in links.items()
        if owner in skipped
        and target["instance_id"] in added
        and all(
            other in skipped
            for other, same in links.items()
            if same["instance_id"] == target["instance_id"]
        )
    }


def prune_unreferenced(payload: dict, created: set[str]) -> None:
    rows = payload.get("infra", {}).get("components", [])
    used = {
        r.get("tailscale", {}).get("auth", {}).get("secretstash", {}).get("component_instance")
        for r in rows
    }
    payload["infra"]["components"] = [
        r for r in rows if r.get("instance_id") not in created or r.get("instance_id") in used
    ]
