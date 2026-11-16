"""Explicit retained-VM reconstruction, selected before old apply admission."""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import typer
from packaging.markers import default_environment
from packaging.tags import compatible_tags, cpython_tags

from .. import repair_bootstrap, repair_guest
from .. import repair_state as state
from . import ordinary_apply, repair_ssh
from .recovery import RepairBlocked, RepairWorkerPending
from .repair_cloud import CloudJournal, Quarantine, RetainedCloud, retained_binding
from .ssh_policy import require_vm_ha_ssh_policy
from .ssh_push import SSHPush
from .vm_ha_cloud import InstanceCloudState, instance_cloud_state


def dependency_environment(observed: dict) -> dict:
    """Build target tags on the controller without importing broken guest deps."""
    version = tuple(int(item) for item in observed["python"].split(".")[:2])
    machine = observed["machine"]
    libc, release = observed["libc"]
    if (
        observed["implementation"] != "cpython"
        or machine not in {"x86_64", "aarch64"}
        or libc != "glibc"
    ):
        raise RuntimeError("Repair requires a supported Ubuntu CPython gateway")
    major, minor = (int(item) for item in release.split(".")[:2])
    if major != 2 or minor < 17:
        raise RuntimeError("Repair gateway libc is outside the supported runtime")
    platforms = [f"manylinux_2_{number}_{machine}" for number in range(minor, 16, -1)]
    platforms += [f"manylinux2014_{machine}", f"linux_{machine}"]
    interpreter = "cp" + "".join(str(item) for item in version)
    tags = [
        *cpython_tags(version, abis=[interpreter], platforms=platforms),
        *compatible_tags(version, interpreter=interpreter, platforms=platforms),
    ]
    markers = dict(default_environment())
    markers.update(
        implementation_name="cpython",
        implementation_version=observed["python"],
        python_full_version=observed["python"],
        python_version=".".join(map(str, version)),
        platform_machine=machine,
        platform_python_implementation="CPython",
        platform_system="Linux",
        sys_platform="linux",
        os_name="posix",
    )
    return {**observed, "wheel_tags": [str(tag) for tag in tags], "markers": markers}


@dataclass
class Admission:
    manager: Any
    plan: Any
    local: dict
    path: Path
    instances: tuple
    ssh: Any
    targets: dict[str, str]
    bindings: dict[str, dict]
    wheel: Path
    artifact: Any
    intent: dict
    credential_plan: Any = None

    @property
    def root(self) -> Path:
        return (
            Path.home()
            / ".local/state/nebius-vpngw/repair"
            / self.intent["project"]
            / self.intent["gateway"]
        )


