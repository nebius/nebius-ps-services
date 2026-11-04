"""Terraform-owned access infrastructure and SDK-owned secret-version lifecycle."""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import time
import uuid
from collections.abc import Mapping
from contextlib import ExitStack
from contextvars import ContextVar
from typing import Any

from .component_instances import component_instance_id, component_type_id
from .component_sources import component_output_root_name
from .runtime_config import to_plain_data
from .ssh_host_identity import generate_host_identity
from .wireguard_access import (
    SCHEMA,
    temporary_ssh_access,
    validate_access,
    validate_connection,
    validate_pair,
)
from .wireguard_journal import WireGuardJournal
from .wireguard_secret_api import WireGuardRequestError, wireguard_api

CURRENT_WIREGUARD_IDENTITIES: ContextVar[Any] = ContextVar(
    "cxcli_wireguard_host_identities", default=None
)


def wireguard_rows(config: Any) -> list[dict[str, Any]]:
    payload = to_plain_data(config)
    return [
        row
        for row in payload.get("infra", {}).get("components", [])
        if component_type_id(row) == "wireguard-gw" and row.get("enabled", False)
    ]


def _modules(manifest: Mapping[str, Any], rows: list[dict[str, Any]]) -> dict[str, str]:
    modules = [
        item
        for item in manifest.get("render", {}).get("module_sources", [])
        if item.get("component_id") == "wireguard-gw"
    ]
    result = {item.get("instance_id"): item.get("module_name") for item in modules}
    if (
        len(result) != len(modules)
        or set(result) != {component_instance_id(row) for row in rows}
        or len(set(result.values())) != len(result)
        or any(
            not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name)
            for name in result.values()
        )
    ):
        raise RuntimeError("WireGuard requires unambiguous rendered module identities; rerender")
    return result


def _output(outputs: Mapping[str, Any], instance: str, name: str) -> str:
    item = outputs.get(component_output_root_name(instance, name), {})
    return str(item.get("value") or "") if isinstance(item, Mapping) else ""


def wireguard_host_outputs(outputs: Mapping[str, Any], instance: str) -> dict[str, str]:
    result = {
        name: _output(outputs, instance, source)
        for name, source in {
            "instance_id": "instance_id",
            "boot_disk_id": "boot_disk_id",
            "public_ip": "public_ip",
            "public_allocation_id": "public_ip_allocation_id",
        }.items()
    }
    if not all(result.values()):
        raise RuntimeError(
            "WireGuard host identity requires instance, disk and address outputs; "
            "complete the new SecretStash deployment"
        )
    return result


