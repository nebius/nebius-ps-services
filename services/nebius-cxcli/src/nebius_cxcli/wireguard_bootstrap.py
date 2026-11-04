"""Immutable, identity-bound WireGuard boot payloads; never Terraform state edits."""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import re
from collections.abc import Mapping
from contextlib import suppress
from typing import Any

import yaml

from .object_storage_errors import ObjectStorageError

MAX_BOOTSTRAP = 32768


def resources(payload: Mapping[str, Any], *, planned: bool = False) -> list[dict]:
    root = payload.get("planned_values" if planned else "values", {}).get("root_module", {})

    def walk(module):
        return [
            *module.get("resources", []),
            *[r for m in module.get("child_modules", []) for r in walk(m)],
        ]

    return walk(root)


def _files(body: str) -> dict[str, str]:
    if not isinstance(body, str) or not body or len(body.encode()) > MAX_BOOTSTRAP:
        raise RuntimeError("WireGuard bootstrap is absent or exceeds the provider bound")
    try:
        result = {}
        for entry in yaml.safe_load(body)["write_files"]:
            content = entry["content"]
            if entry.get("encoding") == "gz+b64":
                content = gzip.decompress(base64.b64decode(content, validate=True)).decode()
            elif entry.get("encoding"):
                raise ValueError
            result[entry["path"]] = content
        return result
    except (ValueError, KeyError, TypeError, OSError, yaml.YAMLError):
        raise RuntimeError("WireGuard bootstrap cannot be validated") from None


def validate_bootstrap(body: str, record: Mapping[str, Any], inputs: Mapping[str, Any]) -> None:
    try:
        files = _files(body)
        access = json.loads(files["/etc/nebius-wireguard/access.json"])
        if any(access.get(key) != value for key, value in record["bootstrap"].items()):
            raise ValueError
        if access["ssh_user"] != record["ssh_user"]:
            raise ValueError
        script = files["/usr/local/bin/setup-wireguard.sh"]
        match = re.search(
            r"cat > /etc/nebius-wireguard/config\.json <<'EOF'\n(.*?)\nEOF", script, re.S
        )
        if not match:
            raise ValueError
        original = json.loads(match[1])
        for key, value in original.items():
            if key != "wireguard_mtu" and key in inputs and inputs[key] != value:
                raise RuntimeError(
                    f"WireGuard first-boot input '{key}' is immutable; use its supported day-2 operation or explicit retirement"
                )
        client_match = re.search(
            r"cat > /etc/nebius-wireguard/bootstrap-clients\.json <<'EOF'\n(.*?)\nEOF", script, re.S
        )
        if "clients" in inputs and (
            not client_match or json.loads(client_match[1]) != inputs["clients"]
        ):
            raise RuntimeError("WireGuard bootstrap clients are immutable; use client issuance")
    except (KeyError, ValueError, TypeError):
        raise RuntimeError(
            "WireGuard bootstrap does not match its owned access generation"
        ) from None


def payload_from_state(payload: Mapping[str, Any], module: str, record: Mapping[str, Any]) -> str:
    selected = [
        r for r in resources(payload) if r.get("address", "").startswith(f"module.{module}.")
    ]
    machines = [r["values"] for r in selected if r.get("type") == "nebius_compute_v1_instance"]
    revisions = [
        r["values"]
        for r in selected
        if r.get("address")
        == f"module.{module}.module.vm.terraform_data.cloud_init_user_data_revision"
    ]
    own = record.get("connection", {})
    if len(machines) != 1 or len(revisions) != 1:
        raise RuntimeError("WireGuard bootstrap requires one exact managed instance and revision")
    vm = machines[0]
    body = vm.get("cloud_init_user_data")
    if (
        vm.get("id") != own.get("instance_id")
        or vm.get("boot_disk", {}).get("existing_disk", {}).get("id") != own.get("boot_disk_id")
        or not isinstance(body, str)
        or revisions[0].get("input") != hashlib.sha256(body.encode()).hexdigest()
    ):
        raise RuntimeError(
            "WireGuard Terraform bootstrap identity or revision disagrees with its journal"
        )
    return body