def inspect(
    *, manager: Any, plan: Any, local: dict, path: Path, instances: tuple, wheel: Path | None = None
) -> Admission:
    from .. import cli

    targets = (
        manager.discover_vm_ha_members(plan.gateway_group)
        if plan.vm_ha is not None
        else manager.discover_ordinary_gateway_members(plan.gateway_group)
    )
    if set(targets) != {item.hostname for item in instances}:
        raise RepairBlocked("repair-vm-missing")
    snapshot = (
        manager._vm_ha_ssh_preflight_snapshot
        if plan.vm_ha is not None
        else manager._ordinary_ssh_preflight_snapshot
    )
    bindings = {name: retained_binding(snapshot[name][0]) for name in targets}
    if any(value["project"] != manager.project_id for value in bindings.values()):
        raise RuntimeError("Repair target belongs to another project")
    vm_spec = local["gateway_group"].get("vm_spec", {})
    if plan.vm_ha is not None:
        assertions = repair_ssh.ha_bindings(manager, plan.gateway_group, path)
    else:
        assertions = manager.ordinary_ssh_trust_bindings(
            plan.gateway_group, retained_hosts=set(targets)
        )
    for assertion in assertions.values():
        assertion()
    retained = repair_ssh.bindings(manager, plan.gateway_group, snapshot)

    def recover(hostnames: frozenset[str]) -> dict:
        values = manager.recover_ordinary_ssh_host_keys(hostnames, spec=plan.gateway_group)
        return {
            name: replace(value, assert_current=retained[name]) for name, value in values.items()
        }

    scope = cli._vm_ha_ssh_trust_scope if plan.vm_ha is not None else cli._ordinary_ssh_trust_scope
    policy = require_vm_ha_ssh_policy(
        tuple(sorted(targets.items())),
        enrollment_hosts=(),
        retained_hosts=set(targets),
        management_key_path=manager._management_key_path,
        management_public_key=vm_spec.get("ssh_public_key"),
        require_management_key=plan.vm_ha is not None,
        trust_scope=scope(local, plan, project_id=manager.project_id),
        default_known_hosts_bindings=retained,
        allow_managed_repair=plan.vm_ha is None,
        persist_default_host_keys=False,
        host_identity_recovery=recover if plan.vm_ha is None else None,
    )
    ssh = SSHPush(ssh_policy=policy)
    artifact = ssh.resolve_vm_ha_agent_artifact() if plan.vm_ha is not None else None
    selected = artifact.path if artifact is not None else wheel
    if selected is None:
        raise RuntimeError("Repair requires the exact selected release wheel")
    checksum = hashlib.sha256(selected.read_bytes()).hexdigest()
    intent = {
        "project": manager.project_id,
        "gateway": plan.gateway_group.name,
        "configuration": state.digest([item.config_yaml for item in instances]),
        "artifact": checksum,
        "ha": plan.vm_ha is not None,
        "targets": [
            {
                "name": name,
                "compute": bindings[name]["compute"],
                "disk": bindings[name]["disk"],
                "transport": targets[name],
                "binding": state.digest(bindings[name]),
            }
            for name in sorted(targets)
        ],
    }
    state.validate_intent(intent)
    credential_plan = None
    if plan.vm_ha is not None:
        from ..vm_ha_managed_credentials import inspect_managed_vm_ha_credentials

        credential_plan = inspect_managed_vm_ha_credentials(
            project_id=manager.project_id,
            gateway_name=plan.gateway_group.name,
            node_ids=tuple(item.vm_ha_node.node_id for item in instances),
            tenant_id=local.get("tenant_id"),
            region_id=plan.gateway_group.region,
        )
    ssh._wheel_path = selected
    admission = Admission(
        manager,
        plan,
        local,
        path,
        instances,
        ssh,
        targets,
        bindings,
        selected,
        artifact,
        intent,
        credential_plan,
    )
    require_package_prerequisites(admission)
    return admission


def require_package_prerequisites(admission: Admission) -> None:
    """Check new resolution before effects without blocking frozen resume work."""
    previous = state.Journal(admission.root).load()
    if previous and previous["intent"] == admission.intent and previous["phase"] != "complete":
        if previous["phase"] != "confirmed":
            # Isolation is published only after every package closure is staged.
            return
        raw = state.read(admission.root / "runs" / previous["id"] / "context.json")
        if raw is not None:
            context = json.loads(raw)
            if (
                isinstance(context, dict)
                and context.get("intent") == state.digest(admission.intent)
                and isinstance(context.get("packages"), dict)
                and all(
                    isinstance(context["packages"].get(name), dict) for name in admission.targets
                )
            ):
                # stage() independently verifies every retained payload hash.
                return
    ordinary_apply.require_dependency_resolver()


def confirm(admission: Admission) -> bool:
    from . import pair_phases

    parent = pair_phases.CURRENT.get()
    if parent is not None and parent.request.get("repair") is True:
        if parent.request.get("repair_scope") != admission.intent:
            raise RuntimeError("Regional repair targets changed after pair confirmation")
        return True
    typer.echo(
        "Full repair will stop VPN traffic on every listed gateway and rebuild VPNGW, strongSwan, FRR, managed node certificates, configuration and operational state."
    )
    for target in admission.intent["targets"]:
        typer.echo(f"  {target['name']}: VM {target['compute']}, retained disk {target['disk']}")
    typer.echo(
        "Ubuntu, VM and disk identities, IP allocations, SSH access and external peer credentials are retained. Repair has no overall outage limit; VPN traffic remains interrupted until recovery completes."
    )
    return typer.confirm("Proceed with disruptive repair?", default=False)


