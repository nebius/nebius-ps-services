"""WireGuard source-only day-2 loading and explicitly privileged lifecycle commands."""

from __future__ import annotations

import copy
import json
import os
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

import yaml

from .cluster_config import validate_source_contract
from .component_instances import component_instance_id
from .paths import resolve_project_paths
from .wireguard_access import (
    decode_connection,
    temporary_ssh_access,
    validate_access,
    validate_connection,
)
from .wireguard_clients import select_wireguard_component
from .wireguard_host_identity import (
    _modules,
    publish_connection,
    remote_access,
    verify_guest,
    wireguard_host_outputs,
    wireguard_rows,
)
from .wireguard_journal import WireGuardJournal
from .wireguard_secret_api import wireguard_api


def load_wireguard_context(config_path: Path, selector: str | None = None):
    """No normalization, auth materialization, backend, render, catalog or cloud calls."""
    raw = config_path.read_bytes()
    if len(raw) > 4 * 1024 * 1024:
        raise RuntimeError("WireGuard configuration exceeds its read bound")
    payload = yaml.safe_load(raw)
    validate_source_contract(payload)
    component = select_wireguard_component(payload, component_selector=selector)
    row = next(
        row
        for row in wireguard_rows(payload)
        if component_instance_id(row) == component.instance_id
    )
    validate_access(row)
    nebius = payload["client_info"]["nebius"]
    project = row["inputs"].get("parent_id") or nebius["project_id"]
    if project != nebius["project_id"]:
        raise RuntimeError("WireGuard project differs from source configuration")
    return payload, resolve_project_paths(config_path), component, row, project


def state_wireguard_instances(
    cli: Any, paths: Any, runtime: dict[str, str], *, initialize: bool = False
) -> set[str]:
    state = cli.terraform_show_json(paths.infra_dir, extra_env=runtime, initialize=initialize)
    result = set()
    for resource in cli._terraform_state_resources(state):
        values = resource.get("values") or {}
        if resource.get("type") == "nebius_compute_v1_instance" and (
            values.get("labels", {}).get("component") == "wireguard-gw"
            or "/etc/nebius-wireguard/access.json" in str(values.get("cloud_init_user_data", ""))
        ):
            if not values.get("id"):
                raise RuntimeError("WireGuard Terraform instance identity is incomplete")
            result.add(values["id"])
    return result


def guard_direct_apply(
    cli: Any, config: Any, paths: Any, runtime: dict[str, str], *, initialize: bool
) -> None:
    from .wireguard_host_identity import CURRENT_WIREGUARD_IDENTITIES

    owner = CURRENT_WIREGUARD_IDENTITIES.get()
    if owner is not None:
        for journal in owner.journals.values():
            journal.assert_held()
        return
    from .wireguard_dependencies import access_owner_rows

    if access_owner_rows(config) or state_wireguard_instances(
        cli, paths, runtime, initialize=initialize
    ):
        raise RuntimeError(
            "WireGuard infrastructure mutations require deploy so its SecretStash lifecycle can prepare and verify the gateway"
        )