def load_snapshot(journal: Any) -> str | None:
    ref = journal.value.get("bootstrap_artifact")
    if ref is None:
        return None
    digest = ref.get("sha256", "")
    expected = journal.key.removesuffix(".json") + "/bootstrap/" + digest + ".cloud-init"
    if not re.fullmatch(r"[a-f0-9]{64}", digest) or ref.get("key") != expected:
        raise RuntimeError("Invalid WireGuard bootstrap artifact reference")
    result = journal.transport.request("get_object", expected, max_bytes=MAX_BOOTSTRAP)
    if hashlib.sha256(result.body).hexdigest() != digest:
        raise RuntimeError("WireGuard bootstrap artifact digest mismatch")
    return result.body.decode()


def freeze_snapshot(journal: Any, body: str) -> None:
    journal.assert_held()
    digest = hashlib.sha256(body.encode()).hexdigest()
    key = journal.key.removesuffix(".json") + "/bootstrap/" + digest + ".cloud-init"
    existing = load_snapshot(journal)
    if existing is not None:
        if existing != body:
            raise RuntimeError("WireGuard bootstrap is already frozen to another payload")
        return
    # A duplicate or lost acknowledgement is accepted only on exact readback.
    with suppress(ObjectStorageError):
        journal.transport.request("put_object", key, body=body.encode(), create_only=True)
    observed = journal.transport.request("get_object", key, max_bytes=MAX_BOOTSTRAP)
    if observed.body != body.encode():
        raise RuntimeError("WireGuard bootstrap publication could not be confirmed")
    journal.value["bootstrap_artifact"] = {"key": key, "sha256": digest}
    journal.save()


def prepare_bootstraps(owner: Any) -> None:
    owner.bootstrap_changed = False
    state = None
    for row in owner.rows:
        from .component_instances import component_instance_id

        instance = component_instance_id(row)
        journal = owner.journals[instance]
        body = load_snapshot(journal)
        if body is None and journal.value.get("connection"):
            if state is None:
                state = owner.executor.cli.terraform_show_json(
                    owner.executor.paths.infra_dir,
                    extra_env=owner.executor.runtime_env,
                    initialize=False,
                )
            body = payload_from_state(state, owner.modules[instance], journal.value)
            if not owner.executor.options.dry_run:
                owner._verify_compute(journal.value["connection"], journal.value)
        if body is not None:
            validate_bootstrap(body, journal.value, row.get("inputs", {}))
            if not owner.executor.options.dry_run:
                freeze_snapshot(journal, body)
            # Terraform string environment variables contain raw text, not JSON string encoding.
            owner.env[f"TF_VAR_{owner.modules[instance]}_bootstrap_user_data"] = body


def freeze_plan_bootstraps(owner: Any, plan: Mapping[str, Any]) -> bool:
    changed = False
    for row in owner.rows:
        from .component_instances import component_instance_id

        instance = component_instance_id(row)
        journal = owner.journals[instance]
        module = owner.modules[instance]
        machines = [
            r
            for r in resources(plan, planned=True)
            if r.get("address", "").startswith(f"module.{module}.")
            and r.get("type") == "nebius_compute_v1_instance"
        ]
        if len(machines) != 1:
            raise RuntimeError("WireGuard plan lacks its exact instance bootstrap")
        body = machines[0].get("values", {}).get("cloud_init_user_data")
        validate_bootstrap(body, journal.value, row.get("inputs", {}))
        frozen = load_snapshot(journal)
        if frozen is not None and body != frozen:
            raise RuntimeError("WireGuard plan changed the frozen bootstrap")
        if frozen is None and not owner.executor.options.dry_run:
            freeze_snapshot(journal, body)
            owner.env[f"TF_VAR_{module}_bootstrap_user_data"] = body
            changed = True
    if changed:
        owner.apply_runtime_inputs(owner.executor)
    return changed