class Coordinator:
    def __init__(self, admission: Admission, journal: state.Journal, *, resumed: bool):
        self.admission, self.journal, self.resumed = admission, journal, resumed
        value = journal.load()
        assert value is not None
        self.operation = value["id"]
        self.directory = admission.root / "runs" / self.operation
        state.directory(self.directory)
        self.path = self.directory / "context.json"
        raw = state.read(self.path)
        self.context: dict[str, Any] = (
            json.loads(raw)
            if raw is not None
            else {
                "intent": state.digest(admission.intent),
                "packages": {},
                "guards": {},
                "installed": {},
            }
        )
        if self.context["intent"] != state.digest(admission.intent):
            raise RuntimeError("Repair continuation differs from the confirmed target intent")
        self.context.setdefault("staged", {})
        sdk = admission.manager._build_sdk_client(admission.plan.gateway_group.region)
        self.cloud = RetainedCloud(
            sdk,
            CloudJournal(admission.root, operation=self.operation, intent=admission.intent),
            admission.bindings,
        )
        self.instances = {item.hostname: item for item in admission.instances}

    def save(self) -> None:
        state.publish(self.path, self.context)

    @property
    def phase(self) -> str:
        current = self.journal.load()
        if current is None or current["id"] != self.operation:
            raise RuntimeError("Repair coordinator lost its confirmed operation")
        return current["phase"]

    def advance(self, phase: str) -> None:
        """Commit a verified boundary on every guest before the controller."""
        for name in sorted(self.instances):
            self.remote(name, "advance", phase=phase)
        self.journal.advance(phase)

    def remote(self, name: str, action: str, **payload: Any) -> dict:
        return ordinary_apply.remote(
            self.admission.ssh,
            self.admission.targets[name],
            self.instances[name],
            self.admission.local,
            {"action": action, "operation": self.operation, **payload},
            bootstrap=repair_bootstrap,
        )

    def package_request(self, name: str) -> dict:
        return {
            "action": "repair-install",
            "operation": self.operation,
            "intent": state.digest(self.admission.intent),
            "package": self.context["packages"][name],
        }

    def wait_access(self, name: str, *, timeout: float = 300, writer_timeout: float = 1800) -> None:
        started = time.monotonic()
        deadline = started + timeout
        writer_deadline = started + writer_timeout
        waiting_for_writer = False
        retirement_observed = False
        while True:
            try:
                self.remote(name, "admit", intent=self.admission.intent)
                return
            except Exception as error:
                if isinstance(error, ordinary_apply.RemoteGuestWriterBusy):
                    if time.monotonic() >= writer_deadline:
                        raise RuntimeError(
                            "Repair is waiting for the existing guest deployment writer; retained work remains intact. Rerun apply --repair to continue."
                        ) from None
                    retired = self.retire_idle_handoffs(name)
                    if retired and not retirement_observed:
                        # Recheck access immediately after a newly observed
                        # retirement. Repeated receipts do not extend the bound
                        # or turn another writer's contention into a busy loop.
                        retirement_observed = True
                        continue
                    if not waiting_for_writer:
                        typer.echo("Waiting for the active guest deployment to finish...")
                        waiting_for_writer = True
                elif not repair_ssh.retryable_transport(error):
                    raise
                elif time.monotonic() >= deadline:
                    raise RepairBlocked(
                        "repair-ssh-unavailable", ownership_established=True,
                    ) from None
                time.sleep(2)

    def retire_idle_handoffs(self, name: str) -> bool:
        """A confirmed repair can retire an idle original handoff, never active work."""
        from . import package_reservation
        from .apply_command import CommandJournal

        if self.phase != "confirmed" or self.admission.plan.vm_ha is None:
            return False
        root = CommandJournal(self.admission.intent["project"], self.admission.intent["gateway"]).root
        reservations = package_reservation.for_host(root, name)
        if not reservations:
            return False
        result = self.remote(name, "retire-idle-handoffs", intent=self.admission.intent,
                             reservations=reservations)
        if result.get("operation") != self.operation or not isinstance(result.get("retirements"), list):
            raise RuntimeError("Prior handoff retirement returned invalid evidence")
        rows = result["retirements"]
        if len(rows) != len(reservations) or any(
            not isinstance(row, dict)
            or row.get("identity") != original["identity"]
            or row.get("state") not in {"ABSENT", "STOPPED", "PENDING", "RETIRED"}
            or (row["state"] == "RETIRED" and row.get("operation") != self.operation)
            for row, original in zip(rows, reservations, strict=True)
        ):
            raise RuntimeError("Prior handoff retirement differs from confirmed scope")
        return any(row["state"] == "RETIRED" for row in rows)

    def guard(self, name: str) -> None:
        self.remember_guard(name, self.remote(name, "guard"))

    def remember_guard(self, name: str, receipt: dict) -> None:
        if (
            receipt.get("operation") != self.operation
            or receipt.get("guard") != hashlib.sha256(repair_guest.GUARD).hexdigest()
        ):
            raise RuntimeError("Independent repair boot guard did not verify")
        self.context["guards"][name] = {
            **receipt,
            "compute": self.admission.bindings[name]["compute"],
            "binding": state.digest(self.admission.bindings[name]),
        }
        self.save()

    def stage(self, name: str) -> None:
        directory = self.directory / "packages" / name
        state.directory(directory)
        package = self.context["packages"].get(name)
        if package is None:
            observed = dependency_environment(self.remote(name, "inspect"))
            manifest = ordinary_apply.artifact(
                self.admission.wheel, self.instances[name].config_yaml
            )
            dependencies = ordinary_apply.resolve_dependencies(
                manifest, observed, directory, reinstall=True
            )
            product = directory / self.admission.wheel.name
            if not product.exists():
                shutil.copyfile(self.admission.wheel, product)
                product.chmod(0o600)
            package = {
                "native": observed["runtime_plan"],
                "wheels": {
                    path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in [*dependencies, product]
                },
                "product": product.name,
            }
            self.context["packages"][name] = package
            self.save()
        payload = {}
        for filename, checksum in package["wheels"].items():
            raw = (directory / filename).read_bytes()
            if hashlib.sha256(raw).hexdigest() != checksum:
                raise RuntimeError("Frozen repair payload changed on the controller")
            payload[filename] = base64.b64encode(raw).decode()
        receipt = self.remote(name, "stage", package=package, wheels=payload)
        if receipt != {"operation": self.operation, "package": state.digest(package)}:
            raise RuntimeError("Repair package staging returned an inexact receipt")
        self.context["staged"][name] = receipt
        self.save()

    def inhibition_order(self) -> list[str]:
        names = sorted(self.instances)
        if self.admission.plan.vm_ha is None:
            return names
        observed = self.admission.manager.observe_vm_ha_migration_state(
            self.admission.plan.gateway_group, self.admission.plan.gateway.get("local_prefixes")
        )
        owner = observed["shared_allocation"].get("owner")
        if owner is None:
            return names
        identifier = owner["compute_id"]
        if identifier not in {value["compute"] for value in self.admission.bindings.values()}:
            raise RuntimeError("Repair shared allocation is attached outside the retained pair")
        # Stop the non-owner controller before disturbing the owner. This
        # reduces old HA takeover races; fresh Stopped/ownership checks remain
        # the authority for the later cold reconstruction.
        return sorted(
            names, key=lambda name: self.admission.bindings[name]["compute"] == identifier
        )

    def prepare(self) -> None:
        # Download on reachable members before intentionally interrupting VPN
        # traffic. A resumed quarantined boot first needs its independent guard
        # and original network policy restored to regain package-source access.
        for name in sorted(self.instances):
            current = self.cloud.require(name)
            if instance_cloud_state(current) is InstanceCloudState.RUNNING:
                self.wait_access(name)
                quarantine = Quarantine(self.cloud, name)
                if quarantine.path.exists() and not quarantine.inspect()["restored"]:
                    if name not in self.context["guards"]:
                        self.guard(name)
                    quarantine.restore(boot_protection=self.context["guards"][name])
                if name not in self.context["staged"]:
                    self.stage(name)
        # Inhibit every live controller before another member can boot. A peer
        # must not race the cold stop or quarantine with automatic Start/rearm.
        for name in self.inhibition_order():
            if instance_cloud_state(self.cloud.require(name)) is InstanceCloudState.RUNNING:
                self.wait_access(name)
                if name not in self.context["guards"]:
                    try:
                        self.guard(name)
                    except (OSError, RuntimeError):
                        if (
                            instance_cloud_state(self.cloud.require(name))
                            is not InstanceCloudState.STOPPED
                        ):
                            raise
                        # An old controller may have stopped this member while
                        # inhibition was in flight. No guard is assumed: the
                        # stopped, unproven member takes the quarantine path.
        from . import repair_retirement

        repair_retirement.retire(self)
        self.cloud.stop_all()
        for name in sorted(self.instances):
            if name in self.context["guards"] and name in self.context["staged"]:
                continue
            current = self.cloud.require(name)
            if instance_cloud_state(current) is InstanceCloudState.STOPPED:
                quarantine = Quarantine(self.cloud, name)
                if name in self.context["guards"]:
                    self.cloud.start_guarded(name, boot_protection=self.context["guards"][name])
                else:
                    quarantine.apply()
                    quarantine.start()
                self.wait_access(name)
                self.guard(name)
                if quarantine.path.exists():
                    quarantine.restore(boot_protection=self.context["guards"][name])
            else:
                self.wait_access(name)
            self.stage(name)
            self.cloud.stop_all()
        for name in sorted(self.instances):
            if name not in self.context["guards"]:
                self.guard(name)
        self.journal.effect("cold-stop", self.admission.bindings)
        self.cloud.stop_all()
        self.journal.effect(
            "cold-stop",
            self.admission.bindings,
            state="complete",
            receipt={"stopped": sorted(self.admission.bindings)},
        )
        if self.admission.plan.vm_ha is not None:
            observed = self.admission.manager.observe_vm_ha_migration_state(
                self.admission.plan.gateway_group, self.admission.plan.gateway.get("local_prefixes")
            )
            allocation = observed["shared_allocation"].get("allocation_id")
            if not allocation:
                raise RuntimeError("Repair requires the retained shared IP allocation")
            preferred = next(
                item
                for item in self.admission.plan.gateway_group.vm_ha.members
                if item.role.value == "active"
            )
            name = f"{self.admission.plan.gateway_group.name}-{preferred.instance_index}"
            nic = self.admission.bindings[name]["nics"][0]["name"]
            self.journal.effect(
                "select-owner", {"allocation": allocation, "name": name, "nic": nic}
            )
            self.cloud.select_owner(allocation_id=allocation, name=name, nic_name=nic)
            self.journal.effect(
                "select-owner",
                {"allocation": allocation, "name": name, "nic": nic},
                state="complete",
                receipt={
                    "allocation": allocation,
                    "owner": self.admission.bindings[name]["compute"],
                },
            )
        self.journal.advance("isolated")

    def install(self) -> None:
        phase = self.phase
        if phase == "isolated":
            self.journal.advance("staged")
        for name in sorted(self.instances):
            self.cloud.start_guarded(name, boot_protection=self.context["guards"][name])
            self.wait_access(name)
            self.remote(name, "isolated")
        for name in sorted(self.instances):
            request = self.package_request(name)
            deadline = time.monotonic() + 1800
            retries = 0
            result = self.remote(
                name,
                "worker-start",
                request=request,
                source=repair_bootstrap.worker_source(),
                retry=True,
            )
            while result.get("state") != "PREPARED":
                if result.get("state") == "BLOCKED":
                    raise RepairBlocked("repair-worker-blocked")
                if time.monotonic() >= deadline:
                    raise RepairWorkerPending()
                if result.get("state") == "READY_TO_CONTINUE":
                    retries += 1
                    if retries > 3:
                        raise RepairBlocked(
                            "repair-package-stalled", ownership_established=True,
                        )
                    result = self.remote(
                        name,
                        "worker-start",
                        request=request,
                        source=repair_bootstrap.worker_source(),
                        retry=True,
                    )
                else:
                    time.sleep(1)
                    result = self.remote(name, "worker-find", request=request)
            installed = self.remote(name, "verify-installed", request=request)
            self.context["installed"][name] = installed
            self.save()
        if self.phase == "staged":
            self.journal.advance("reset")
        self.journal.advance("installed")

    def finish(self) -> None:
        for phase in ("enrolled", "prepared", "originating", "verified"):
            self.advance(phase)
        for name in sorted(self.instances):
            self.remote(name, "advance", phase="complete")
            self.remote(name, "finish")
            quarantine = Quarantine(self.cloud, name)
            if quarantine.path.exists():
                quarantine.cleanup()
        self.journal.advance("complete")


