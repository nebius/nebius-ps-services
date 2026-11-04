"""Tailscale owner within the ordinary project Terraform deployment lifecycle."""

from __future__ import annotations

import copy
import hashlib
import json
import time
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from http.client import HTTPException
from pathlib import Path
from typing import Any

from .component_sources import component_output_root_name
from .object_storage_transport import object_storage_transport
from .ssh_host_identity import (
    ComputeHostIdentityClient,
    compute_instance,
    fresh_identity,
    recovered_identity,
)
from .tailscale_api import ApiError, TailscaleAPI
from .tailscale_config import desired, members, rows, secret, validate
from .tailscale_enrollment import KeyStore, resume
from .tailscale_ssh import Guest, asset
from .tailscale_state import Journal
from .terraform_backend import backend_settings_from_config

CURRENT_TAILSCALE: ContextVar[Any] = ContextVar("cxcli_tailscale", default=None)


def outputs(raw: dict[str, Any], instance: str) -> dict[str, Any]:
    return raw.get(component_output_root_name(instance, "members"), {}).get("value", {})


def resources(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = []

    def walk(node: dict[str, Any]) -> None:
        result.extend(node.get("resources", []))
        for child in node.get("child_modules", []):
            walk(child)

    walk(payload.get("values", payload.get("planned_values", {})).get("root_module", {}))
    return result


def disruptive_vm_change(resource: dict[str, Any]) -> bool:
    if resource.get("type") != "nebius_compute_v1_instance":
        return False
    change = resource.get("change", {})
    before, after = change.get("before") or {}, change.get("after") or {}
    return "delete" in change.get("actions", []) or (
        change.get("actions") == ["update"]
        and any(
            before.get(k) != after.get(k)
            for k in ("resources", "boot_disk", "network_interfaces", "cloud_init_user_data")
        )
    )


def check_plan(payload: dict[str, Any], modules: set[str]) -> None:
    destructive = set()
    for change in payload.get("resource_changes", []):
        if "delete" not in change.get("change", {}).get("actions", []) and not disruptive_vm_change(
            change
        ):
            continue
        address = change.get("address", "")
        for module in modules:
            if address.startswith(f"module.{module}."):
                if disruptive_vm_change(change):
                    destructive.add(address)
                # Deleting a router via config removal must use its retirement owner.
                if change.get("type") == "nebius_compute_v1_instance" and change.get(
                    "change", {}
                ).get("actions") == ["delete"]:
                    raise RuntimeError(
                        "Retire Tailscale gateways with ordinary destroy before removing members"
                    )
    if len(destructive) > 1:
        raise RuntimeError(
            "Replacing or disrupting multiple Tailscale members in one plan would remove HA; stage per-member changes"
        )


def guest_ready(report: dict[str, Any], intent: dict[str, Any]) -> bool:
    return bool(
        report.get("online")
        and report.get("forwarding")
        and report.get("tailnet") == intent["tailnet"]
        and report.get("node_id")
        and sorted(report.get("tags", [])) == sorted(intent["tags"])
        and sorted(report.get("routes", [])) == sorted(intent["routes"])
        and report.get("version") == intent["version"]
        and report.get("snat")
        and report.get("stateful_filtering")
        and report.get("netfilter")
        and not report.get("accept_routes")
        and not report.get("accept_dns")
        and not report.get("tailscale_ssh")
        and not report.get("expired")
    )


def readiness(
    api: Any, report: dict[str, Any], intent: dict[str, Any], *, approve: bool = False
) -> bool:
    if not guest_ready(report, intent):
        return False
    if api is None:
        # The authenticated guest's control-plane netmap includes approved AllowedIPs.
        # No expiry (or Go's zero time) is distinct from an enrollment key's expiry.
        expiry = report.get("key_expiry")
        return set(report.get("approved_routes", [])) == set(intent["routes"]) and expiry in {
            None,
            "0001-01-01T00:00:00Z",
        }
    device = api.device(report["node_id"])
    if report["node_id"] not in {device.get("id"), device.get("nodeId")} or set(
        device.get("tags", [])
    ) != set(intent["tags"]):
        raise RuntimeError("Tailscale API device identity differs from the owned member")
    route = (
        api.approve(report["node_id"], intent["routes"])
        if approve
        else api.routes(report["node_id"])
    )
    return bool(
        set(route.get("enabledRoutes", [])) == set(intent["routes"])
        and device.get("authorized")
        and device.get("keyExpiryDisabled")
    )


class TailscaleDeployment:
    def __init__(self, executor: Any, *, settings: Any = None) -> None:
        self.executor, self.settings = executor, settings
        self.stack = ExitStack()
        self.rows = rows(executor.config)
        self.runtime: dict[str, str] = {}
        self.prepared = False
        self.journal: Journal | None = None

    def __enter__(self) -> TailscaleDeployment:
        self.token = CURRENT_TAILSCALE.set(self)
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            self.stack.close()
        finally:
            CURRENT_TAILSCALE.reset(self.token)

    def assert_held(self) -> None:
        if self.executor.options.dry_run or self.executor.lease is None:
            raise RuntimeError("Tailscale mutation requires the shared project execution lease")
        self.executor.lease.assert_held()

    def open(self) -> Journal:
        if self.journal is None:
            settings = self.settings or backend_settings_from_config(self.executor.config)
            transport = self.stack.enter_context(
                object_storage_transport(settings, extra_env=self.executor.runtime_env)
            )
            self.journal = Journal(transport, settings.key, self.assert_held)
            if not self.executor.options.dry_run:
                self.journal.acquire()
                self.stack.callback(self.journal.release)
        return self.journal

    def raw_outputs(self) -> dict[str, Any]:
        return self.executor.cli.terraform_output_json(
            self.executor.paths.infra_dir, extra_env=self.executor.runtime_env, initialize=False
        )

    def prepare(self) -> None:
        if self.prepared or not self.rows:
            self.prepared = True
            return
        journal = self.open()
        raw = self.raw_outputs()
        state = self.executor.cli.terraform_show_json(
            self.executor.paths.infra_dir, extra_env=self.executor.runtime_env, initialize=False
        )
        all_resources = resources(state)
        for row in self.rows:
            cfg = validate(row)
            if cfg["stage"] == "active":
                from .tailscale_onboarding import assert_activation

                assert_activation(row)
            instance = row["instance_id"]
            item = asset(self.executor.manifest, instance)
            module = item["module_name"]
            record = journal.value["gateways"].setdefault(
                instance,
                {
                    "tailnet": cfg["tailnet"],
                    "members": {},
                    "lifecycle": "provisioning",
                    "activation_started": False,
                },
            )
            if cfg["stage"] == "infrastructure" and record.get("activation_started"):
                raise RuntimeError("An activated gateway cannot return to infrastructure stage")
            if cfg["stage"] == "active" and not record.get("infrastructure_ready"):
                raise RuntimeError("Complete an infrastructure-stage deployment before activation")
            if not record.get("activation_started"):
                record["tailnet"] = cfg["tailnet"]
            if (
                record["tailnet"] != cfg["tailnet"]
                or record.get("retired")
                or record.get("retiring")
            ):
                raise RuntimeError("Tailscale owner cannot be rebound or reused after retirement")
            keys, bootstraps = {}, {}
            current = outputs(raw, instance)
            for member in members(row):
                from .nat_gateway_sdk import NatGatewaySDK

                sizing = {
                    **row["inputs"],
                    **{
                        key: value
                        for key, value in row["inputs"]
                        .get("member_overrides", {})
                        .get(member, {})
                        .items()
                        if value is not None
                    },
                }
                with NatGatewaySDK(row["inputs"]["parent_id"]) as capacity:
                    preset = capacity.preset_resources(sizing["platform"], sizing["preset"])
                    if preset["gpu_count"] != 0:
                        raise ValueError(
                            "Tailscale gateways require a currently available CPU-only preset"
                        )
                known = record["members"].setdefault(member, {})
                selected = [
                    r
                    for r in all_resources
                    if r.get("type") == "nebius_compute_v1_instance"
                    and r.get("address", "").startswith(
                        f'module.{module}.module.member["{member}"].'
                    )
                ]
                if len(selected) > 1:
                    raise RuntimeError("Ambiguous Tailscale member in Terraform state")
                identity = (
                    selected[0].get("values", {}).get("id", "")
                    if selected
                    else current.get(member, {}).get("instance_id", "")
                )
                with ComputeHostIdentityClient(row["inputs"]["parent_id"]) as sdk:
                    if identity:
                        vm = compute_instance(sdk, identity)
                        keys[member] = recovered_identity(vm)
                        body = vm.spec.cloud_init_user_data
                        if not keys[member] or (
                            known.get("instance_id")
                            and known["instance_id"] != identity
                            and not known.get("replacement")
                        ):
                            raise RuntimeError(
                                "Tailscale VM identity changed; explicit staged replacement recovery required"
                            )
                        if selected and selected[0]["values"].get("cloud_init_user_data") != body:
                            raise RuntimeError(
                                "Tailscale Compute bootstrap differs from Terraform state"
                            )
                        sha = hashlib.sha256(body.encode()).hexdigest()
                        if known.get("bootstrap_sha256") and known["bootstrap_sha256"] != sha:
                            raise RuntimeError("Tailscale immutable bootstrap digest changed")
                        bootstraps[member] = body
                        if not known.get("replacement"):
                            known["instance_id"] = identity
                        known["bootstrap_sha256"] = sha
                    else:
                        keys[member] = fresh_identity(
                            self.executor.report_paths,
                            row["inputs"]["parent_id"],
                            instance + "/" + member,
                            preview=self.executor.options.dry_run,
                            namespace="tailscale-host-keys",
                        )
                    if (
                        known.get("host_public_key")
                        and known["host_public_key"] != keys[member]["ed25519_public"]
                    ):
                        raise RuntimeError(
                            "Tailscale host identity differs from its prepared owner"
                        )
                    known["host_public_key"] = keys[member]["ed25519_public"]
            if (
                cfg["stage"] == "active"
                and not self.executor.options.dry_run
                and any(not own.get("node_id") for own in record["members"].values())
            ):
                if cfg.get("auth", {}).get("mode", "oauth") == "oauth":
                    api = self.api(row)
                    if api is None:
                        raise RuntimeError(
                            "Configure scoped OAuth credentials before provisioning Tailscale gateways"
                        )
                    pending = [
                        own["enrollment"]
                        for own in record["members"].values()
                        if own.get("enrollment") and not own["enrollment"].get("key_retired")
                    ]
                    if pending:
                        for op in pending:
                            api.bind_credential(op["credential"])
                            api.request("GET", api.tailnet + "/devices?fields=default")
                    else:
                        api.request("GET", api.tailnet + "/devices?fields=default")
                else:
                    for member, own in record["members"].items():
                        if not own.get("node_id") and not own.get("enrollment"):
                            reference = (
                                cfg.get("auth", {}).get("member_key_refs", {}).get(member, "")
                            )
                            if not secret(
                                reference, base=self.executor.report_paths.project_dir
                            ).startswith("tskey-auth-"):
                                raise RuntimeError("Administrator-issued member key is unavailable")
            self.runtime[f"TF_VAR_{module}_ssh_host_keys"] = json.dumps(keys)
            if bootstraps:
                self.runtime[f"TF_VAR_{module}_bootstrap_user_data"] = json.dumps(bootstraps)
        if not self.executor.options.dry_run:
            journal.save()
        self.prepared = True
        self.apply_runtime_inputs(self.executor)

    def apply_runtime_inputs(self, executor: Any) -> None:
        if rows(executor.config) != self.rows:
            raise RuntimeError("Tailscale runtime intent changed during deployment")
        executor.runtime_env.update(self.runtime)

    def check_plan(self, payload: dict[str, Any]) -> None:
        check_plan(
            payload,
            {asset(self.executor.manifest, r["instance_id"])["module_name"] for r in self.rows},
        )
        for change in payload.get("resource_changes", []):
            old = (change.get("change", {}).get("before") or {}).get("labels") or {}
            if (
                old.get("cxcli-component") == "tailscale-gw"
                and "delete" in change.get("change", {}).get("actions", [])
                and not any(
                    change.get("address", "").startswith(
                        "module."
                        + asset(self.executor.manifest, row["instance_id"])["module_name"]
                        + "."
                    )
                    for row in self.rows
                )
            ):
                raise RuntimeError(
                    "Retain Tailscale configuration until ordinary destroy retires its devices"
                )
        # Record only bootstrap digests, never secret-bearing user data.
        if not self.rows or self.executor.options.dry_run:
            return
        journal = self.open()
        for row in self.rows:
            module = asset(self.executor.manifest, row["instance_id"])["module_name"]
            for change in payload.get("resource_changes", []):
                if change.get("type") != "nebius_compute_v1_instance":
                    continue
                for member in members(row):
                    if change.get("address", "").startswith(
                        f'module.{module}.module.member["{member}"].'
                    ):
                        actions = change.get("change", {}).get("actions", [])
                        if row["tailscale"]["stage"] == "infrastructure" and (
                            disruptive_vm_change(change) or "delete" in actions
                        ):
                            raise RuntimeError(
                                "Retire and recreate infrastructure before changing its immutable cloud identity"
                            )
                        if disruptive_vm_change(change) and "delete" not in actions:
                            record = journal.value["gateways"][row["instance_id"]]
                            with ComputeHostIdentityClient(row["inputs"]["parent_id"]) as sdk:
                                guests = self.guests(
                                    row, outputs(self.raw_outputs(), row["instance_id"]), sdk
                                )
                                self.assert_peer(row, member, guests, record, self.api(row))
                        if "delete" in actions:
                            record = journal.value["gateways"][row["instance_id"]]
                            own = record["members"][member]
                            api = self.api(row)
                            if api is None or not own.get("node_id"):
                                raise RuntimeError(
                                    "Staged replacement requires an owned device and scoped API credentials"
                                )
                            with ComputeHostIdentityClient(row["inputs"]["parent_id"]) as sdk:
                                guests = self.guests(
                                    row, outputs(self.raw_outputs(), row["instance_id"]), sdk
                                )
                                self.assert_peer(row, member, guests, record, api)
                            if not own.get("replacement"):
                                own["replacement"] = {
                                    "node_id": own["node_id"],
                                    "instance_id": own["instance_id"],
                                }
                        body = (change.get("change", {}).get("after") or {}).get(
                            "cloud_init_user_data"
                        )
                        if isinstance(body, str):
                            known = journal.value["gateways"][row["instance_id"]]["members"][member]
                            sha = hashlib.sha256(body.encode()).hexdigest()
                            if known.get("bootstrap_sha256") not in {None, sha}:
                                raise RuntimeError("Tailscale plan changed immutable bootstrap")
                            known["bootstrap_sha256"] = sha
        journal.save()

    def api(self, row: dict[str, Any]) -> TailscaleAPI | None:
        cfg = copy.deepcopy(validate(row))
        ref = cfg.get("auth", {}).get("secretstash")
        if ref and ref.get("component_instance"):
            from .tailscale_dependencies import resolve_secret

            ref["secret_id"] = resolve_secret(ref, self.raw_outputs())
        return (
            TailscaleAPI(cfg, base_dir=self.executor.report_paths.project_dir)
            if cfg.get("auth", {}).get("client_secret_ref") or ref
            else None
        )

    def guests(self, row: dict[str, Any], output: dict[str, Any], sdk: Any) -> dict[str, Guest]:
        key = validate(row).get("auth", {}).get("ssh_private_key_file")
        private = Path(key).expanduser() if key else None
        if private and not private.is_absolute():
            private = self.executor.report_paths.project_dir / private
        if set(output) != set(members(row)):
            raise RuntimeError("Tailscale Terraform member outputs are incomplete")
        return {
            m: Guest(sdk, self.executor.paths, output[m], row["inputs"], private)
            for m in members(row)
        }

    def assert_peer(
        self,
        row: dict[str, Any],
        member: str,
        guests: dict[str, Any],
        record: dict[str, Any],
        api: Any,
    ) -> None:
        if len(guests) == 1:
            return
        for peer, guest in guests.items():
            if peer == member:
                continue
            own = record["members"][peer]
            intent = own.get("intent") or desired(row, peer)
            observation = guest.call("status")
            if observation.get("node_id") != own.get("node_id") or not readiness(
                api, observation, intent
            ):
                raise RuntimeError("Tailscale peer is not ready; stop rolling changes")

    def complete(self) -> None:
        if not self.rows or self.executor.options.dry_run:
            return
        journal = self.open()
        raw = self.raw_outputs()
        reports: list[dict[str, Any]] = []
        clients: list[dict[str, Any]] = []
        self.write_report(
            [{"status": "PENDING", "reason": "Current deployment verification in progress"}]
        )
        for row in self.rows:
            cfg, instance = validate(row), row["instance_id"]
            if cfg["stage"] == "active":
                from .tailscale_onboarding import assert_activation

                assert_activation(row)
            output = outputs(raw, instance)
            record = journal.value["gateways"][instance]
            if cfg["stage"] == "active" and not record.get("infrastructure_ready"):
                raise RuntimeError(
                    "Complete infrastructure setup in a previous deployment before activation"
                )
            with ComputeHostIdentityClient(row["inputs"]["parent_id"]) as sdk:
                from .tailscale_network import validate_live

                if not output:
                    raise RuntimeError("Tailscale cloud outputs are missing")
                first = next(iter(output.values()))
                validate_live(sdk, first["network_id"], first["subnet_id"], cfg["routes"])
                guests = self.guests(row, output, sdk)
                if cfg["stage"] == "infrastructure":
                    reports.extend(self.complete_infrastructure(row, output, record, guests))
                    clients.append(
                        {
                            "component": instance,
                            "status": "NOT_RUN",
                            "reason": "Awaiting administrator handoff",
                        }
                    )
                    self.write_report(reports, clients)
                    continue
                api = self.api(row)
                if not record.get("activation_started"):
                    from .tailscale_onboarding import fingerprint

                    checkpoint = record["infrastructure_ready"]
                    if checkpoint["members"] != {
                        m: {
                            k: output[m][k]
                            for k in ("instance_id", "boot_disk_id", "public_allocation_id")
                        }
                        for m in members(row)
                    }:
                        raise RuntimeError(
                            "Infrastructure identity changed before activation; repeat infrastructure verification"
                        )
                    record.update(
                        activation_started=True,
                        lifecycle="activating",
                        accepted_intent=fingerprint(row),
                    )
                    journal.save()
                for member, guest in guests.items():
                    journal.assert_held()
                    own = record["members"][member]
                    if record.get("retiring") or record.get("retired"):
                        raise RuntimeError("Tailscale owner is fenced for retirement")
                    replacement = own.get("replacement")
                    if (
                        own.get("instance_id") not in {None, output[member]["instance_id"]}
                        and not replacement
                    ):
                        raise RuntimeError("Tailscale cloud identity differs from the owned member")
                    for field in ("boot_disk_id", "public_allocation_id"):
                        if own.get(field) not in {None, output[member][field]} and not replacement:
                            raise RuntimeError(
                                "Tailscale cloud binding changed outside a staged replacement"
                            )
                    guest.install(asset(self.executor.manifest, instance))
                    current, intent = guest.call("status"), desired(row, member)
                    if (
                        own.get("node_id")
                        and own["node_id"] != current.get("node_id")
                        and not replacement
                    ):
                        raise RuntimeError(
                            "Tailscale node identity changed; refusing automatic re-enrollment"
                        )
                    if (
                        current.get("node_id")
                        and current["node_id"] != own.get("node_id")
                        and not own.get("enrollment")
                    ):
                        raise RuntimeError(
                            "Unowned Tailscale node found on the VM; automatic adoption is forbidden"
                        )
                    disruptive = bool(current.get("routes")) and (
                        current.get("version") != intent["version"]
                        or current.get("routes") != sorted(intent["routes"])
                    )
                    if disruptive:
                        self.assert_peer(row, member, guests, record, api)
                    if (
                        replacement
                        and own.get("enrollment")
                        and (
                            own["enrollment"]["binding"]["instance_id"]
                            != output[member]["instance_id"]
                        )
                    ):
                        if own["enrollment"]["phase"] != "enrolled":
                            raise RuntimeError(
                                "Finish enrollment recovery before replacing a member"
                            )
                        previous = own["enrollment"]
                        KeyStore(self.executor.report_paths.project_dir).delete(previous)
                        own.setdefault("prior_enrollments", []).append(own.pop("enrollment"))
                        if current.get("node_id"):
                            if (
                                current["node_id"] != own.get("node_id")
                                or output[member]["boot_disk_id"]
                                != previous["binding"]["boot_disk_id"]
                            ):
                                raise RuntimeError(
                                    "Replacement guest identity is not the retained owned node"
                                )
                        else:
                            own.pop("node_id", None)
                    own.update(copy.deepcopy(output[member]))
                    own.update(
                        project_id=row["inputs"]["parent_id"], member=member, phase="reconciling"
                    )
                    journal.save()
                    if not own.get("node_id") or own.get("enrollment"):
                        result = resume(
                            journal,
                            own,
                            guest,
                            cfg,
                            intent,
                            api,
                            KeyStore(self.executor.report_paths.project_dir),
                        )
                    else:
                        journal.assert_held()
                        result = guest.call("reconcile", desired=intent)
                    status = "PENDING"
                    deadline = time.monotonic() + 45
                    while True:
                        journal.assert_held()
                        try:
                            if readiness(api, result, intent, approve=cfg.get("approval") == "api"):
                                status = "READY"
                                break
                        except ApiError as error:
                            if error.status not in {401, 403}:
                                raise
                            break
                        if time.monotonic() >= deadline or not result.get("online"):
                            break
                        time.sleep(3)
                        result = guest.call("status")
                        if result.get("node_id") != own.get("node_id"):
                            raise RuntimeError("Node identity changed while waiting for readiness")
                    own.update(phase=status, intent=intent)
                    journal.save()
                    reports.append(
                        {
                            "component": instance,
                            "member": member,
                            "status": status,
                            "node_id": result.get("node_id", ""),
                            "enrollment": {
                                k: own.get("enrollment", {}).get(k)
                                for k in ("id", "phase", "key_id")
                            },
                            "ha_configured": len(guests) == 2,
                        }
                    )
                    self.write_report(reports)
                    if status != "READY" and (disruptive or replacement):
                        raise RuntimeError(
                            "Updated member is not ready; remaining members were not changed"
                        )
                    if replacement and status == "READY":
                        if api is None:
                            raise RuntimeError(
                                "Retiring a replaced device requires scoped API credentials"
                            )
                        if replacement["node_id"] != own.get("node_id"):
                            api.delete_owned(replacement["node_id"], cfg["tags"])
                        own.pop("replacement", None)
                        journal.save()
                record["lifecycle"] = (
                    "active"
                    if all(own.get("phase") == "READY" for own in record["members"].values())
                    else "activating"
                )
                journal.save()
                verification = cfg.get("verification", {})
                if verification.get("targets") and all(
                    own.get("phase") == "READY" for own in record["members"].values()
                ):
                    from .tailscale_verify import verify

                    try:
                        evidence = verify(
                            cfg, record["members"], base=self.executor.report_paths.project_dir
                        )
                    except (RuntimeError, OSError, HTTPException):
                        evidence = {
                            "status": "PENDING",
                            "reason": "Client verification did not pass; run tailscale verify from a connected client",
                        }
                        if verification.get("required"):
                            self.write_report(
                                reports, clients + [{"component": instance, **evidence}]
                            )
                            raise RuntimeError(
                                "Required Tailscale client verification did not pass"
                            ) from None
                    clients.append({"component": instance, **evidence})
        self.write_report(reports, clients)
        if any(report["status"] not in {"READY", "INFRASTRUCTURE_READY"} for report in reports):
            raise RuntimeError(
                "Tailscale infrastructure exists, but tailnet readiness is PENDING; resolve approvals with tailscale doctor, then resume deploy"
            )

    def complete_infrastructure(
        self,
        row: dict[str, Any],
        output: dict[str, Any],
        record: dict[str, Any],
        guests: dict[str, Any],
    ) -> list[dict[str, Any]]:
        from .tailscale_cli import protected_json
        from .tailscale_onboarding import request

        journal = self.open()
        if record.get("activation_started") or any(
            own.get("enrollment") or own.get("node_id") for own in record["members"].values()
        ):
            raise RuntimeError("Infrastructure-only completion cannot demote an enrollment")
        reports = []
        for member, guest in guests.items():
            journal.assert_held()
            own = record["members"][member]
            for field in ("instance_id", "boot_disk_id", "public_allocation_id"):
                if own.get(field) not in {None, output[member][field]}:
                    raise RuntimeError("Infrastructure identity differs from the gateway owner")
            guest.install(asset(self.executor.manifest, row["instance_id"]))
            observed = guest.call("status")
            if observed.get("node_id") or observed.get("online"):
                raise RuntimeError("Unowned enrolled node blocks infrastructure-only completion")
            if observed.get("version") != desired(row, member)["version"] or not observed.get(
                "forwarding"
            ):
                raise RuntimeError("Gateway package or IP forwarding is not ready")
            own.update(
                copy.deepcopy(output[member]),
                project_id=row["inputs"]["parent_id"],
                member=member,
                phase="INFRASTRUCTURE_READY",
            )
            reports.append(
                {
                    "component": row["instance_id"],
                    "member": member,
                    "status": "INFRASTRUCTURE_READY",
                    "gateway_ready": False,
                    "next": "Run tailscale onboarding request, complete administrator handoff, then onboarding configure, render and deploy.",
                }
            )
        record.update(
            lifecycle="infrastructure-ready",
            activation_started=False,
            infrastructure_ready={
                "members": {
                    m: {
                        k: output[m][k]
                        for k in ("instance_id", "boot_disk_id", "public_allocation_id")
                    }
                    for m in members(row)
                }
            },
        )
        journal.save()
        request_row = copy.deepcopy(row)
        ref = request_row["tailscale"].get("auth", {}).get("secretstash")
        if ref and ref.get("component_instance"):
            from .tailscale_dependencies import resolve_secret

            ref["secret_id"] = resolve_secret(ref, self.raw_outputs())
        handoff = request(
            request_row,
            infrastructure={
                "status": "VERIFIED",
                "members": record["infrastructure_ready"]["members"],
            },
        )
        for paths in (self.executor.paths, self.executor.report_paths):
            protected_json(
                paths.reports_dir / (row["instance_id"] + "-tailscale-onboarding.json"), handoff
            )
        return reports

    def write_report(
        self, reports: list[dict[str, Any]], clients: list[dict[str, Any]] | None = None
    ) -> None:
        from datetime import UTC, datetime

        for paths in (self.executor.paths, self.executor.report_paths):
            paths.reports_dir.mkdir(parents=True, exist_ok=True)
            target = paths.reports_dir / "tailscale-report.json"
            from .tailscale_cli import protected_json

            protected_json(
                target,
                {
                    "schema": 1,
                    "observed_at": datetime.now(UTC).isoformat(),
                    "members": reports,
                    "gateway_ready": bool(reports)
                    and all(r.get("status") == "READY" for r in reports),
                    "onboarding_pending": any(
                        r.get("status") == "INFRASTRUCTURE_READY" for r in reports
                    ),
                    "client": clients
                    or [
                        {
                            "status": "PENDING",
                            "reason": "Run tailscale verify from a connected client",
                        }
                    ],
                },
            )


def guard_direct_apply(
    cli: Any, config: Any, paths: Any, runtime: dict[str, str], *, initialize: bool
) -> None:
    owner = CURRENT_TAILSCALE.get()
    if owner is not None and owner.rows and owner.prepared:
        owner.open().assert_held()
        return
    if rows(config) or any(
        (resource.get("values", {}).get("labels") or {}).get("cxcli-component") == "tailscale-gw"
        for resource in cli._terraform_state_resources(
            cli.terraform_show_json(paths.infra_dir, extra_env=runtime, initialize=initialize)
        )
        if resource.get("mode", "managed") == "managed"
    ):
        raise RuntimeError(
            "Tailscale infrastructure must use ordinary render/deploy with protected host identities"
        )


def assert_retirement_coverage(
    configured: list[dict[str, Any]],
    state: list[dict[str, Any]],
    manifest: dict[str, Any],
    gateways: dict[str, Any] | None = None,
) -> None:
    """Whole-root destroy must account for every rendered or journaled Tailscale owner."""
    selected = {row["instance_id"] for row in configured}
    sources = [
        s
        for s in manifest.get("render", {}).get("module_sources", [])
        if s.get("component_id") == "tailscale-gw"
    ]
    covered = {
        "module." + s["module_name"] + "." for s in sources if s.get("instance_id") in selected
    }
    missing = any(s.get("instance_id") not in selected for s in sources)
    missing |= any(
        name not in selected and not record.get("retired")
        for name, record in (gateways or {}).items()
    )
    missing |= any(
        (r.get("values", {}).get("labels") or {}).get("cxcli-component") == "tailscale-gw"
        and not any(r.get("address", "").startswith(prefix) for prefix in covered)
        for r in state
        if r.get("mode", "managed") == "managed"
    )
    if missing:
        raise RuntimeError(
            "Retain Tailscale component configuration for every rendered or owned gateway until ordinary destroy retires its devices"
        )


@contextmanager
def retire_for_destroy(config: Any, paths: Any, runtime: dict[str, str]):
    """Retire exact tailnet devices before ordinary Terraform removes their VMs."""
    from types import SimpleNamespace

    from . import cli
    from .deployment_cli import deployment_execution
    from .generated_manifest import load_generated_manifest, manifest_path_for_generated_dir

    configured = rows(config)
    state = cli.terraform_show_json(paths.infra_dir, extra_env=runtime, initialize=False)
    manifest = (
        load_generated_manifest(paths.generated_dir)
        if configured
        or manifest_path_for_generated_dir(paths.generated_dir).exists()
        or any(
            (r.get("values", {}).get("labels") or {}).get("cxcli-component") == "tailscale-gw"
            for r in resources(state)
        )
        else {}
    )
    assert_retirement_coverage(configured, resources(state), manifest)
    with deployment_execution(
        config=config,
        paths=paths,
        target_ref="project",
        operation_id="Tailscale retirement",
        bootstrap_backend=False,
    ) as lease:
        executor = SimpleNamespace(
            cli=cli,
            config=config,
            paths=paths,
            report_paths=paths,
            manifest=manifest,
            options=SimpleNamespace(dry_run=False),
            lease=lease,
            runtime_env=dict(runtime),
        )
        with TailscaleDeployment(executor) as owner:
            journal = owner.open()
            from .tailscale_retirement import confirm, prepare, retire_members

            # Refresh under the project lease; no stale pre-lock state may bind ownership.
            current_resources = resources(
                cli.terraform_show_json(
                    paths.infra_dir, extra_env=executor.runtime_env, initialize=False
                )
            )
            assert_retirement_coverage(
                configured, current_resources, manifest, journal.value["gateways"]
            )
            for row in configured:
                with ComputeHostIdentityClient(row["inputs"]["parent_id"]) as sdk:
                    output = prepare(owner, row, current_resources, sdk)
                    retire_members(owner, row, output, sdk)
            yield executor.runtime_env
            journal.assert_held()
            remaining = resources(
                cli.terraform_show_json(
                    paths.infra_dir, extra_env=executor.runtime_env, initialize=False
                )
            )
            for row in configured:
                with ComputeHostIdentityClient(row["inputs"]["parent_id"]) as sdk:
                    confirm(owner, row, remaining, sdk)