def rotate_login(api: Any, journal: WireGuardJournal) -> None:
    """All guest effects and publications are replayable under the retained operation."""
    from .ssh_host_identity import generate_host_identity

    record = journal.value
    connection = copy.deepcopy(record.get("connection", {}))
    rotation = record.get("rotation")
    if rotation is None or rotation["phase"] == "complete":
        validate_connection(
            connection,
            project=record["project_id"],
            component=record["component_id"],
            generation=record["generation"],
        )
        if api.payload(record["resources"]["connection"]["id"])[0] != record["connection_version"]:
            raise RuntimeError("WireGuard connection changed outside its lifecycle journal")
        rotation = {
            "id": str(uuid.uuid4()),
            "phase": "preparing",
            "old_version": record["login_version_id"],
            "connection": connection,
        }
        record["rotation"] = rotation
        journal.save()
    else:
        connection = copy.deepcopy(rotation["connection"])
    identity = rotation["id"]
    login_secret = record["resources"]["login"]["id"]
    candidate_version = api.version(
        journal, record, f"rotate-{identity}", login_secret, generate_host_identity
    )
    _, candidate = api.payload(login_secret, candidate_version)
    _, old = api.payload(login_secret, rotation["old_version"])
    request = {"operation": identity, "public_key": candidate["ed25519_public"]}
    with (
        temporary_ssh_access(connection, candidate) as new_ssh,
        temporary_ssh_access(connection, old) as old_ssh,
    ):
        if rotation["phase"] in {"preparing", "staged"}:
            journal.assert_held()
            # Prefer candidate on retry; after retirement the old key must never be restored.
            probe = mutate_guest(journal, new_ssh, "stage", request)
            if probe.returncode:
                probe = mutate_guest(journal, old_ssh, "stage", request)
            if probe.returncode:
                raise RuntimeError("WireGuard candidate staging failed; retain rotation journal")
            verify_guest(connection, candidate)
            rotation["phase"] = "staged"
            journal.save()
            unavailable = {**connection, "state": "PENDING"}
            publish_connection(api, journal, f"rotate-{identity}-pending", unavailable)
            rotation["phase"] = "unavailable"
            journal.save()
        if rotation["phase"] == "unavailable":
            journal.assert_held()
            if mutate_guest(journal, new_ssh, "retire", request).returncode:
                raise RuntimeError("WireGuard old-key retirement is unconfirmed; resume rotation")
            rotation["phase"] = "retired"
            journal.save()
        if rotation["phase"] == "retired":
            verify_guest(connection, candidate)
            rejected = remote_access(old_ssh, "verify")
            if rejected.returncode != 255 or "Permission denied (publickey" not in rejected.stderr:
                raise RuntimeError(
                    "WireGuard old-key rejection was not proven; issuance stays unavailable"
                )
            ready = {**connection, "login_version_id": candidate_version, "state": "READY"}
            publish_connection(api, journal, f"rotate-{identity}-ready", ready)
            record["login_version_id"] = candidate_version
            rotation["phase"] = "published"
            journal.save()
        if rotation["phase"] == "published":
            journal.assert_held()
            if mutate_guest(journal, new_ssh, "finish", request).returncode:
                raise RuntimeError(
                    "WireGuard rotation publication succeeded; guest completion needs retry"
                )
            rotation["phase"] = "complete"
            journal.save()


def mutate_guest(journal: WireGuardJournal, ssh: Any, command: str, request: dict[str, str]):
    journal.begin_effect(
        client="guest", nonce=request["operation"], collection="guest", key=command
    )
    result = remote_access(ssh, command, request)
    denied = result.returncode == 255 and "Permission denied (publickey" in result.stderr
    if result.returncode and not denied:
        raise RuntimeError(
            "WireGuard guest operation is unconfirmed; remote ownership retained for explicit recovery"
        )
    journal.value.pop("in_flight")
    journal.save()
    return result


def reconcile_guest(api: Any, journal: WireGuardJournal) -> None:
    record = journal.value
    pending = record["in_flight"]
    rotation = record["rotation"]
    if pending["nonce"] != rotation["id"]:
        raise RuntimeError("WireGuard guest recovery operation differs from its journal")
    candidate = record["versions"][f"rotate-{rotation['id']}"]["id"]
    for version in (candidate, rotation["old_version"]):
        _, pair = api.payload(record["resources"]["login"]["id"], version)
        with temporary_ssh_access(rotation["connection"], pair) as ssh:
            result = remote_access(ssh, "inspect")
        if result.returncode == 255 and "Permission denied (publickey" in result.stderr:
            continue
        try:
            observed = json.loads(result.stdout)
            if result.returncode or observed["generation"] != record["generation"]:
                raise ValueError
            if observed["rotation"] not in {"", rotation["id"]} and observed["phase"] != "complete":
                raise ValueError
        except (KeyError, ValueError):
            raise RuntimeError(
                "WireGuard guest recovery could not prove its locked maintenance state"
            ) from None
        journal.value.pop("in_flight")
        journal.save()
        return
    raise RuntimeError("WireGuard guest recovery has no working owned login credential")