def remote_access(
    ssh: Any, command: str, payload: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    from .wireguard_clients import _ssh_command_parts

    argv = _ssh_command_parts(
        ssh_user=ssh.ssh_user,
        public_ip=ssh.public_ip,
        ssh_private_key=ssh.private_key,
        ssh_known_hosts_file=ssh.known_hosts,
        remote_parts=["sudo", "-n", "/usr/local/sbin/nebius-wireguard-access", command],
    )
    try:
        return subprocess.run(
            argv,
            input=json.dumps(payload or {}),
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError("WireGuard authenticated access probe did not complete") from None


def verify_guest(connection: Mapping[str, Any], pair: Mapping[str, str]) -> None:
    with temporary_ssh_access(connection, pair) as ssh:
        result = remote_access(ssh, "verify")
    try:
        if result.returncode or json.loads(result.stdout) != {
            "generation": connection["generation"],
            "host_public_key": connection["host_public_key"],
        }:
            raise ValueError
    except ValueError:
        raise RuntimeError(
            "WireGuard pinned SSH, host identity or gateway readiness verification failed"
        ) from None


def wait_for_guest(
    connection: Mapping[str, Any], pair: Mapping[str, str], *, timeout: float = 900
) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            verify_guest(connection, pair)
            return
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "WireGuard setup did not become ready before the deployment deadline; its guest setup service will retry, then resume deploy"
                ) from None
            time.sleep(min(5, max(0, deadline - time.monotonic())))


def connection_payload(value: Mapping[str, Any]) -> dict[str, str]:
    return {"connection": json.dumps(value, sort_keys=True, separators=(",", ":"))}


def publish_connection(
    api: Any, journal: WireGuardJournal, key: str, connection: dict[str, Any]
) -> None:
    record = journal.value
    secret = record["resources"]["connection"]["id"]
    publications = record.setdefault("publications", {})
    if key not in publications:
        prior, _ = api.payload(secret)
        publications[key] = {"expected": prior, "connection": connection}
        journal.save()
    if publications[key]["connection"] != connection:
        raise RuntimeError("WireGuard publication differs from its frozen operation")
    version = api.version(journal, record, key, secret, lambda: connection_payload(connection))
    journal.assert_held()
    api.publish(journal, secret, version, publications[key]["expected"])
    record["connection"] = connection
    record["connection_version"] = version
    journal.save()


class WireGuardHostIdentities:
    """One owner for install/resume; no private inputs and no preview mutations."""

    def __init__(self, executor: Any, *, settings: Any = None) -> None:
        self.executor = executor
        self.settings = settings
        from .wireguard_dependencies import access_dependencies, access_owner_rows

        self.dependencies = access_dependencies(executor.config)
        self.owners = sorted(access_owner_rows(executor.config), key=component_instance_id)
        self.rows = copy.deepcopy(wireguard_rows(executor.config))
        self.modules = _modules(executor.manifest, self.rows)
        self.env: dict[str, str] = {}
        self.journals: dict[str, WireGuardJournal] = {}
        self.stack = ExitStack()
        self.api: Any = None
        self.prepared = False
        self.request_failure: WireGuardRequestError | None = None

    def __enter__(self) -> WireGuardHostIdentities:
        paths = getattr(self.executor, "report_paths", self.executor.paths)
        self.source_before = paths.config_path.read_bytes() if self.rows else b""
        self.token = CURRENT_WIREGUARD_IDENTITIES.set(self)
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            for journal in reversed(list(self.journals.values())):
                journal.release()
        finally:
            self.stack.close()
            self.env.clear()
            CURRENT_WIREGUARD_IDENTITIES.reset(self.token)

    def prepare(self) -> None:
        if self.request_failure:
            raise WireGuardRequestError(
                self.request_failure.status_code, context=self.request_failure.context
            )
        try:
            self._prepare()
        except WireGuardRequestError as error:
            # Preserve safe context without redispatching an uncertain cloud write.
            self.request_failure = error
            raise

    def _prepare(self) -> None:
        if self.prepared:
            self.apply_runtime_inputs(self.executor)
            return
        if not self.owners:
            self.prepared = True
            return
        from .object_storage_transport import object_storage_transport
        from .terraform_backend import backend_settings_from_config
        from .wireguard_foundation import assert_ownership, ownership, reconcile_access_root

        executor = self.executor
        from .wireguard_networking import network_asset

        for row in self.rows:
            network_asset(executor.manifest, component_instance_id(row))
        executor.runtime_env = {
            **executor.cli._terraform_runtime_env(executor.config),
            **executor.runtime_env,
        }
        settings = self.settings or backend_settings_from_config(executor.config)
        transport = self.stack.enter_context(
            object_storage_transport(settings, extra_env=executor.runtime_env)
        )
        if not executor.options.dry_run:
            executor.lease.assert_held()
            self.api = self.stack.enter_context(
                wireguard_api(credentials_file=os.environ.get("CXCLI_WIREGUARD_CREDENTIALS_FILE"))
            )
        nebius = to_plain_data(executor.config)["client_info"]["nebius"]
        for row in self.owners:
            validate_access(row)
            instance = component_instance_id(row)
            project = str(row.get("inputs", {}).get("parent_id") or nebius["project_id"])
            if project != settings.project_id or project != nebius["project_id"]:
                raise RuntimeError("WireGuard lifecycle project differs from backend/configuration")
            journal = self.journals.get(instance)
            if journal is None:
                journal = WireGuardJournal(transport, project, instance)
                self.journals[instance] = journal
            if executor.options.dry_run:
                if not journal.value.get("bootstrap"):
                    raise RuntimeError(
                        "WireGuard SecretStash bootstrap references are unresolved; deploy prepares them before the final plan"
                    )
            else:
                journal.acquire()
                self.api.validate_group(
                    row["access"]["admin_group_id"], nebius["tenant_id"], project
                )
                broad = self.api.direct_broad_payload_grants(
                    row["access"]["admin_group_id"], nebius["tenant_id"], project
                )
                for permit in broad:
                    executor.cli.progress_console.print(
                        f"WireGuard operator group has a direct {permit['scope']}-wide "
                        f"{permit['role']} grant ({permit['id']}). It includes host and "
                        "unrelated secret payloads; narrow WG grants do not restrict it. "
                        "Existing grants are unchanged. Inherited roles may grant further access.",
                        markup=False,
                    )
                record = journal.value
                if not record:
                    outputs = executor.cli.terraform_output_json(
                        executor.paths.infra_dir, extra_env=executor.runtime_env, initialize=False
                    )
                    if _output(outputs, instance, "instance_id"):
                        raise RuntimeError(
                            "Existing WireGuard VM has no SecretStash generation; this contract supports new deployments only"
                        )
                    from .wireguard_foundation import assert_fresh_access_state

                    assert_fresh_access_state(self, row)
                    if row["access"].get("generation") or row["access"].get("connection_secret_id"):
                        raise RuntimeError(
                            "Existing WireGuard references have no matching Terraform ownership journal"
                        )
                    record.update(
                        ownership=ownership(settings, row),
                        schema=SCHEMA,
                        project_id=project,
                        component_id=instance,
                        generation=str(uuid.uuid4()),
                        admin_group_id=row["access"]["admin_group_id"],
                        phase="preparing",
                    )
                    journal.save()
                assert_ownership(record, settings, row)
                if record["admin_group_id"] != row["access"]["admin_group_id"]:
                    raise RuntimeError(
                        "WireGuard admin group changed; explicit access reassignment is required"
                    )
                if record["phase"] == "retired" and row["access"].get("lifecycle") != "retained":
                    raise RuntimeError(
                        "WireGuard generation is retired; use a new component instance for a fresh deployment"
                    )
                if row["access"].get("generation") not in {None, record["generation"]}:
                    raise RuntimeError("WireGuard source references a different generation")
                if record.get("rotation", {}).get("phase") not in {None, "complete"}:
                    raise RuntimeError(
                        "WireGuard login rotation is incomplete; resume --rotate-login-key first"
                    )
                if row["access"].get("lifecycle") == "retained" and (
                    record["phase"] != "retired" or not record.get("retirement_verified")
                ):
                    raise RuntimeError("Retained access owner lacks verified retirement")
                if row["access"].get("lifecycle") == "retained":
                    vm = record.get("connection", {}).get("instance_id")
                    if vm:
                        self.api.verify_instance_absent(vm)
            assert_ownership(journal.value, settings, row)
        # Every affected gateway journal is held before Terraform can change access.
        if not executor.options.dry_run:
            reconcile_access_root(self)
        for row in self.rows:
            instance = component_instance_id(row)
            journal = self.journals[instance]
            record = journal.value
            if not executor.options.dry_run and "bootstrap" not in record:
                initial = {
                    "schema": SCHEMA,
                    "project_id": record["project_id"],
                    "component_id": instance,
                    "generation": record["generation"],
                    "state": "PENDING",
                }

                def initial_connection(value: Mapping[str, Any] = initial) -> dict[str, str]:
                    return connection_payload(value)

                for kind in ("login", "host", "connection"):
                    secret = record["resources"][kind]["id"]
                    factory = initial_connection if kind == "connection" else generate_host_identity
                    version = self.api.version(journal, record, "initial-" + kind, secret, factory)
                    self.api.publish(journal, secret, version, "")
                login_version, login_pair = self.api.payload(record["resources"]["login"]["id"])
                host_version, host_pair = self.api.payload(record["resources"]["host"]["id"])
                validate_pair(login_pair)
                validate_pair(host_pair)
                record["bootstrap"] = {
                    "generation": record["generation"],
                    "service_account_id": record["resources"]["account"]["id"],
                    "host_secret_id": record["resources"]["host"]["id"],
                    "host_version_id": host_version,
                    "host_public_key": host_pair["ed25519_public"],
                    "login_public_key": login_pair["ed25519_public"],
                }
                record["login_version_id"] = login_version
                record["ssh_user"] = row["inputs"]["ssh_user_name"]
                record["phase"] = "prepared"
                journal.save()
            if journal.value["ssh_user"] != row["inputs"]["ssh_user_name"]:
                raise RuntimeError("WireGuard bootstrap username is immutable for this generation")
            self.env[f"TF_VAR_{self.modules[instance]}_access_bootstrap"] = json.dumps(
                journal.value["bootstrap"], sort_keys=True
            )
        from .wireguard_bootstrap import prepare_bootstraps

        prepare_bootstraps(self)
        self.prepared = True
        self.apply_runtime_inputs(executor)

    def apply_runtime_inputs(self, executor: Any) -> None:
        from .wireguard_dependencies import access_dependencies, access_owner_rows

        if (
            not self.prepared
            or sorted(access_owner_rows(executor.config), key=component_instance_id) != self.owners
            or access_dependencies(executor.config) != self.dependencies
            or wireguard_rows(executor.config) != self.rows
            or _modules(executor.manifest, self.rows) != self.modules
        ):
            raise RuntimeError(
                "WireGuard public inputs differ from prepared component/module identity"
            )
        executor.runtime_env.update(self.env)

    def check_plan(self, plan: Mapping[str, Any]) -> None:
        for change in plan.get("resource_changes", []):
            if "delete" not in change.get("change", {}).get("actions", []):
                continue
            before = change.get("change", {}).get("before") or {}
            known = any(
                change.get("address", "").startswith(f"module.{module}.")
                for module in self.modules.values()
            )
            if (
                known
                or before.get("labels", {}).get("component") == "wireguard-gw"
                or "/etc/nebius-wireguard/access.json"
                in str(before.get("cloud_init_user_data", ""))
            ):
                raise RuntimeError(
                    "WireGuard deletion/replacement requires explicit destroy to retire its connection first; retain its configuration until destroy completes"
                )

    def complete(self) -> None:
        if self.executor.options.dry_run or not self.owners:
            return
        from .wireguard_foundation import reconcile_access_root

        reconcile_access_root(self, verify_only=True)
        if not self.rows:
            return
        executor = self.executor
        executor.lease.assert_held()
        outputs = executor.cli.terraform_output_json(
            executor.paths.infra_dir, extra_env=executor.runtime_env, initialize=False
        )
        for row in self.rows:
            instance = component_instance_id(row)
            journal = self.journals[instance]
            journal.assert_held()
            record = journal.value
            own = wireguard_host_outputs(outputs, instance)
            if record.get("connection") and any(
                record["connection"].get(k) != v for k, v in own.items()
            ):
                raise RuntimeError(
                    "WireGuard physical binding changed; refusing host identity reuse"
                )
            self._verify_compute(own, record)
            connection = {
                "schema": SCHEMA,
                "state": "READY",
                "project_id": record["project_id"],
                "component_id": instance,
                "generation": record["generation"],
                **own,
                "ssh_user": record["ssh_user"],
                "host_public_key": record["bootstrap"]["host_public_key"],
                "login_secret_id": record["resources"]["login"]["id"],
                "login_version_id": record["login_version_id"],
            }
            validate_connection(
                connection,
                project=record["project_id"],
                component=instance,
                generation=record["generation"],
            )
            _, pair = self.api.payload(
                connection["login_secret_id"], connection["login_version_id"]
            )
            wait_for_guest(connection, pair)
            from .wireguard_networking import network_asset, reconcile_guest

            record["network_revision"] = reconcile_guest(
                connection, pair, network_asset(executor.manifest, instance)
            )
            self._verify_compute(own, record)
            if record.get("connection") != connection:
                publish_connection(self.api, journal, "ready", connection)
            record["phase"] = "ready"
            journal.save()
        self._publish_references()

    def _verify_compute(self, own: Mapping[str, str], record: Mapping[str, Any]) -> None:
        self.api.verify_instance_binding(own, record)

    def _publish_references(self) -> None:
        import yaml

        from .config_loader import _write_text_atomic
        from .deployment_cli import render_publication_lock

        path = self.executor.report_paths.config_path
        with render_publication_lock(config=self.executor.config, paths=self.executor.report_paths):
            before = path.read_bytes()
            if before != self.source_before:
                raise RuntimeError(
                    "Source configuration changed during WireGuard deployment; rerender before publishing references"
                )
            source = yaml.safe_load(before)
            rows = wireguard_rows(source)
            expected = {component_instance_id(row): row for row in self.rows}
            if {component_instance_id(row) for row in rows} != set(expected):
                raise RuntimeError(
                    "WireGuard source selection changed before reference publication"
                )
            for row in rows:
                instance = component_instance_id(row)
                if (
                    row["access"]["admin_group_id"]
                    != expected[instance]["access"]["admin_group_id"]
                ):
                    raise RuntimeError(
                        "WireGuard source admin group changed before reference publication"
                    )
                record = self.journals[instance].value
                if row["access"].get("generation") not in {None, record["generation"]}:
                    raise RuntimeError(
                        "WireGuard source generation changed before reference publication"
                    )
                row["access"].update(
                    connection_secret_id=record["resources"]["connection"]["id"],
                    generation=record["generation"],
                )
            # wireguard_rows/to_plain_data return copies; apply only the owned access fields.
            changes = {component_instance_id(row): row["access"] for row in rows}
            for row in source["infra"]["components"]:
                if (
                    component_type_id(row) == "wireguard-gw"
                    and component_instance_id(row) in changes
                ):
                    row["access"] = changes[component_instance_id(row)]
            if path.read_bytes() != before:
                raise RuntimeError("WireGuard source changed concurrently")
            _write_text_atomic(
                path, yaml.safe_dump(source, sort_keys=False), file_mode=path.stat().st_mode & 0o777
            )
            self.source_before = path.read_bytes()