def execute(admission: Admission, *, report: Any) -> None:
    """Enter only under the canonical gateway writer lock."""
    from . import pair_phases

    parent = pair_phases.CURRENT.get()
    if parent is not None and parent.request["inspect_repair"]:
        parent.record["repair_scope"] = admission.intent
        parent.save()
        return
    if not confirm(admission):
        report.cancelled = True
        typer.echo("Repair cancelled; no gateway state was changed.")
        return
    journal = state.Journal(admission.root)
    previous = journal.load()
    settled = False
    if previous and previous["phase"] != "complete" and previous["intent"] != admission.intent:
        if previous["intent"]["targets"] != admission.intent["targets"]:
            raise RuntimeError("Repair successor cannot change retained VM identities")
        predecessor = Coordinator(
            replace(admission, intent=previous["intent"]), journal, resumed=True
        )
        # Retire all reachable writers before booting any stopped peer. An old
        # HA controller must not race the successor's quarantine or cold stop.
        settled_members = set()
        for name in sorted(predecessor.instances):
            if instance_cloud_state(predecessor.cloud.require(name)) is InstanceCloudState.RUNNING:
                predecessor.wait_access(name)
                predecessor.remember_guard(name, predecessor.remote(name, "settle"))
                quarantine = Quarantine(predecessor.cloud, name)
                if quarantine.path.exists():
                    quarantine.restore(boot_protection=predecessor.context["guards"][name])
                    quarantine.cleanup()
                settled_members.add(name)
        predecessor.cloud.stop_all()
        for name in sorted(predecessor.instances):
            if name in settled_members:
                continue
            if instance_cloud_state(predecessor.cloud.require(name)) is InstanceCloudState.STOPPED:
                proof = predecessor.context["guards"].get(name)
                if proof is None:
                    quarantine = Quarantine(predecessor.cloud, name)
                    quarantine.apply()
                    quarantine.start()
                else:
                    predecessor.cloud.start_guarded(name, boot_protection=proof)
            predecessor.wait_access(name)
            predecessor.remember_guard(name, predecessor.remote(name, "settle"))
            quarantine = Quarantine(predecessor.cloud, name)
            if quarantine.path.exists():
                quarantine.restore(boot_protection=predecessor.context["guards"][name])
                quarantine.cleanup()
        predecessor.cloud.stop_all()
        journal.settle_pending(
            {"stopped": sorted(admission.bindings), "writer_settlement": previous["id"]}
        )
        settled = True
    value = journal.begin(admission.intent, predecessor_settled=settled)
    resumed = previous is not None and previous["id"] == value["id"]
    owner = Coordinator(admission, journal, resumed=resumed)
    report.resumed = resumed
    report.plan(("full-repair",))
    report.started = True
    try:
        phase = owner.phase
        if phase == "confirmed":
            owner.prepare()
        if state.PHASES.index(owner.phase) < state.PHASES.index("installed"):
            owner.install()
        from . import repair_activation

        if state.PHASES.index(owner.phase) < state.PHASES.index("verified"):
            if admission.plan.vm_ha is not None:
                repair_activation.activate_ha(owner, report)
            else:
                repair_activation.activate_ordinary(owner)
        owner.finish()
    except BaseException:
        # The shared command boundary publishes the blocker and exact resume
        # command once, before structured or pair-child results are serialized.
        raise
    report.verified(changed=True)
    typer.echo(
        "Repair completed: packages, configuration, service state and forwarding authority verified. End-to-end connectivity requires separate verification."
    )