def rotate_from_config(
    config_path: Path, selector: str | None, credentials_file: str | None = None
) -> None:
    from . import cli
    from .config_model import to_runtime_payload
    from .object_storage_transport import object_storage_transport
    from .runtime_config import wrap_runtime_config
    from .terraform_backend import backend_settings_from_config

    payload, _paths, component, row, project = load_wireguard_context(config_path, selector)
    config = wrap_runtime_config(to_runtime_payload(payload))
    settings = backend_settings_from_config(config)
    # This privileged mode uses an existing admitted backend, never bootstraps one.
    runtime = cli._terraform_runtime_env(config)
    with (
        wireguard_api(credentials_file=credentials_file) as api,
        object_storage_transport(settings, extra_env=runtime) as transport,
    ):
        api.admit_backend(settings)
        api.validate_group(
            row["access"]["admin_group_id"], payload["client_info"]["nebius"]["tenant_id"], project
        )
        journal = WireGuardJournal(transport, project, component.instance_id)
        journal.acquire()
        try:
            from .wireguard_foundation import assert_ownership

            assert_ownership(journal.value, settings, row)
            if (
                journal.value.get("generation") != row["access"].get("generation")
                or journal.value.get("resources", {}).get("connection", {}).get("id")
                != row["access"].get("connection_secret_id")
                or journal.value.get("phase") != "ready"
            ):
                raise RuntimeError("WireGuard rotation requires an exact deployed READY generation")
            rotate_login(api, journal)
        finally:
            journal.release()


def recover_from_config(
    config_path: Path,
    selector: str | None,
    *,
    lock_owner: str,
    prior_writer_stopped: bool,
    operation_id: str = "",
    credentials_file: str | None = None,
) -> None:
    from . import cli
    from .config_model import to_runtime_payload
    from .object_storage_transport import object_storage_transport
    from .runtime_config import wrap_runtime_config
    from .terraform_backend import backend_settings_from_config

    if not lock_owner or not prior_writer_stopped:
        raise RuntimeError(
            "Recovery requires the exact --lock-owner and --prior-writer-stopped after independently stopping the previous process and its children"
        )
    payload, _, component, row, project = load_wireguard_context(config_path, selector)
    config = wrap_runtime_config(to_runtime_payload(payload))
    settings = backend_settings_from_config(config)
    with (
        wireguard_api(credentials_file=credentials_file) as api,
        object_storage_transport(
            settings, extra_env=cli._terraform_runtime_env(config)
        ) as transport,
    ):
        api.admit_backend(settings)
        journal = WireGuardJournal(transport, project, component.instance_id)
        from .wireguard_foundation import assert_ownership

        assert_ownership(journal.value, settings, row)
        found = journal.read(journal.lock_key)
        if found is None or found[1] != {"owner": lock_owner}:
            raise RuntimeError("WireGuard recovery lock does not match the exact stopped writer")
        journal.owner, journal.lock_etag = lock_owner, found[0]
        journal.assert_held()
        if journal.value.get("in_flight", {}).get("client") == "guest":
            reconcile_guest(api, journal)
        else:
            api.reconcile_effect(journal, operation_id=operation_id)
        journal.release()


@contextmanager
def retire_for_destroy(config: Any, paths: Any, runtime: dict[str, str]):
    from . import cli
    from .deployment_cli import deployment_execution, render_publication_lock
    from .generated_manifest import load_generated_manifest
    from .object_storage_transport import object_storage_transport
    from .terraform_backend import backend_settings_from_config
    from .wireguard_foundation import assert_ownership

    rows = sorted(wireguard_rows(config), key=component_instance_id)
    source_before = paths.config_path.read_bytes()
    live = state_wireguard_instances(cli, paths, runtime)
    if not rows:
        if live:
            raise RuntimeError(
                "Retain the WireGuard component configuration until explicit destroy retires its connection"
            )
        yield runtime
        return
    settings = backend_settings_from_config(config)
    modules = _modules(load_generated_manifest(paths.generated_dir), rows)
    with (
        deployment_execution(
            config=config,
            paths=paths,
            target_ref="project",
            operation_id="WireGuard retirement",
            bootstrap_backend=False,
        ) as lease,
        ExitStack() as stack,
    ):
        api = stack.enter_context(
            wireguard_api(credentials_file=os.environ.get("CXCLI_WIREGUARD_CREDENTIALS_FILE"))
        )
        api.admit_backend(settings)
        transport = stack.enter_context(object_storage_transport(settings, extra_env=runtime))
        result = dict(runtime)
        journals = {}
        bound = set()
        outputs = cli.terraform_output_json(paths.infra_dir, extra_env=runtime, initialize=False)
        for row in rows:
            instance = component_instance_id(row)
            journal = WireGuardJournal(transport, settings.project_id, instance)
            journal.acquire()
            stack.callback(journal.release)
            journals[instance] = journal
            record = journal.value
            assert_ownership(record, settings, row)
            if not record.get("bootstrap"):
                raise RuntimeError(
                    "WireGuard has no owned bootstrap journal; refusing unbound destroy"
                )
            if record.get("rotation", {}).get("phase") not in {None, "complete"}:
                raise RuntimeError("Complete WireGuard login rotation before destroy")
            secret = record["resources"]["connection"]["id"]
            _, current = api.payload(secret)
            connection = decode_connection(current)
            if connection.get("generation") != record["generation"]:
                raise RuntimeError("WireGuard destroy binding differs from the owned generation")
            if not connection.get("instance_id") and live:
                own = wireguard_host_outputs(outputs, instance)
                api.verify_instance_binding(own, record, require_running=False)
                connection = {**connection, **own}
            if connection.get("instance_id"):
                bound.add(connection["instance_id"])
            if record["phase"] != "retired":
                publish_connection(api, journal, "retired", {**connection, "state": "RETIRED"})
                record["phase"] = "retired"
                journal.save()
            result[f"TF_VAR_{modules[instance]}_access_bootstrap"] = json.dumps(record["bootstrap"])
        if live - bound:
            raise RuntimeError(
                "WireGuard destroy includes an unbound instance; retain and reconcile its owning configuration"
            )
        yield result
        lease.assert_held()
        if state_wireguard_instances(cli, paths, result):
            raise RuntimeError("WireGuard state still contains a VM after destroy")
        for journal in journals.values():
            journal.assert_held()
            instance_id = journal.value.get("connection", {}).get("instance_id")
            if instance_id:
                api.verify_instance_absent(instance_id)
            journal.value["retirement_verified"] = True
            journal.save()
        from .config_loader import _write_text_atomic

        with render_publication_lock(config=config, paths=paths):
            if paths.config_path.read_bytes() != source_before:
                raise RuntimeError(
                    "Configuration changed during destroy; preserve the retired owner and rerender"
                )
            source = yaml.safe_load(source_before)
            for row in source["infra"]["components"]:
                retained_journal = journals.get(component_instance_id(row))
                if retained_journal is None:
                    continue
                row["enabled"] = False
                row["access"].update(
                    lifecycle="retained",
                    generation=retained_journal.value["generation"],
                    connection_secret_id=retained_journal.value["resources"]["connection"]["id"],
                )
            _write_text_atomic(
                paths.config_path,
                yaml.safe_dump(source, sort_keys=False),
                file_mode=paths.config_path.stat().st_mode & 0o777,
            )
