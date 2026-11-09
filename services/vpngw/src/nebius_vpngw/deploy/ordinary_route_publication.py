"""Compose runtime route ownership with existing deployment and IAM owners."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import yaml

from .. import ordinary_route_binding as binding
from ..apply_approval import digest
from ..vm_ha_credentials import managed_vm_ha_credential_path
from ..vm_ha_managed_credentials import (
    OrdinaryCredentialRecovery,
    inspect_managed_vm_ha_credentials,
)
from . import credential_enrollment
from .apply_plan import Effect, Progress
from .ordinary_route_ownership import RouteJournal
from .ordinary_runtime_handoff import (
    Handoff,
    ObservedJournal,
    import_journal,
    same_completed_routes,
)

RESERVATION = "route-reservation"
PUBLICATION = "route-runtime"
OWNERS = {"managed-credentials", "ordinary-route-reservation", "ordinary-route-runtime"}


@dataclass
class Publication:
    journal: Any
    ssh: Any
    instance: Any
    local: dict
    target: str | None
    observed: dict
    predecessor: dict
    credential_plan: Any
    runtime_enabled: bool = True
    required: bool = False
    credentials: Any = None
    acquired: bool = False

    @property
    def transport(self) -> Handoff:
        if not self.target:
            raise RuntimeError("Runtime route publication has no verified guest transport")
        return Handoff(self.ssh, self.target, self.instance, self.local)

    @property
    def holder(self) -> str:
        parent = self.journal.snapshot()["transaction"]
        return digest([self.journal.project, self.journal.gateway, parent["execution_id"]])

    def resume_sets(self, progress: dict) -> tuple[set[str], set[str]]:
        owner = self.observed["authority"]
        if (
            owner is not None
            and not self.released(self.observed)
            and (owner["writer"] != "workstation" or owner["holder"] != self.holder)
            and self.observed != self.predecessor
        ):
            raise RuntimeError("Pending runtime publication lost its writer authority")
        if progress[PUBLICATION] is Progress.VERIFIED and not self.released(self.observed):
            raise RuntimeError("Completed runtime publication lost its exact handoff receipt")
        keys = {"credentials", RESERVATION, PUBLICATION}
        satisfied = {key for key in keys if progress[key] is Progress.VERIFIED}
        return keys - satisfied, satisfied

    @property
    def desired(self) -> dict:
        return {
            "node": self.instance.hostname,
            "generation": hashlib.sha256(self.instance.config_yaml.encode()).hexdigest(),
            "predecessor": self.predecessor,
            "runtime_enabled": self.runtime_enabled,
        }

    def preview_journal(self) -> RouteJournal:
        current = RouteJournal(self.journal.project, self.journal.gateway)
        owner = self.observed["authority"]
        if owner and owner["writer"] == "runtime" and not self.journal.pending():
            return ObservedJournal(current.project, current.gateway, self.observed["journal"])
        # A resumed workstation retains its own uncertain operations. Never
        # replace them with the guest's pre-handoff journal.
        return current

    def select(self, *, changed: bool) -> None:
        pending = self.journal.pending()
        previous = self.observed["authority"]
        self.required = bool(
            pending
            and PUBLICATION in pending["progress"]
            or changed
            or self.credential_plan.command_action == "recover"
            or previous is None
            or previous["binding"] is None
            or (previous["writer"] == "runtime") != self.runtime_enabled
            or previous["binding"]["generation"] != self.desired["generation"]
        )

    def scope_changed(self, intent: dict) -> bool:
        owner = self.observed["authority"]
        return bool(
            owner
            and owner["binding"]
            and any(
                digest(owner["binding"][key]) != digest(intent[key])
                for key in ("policy", "infrastructure")
            )
        )

    def prefix(self) -> tuple[list[Effect], dict]:
        if not self.required:
            return [], {}
        credentials = self.credential_plan.approval_record()
        nodes = (self.instance.hostname,)
        effects = [
            Effect(
                "credentials",
                "managed-credentials",
                self.credential_plan.command_action,
                nodes,
                digest(credentials),
                digest(credentials),
                False,
            ),
            Effect(
                RESERVATION,
                "ordinary-route-reservation",
                "reserve",
                nodes,
                digest(self.desired),
                digest(self.predecessor),
                False,
                ("credentials",),
            ),
        ]
        return effects, {"credentials": credentials, RESERVATION: self.desired}

    def suffix(self, dependencies: tuple[str, ...]) -> tuple[Effect, dict]:
        return (
            Effect(
                PUBLICATION,
                "ordinary-route-runtime",
                "publish" if self.runtime_enabled else "retire",
                (self.instance.hostname,),
                digest(self.desired),
                digest(self.predecessor),
                False,
                dependencies,
            ),
            self.desired,
        )

    def begin(self) -> None:
        if not self.required:
            return
        transaction = self.journal.state["transaction"]
        if transaction["progress"]["credentials"] != Progress.VERIFIED.value:
            self.journal.started("credentials")
        result = credential_enrollment.execute(
            self.journal,
            credential_enrollment.EnrollmentJournal(self.journal.project, self.journal.gateway),
            self.credential_plan,
            node_ids=(self.instance.hostname,),
            tenant_id=self.local.get("tenant_id"),
            region_id=self.local.get("region"),
        )
        self.credentials = result.credentials
        if transaction["progress"][RESERVATION] != Progress.VERIFIED.value:
            self.journal.started(RESERVATION)
        if self.target:
            current = self.transport.inspect()
            owner = current["authority"]
            if owner is not None:
                if self.released(current):
                    self.acquired = False
                else:
                    self.acquire()
            elif self.predecessor["authority"] is not None:
                raise RuntimeError("Runtime route authority disappeared before reservation")
        # A first installation has no runtime writer. It acquires the durable
        # reservation after package installation, before any route mutation.
        self.journal.verified(
            RESERVATION, {"holder": self.holder, "predecessor": digest(self.predecessor)}
        )

    def acquire(self) -> None:
        if self.acquired:
            return
        current = self.transport.reserve(self.holder, self.predecessor)
        if self.predecessor["authority"] and self.predecessor["authority"]["writer"] == "runtime":
            local = RouteJournal(self.journal.project, self.journal.gateway)
            transaction = local.transaction()
            parent = self.journal.state["transaction"]
            owned = bool(
                transaction
                and transaction["approval"] == parent["approval"]
                and (
                    transaction["phase"] == "pending"
                    or parent["progress"].get("routes")
                    in {Progress.STARTED.value, Progress.VERIFIED.value}
                )
            )
            if not owned:
                import_journal(local, current["journal"])
        self.acquired = True

    def released(self, current: dict | None = None) -> bool:
        if self.journal.pending() is None:
            return False
        current = current or self.transport.inspect()
        owner = current["authority"]
        writer = "runtime" if self.runtime_enabled else "deployment"
        if not owner or owner["writer"] != writer or owner["receipt"] is None:
            return False
        local = RouteJournal(self.journal.project, self.journal.gateway).snapshot()
        if not self.runtime_enabled:
            return owner["receipt"] == {
                "holder": self.holder,
                "request": digest(["deployment", self.desired["generation"], local]),
            }
        return bool(
            owner["receipt"]
            == {"holder": self.holder, "request": digest([owner["binding"], local])}
            and owner["binding"]["generation"] == self.desired["generation"]
        )

    def before_routes(self, routes: Any) -> None:
        if not self.required:
            if routes.required:
                raise RuntimeError("Runtime route mutation lacks a reviewed reservation")
            return
        if self.released():
            return
        self.acquire()
        routes.discovery.journal = RouteJournal(self.journal.project, self.journal.gateway)

    def verify_released(self, discovery: Any) -> bool:
        """Audit current runtime receipts without replaying the old parent plan."""
        from dataclasses import replace

        from .ordinary_route_ownership import owned

        current = self.transport.inspect()
        if not self.released(current):
            return False
        state = current["journal"]
        if state and state["transaction"] and state["transaction"]["phase"] != "complete":
            raise RuntimeError("Runtime route operation is settling; retry parent verification")
        value = current["authority"]["binding"]
        journal = ObservedJournal(self.journal.project, self.journal.gateway, state)
        observed = replace(
            discovery,
            journal=journal,
            force_copy=False,
            **({"summarize": value["policy"]["summarize"]} if self.runtime_enabled else {}),
        ).observe()
        if self.runtime_enabled and (
            observed["infrastructure"] != value["infrastructure"]
            or digest(observed["policy"].mapping()) != digest(value["policy"])
        ):
            raise RuntimeError("Runtime route binding changed after publication")
        records = journal.inventory()
        found = set()
        for routes in observed["routes"].values():
            for route in routes:
                if owned(route, records, project=journal.project, gateway=journal.gateway):
                    found.add(str(route.metadata.id))
        if found != set(records) or not same_completed_routes(current, self.transport.inspect()):
            raise RuntimeError("Runtime route evidence changed during parent verification")
        return True

    def finish(self, routes: Any) -> None:
        if not self.required:
            return
        transaction = self.journal.state["transaction"]
        if transaction["progress"][PUBLICATION] != Progress.VERIFIED.value:
            self.journal.started(PUBLICATION)
        current = self.transport.inspect()
        if not self.released(current):
            self.acquire()
            from dataclasses import replace

            if self.runtime_enabled:
                observation = replace(routes.discovery, force_copy=False).observe()
                value = binding.build(
                    project=self.journal.project,
                    gateway=self.journal.gateway,
                    node=self.instance.hostname,
                    config=self.instance.config_yaml.encode(),
                    observation=observation,
                    credentials=self.credentials,
                    gateway_subnet_name=routes.discovery.routes._gateway_subnet_name(self.local),
                )
                self.transport.install(self.holder, value, self.credentials.nodes[0])
                current = self.transport.release(
                    self.holder, value, routes.discovery.journal.snapshot()
                )
            else:
                current = self.transport.retire(
                    self.holder, self.desired["generation"], routes.discovery.journal.snapshot()
                )
        if not self.released(current):
            raise RuntimeError("Runtime route publication has no exact terminal receipt")
        self.journal.verified(PUBLICATION, current["authority"]["receipt"])


def inspect(
    journal: Any, ssh: Any, instances: tuple, local: dict, targets: dict
) -> Publication | None:
    # Retire an installed Single-VM writer through the same composed command
    # when the existing ordinary provisioning owner changes cardinality.
    runtime_enabled = len(instances) == 1 and instances[0].instance_index == 0
    retiring = []
    if not runtime_enabled:
        for instance in instances:
            target = targets.get(instance.hostname, (None, None))[1]
            observed = Handoff(ssh, target, instance, local).inspect() if target else None
            if (
                observed
                and observed["authority"]
                and (
                    observed["authority"]["writer"] != "deployment"
                    or journal.pending()
                    and PUBLICATION in journal.pending()["progress"]
                )
            ):
                retiring.append(instance)
        if not retiring:
            return None
        if len(retiring) != 1:
            raise RuntimeError("Cardinality change has ambiguous runtime route writers")
    instance = instances[0] if runtime_enabled else retiring[0]
    cfg = yaml.safe_load(instance.config_yaml)
    default = cfg.get("defaults", {}).get("routing", {}).get("mode", "bgp")
    dynamic = any(c.get("routing_mode", default) == "bgp" for c in cfg.get("connections", []))
    target = targets.get(instance.hostname, (None, None))[1]
    observed = (
        Handoff(ssh, target, instance, local).inspect()
        if target
        else {"authority": None, "journal": None}
    )
    owner = observed["authority"]
    if not dynamic and owner is None:
        return None
    if (
        owner
        and owner["binding"]
        and any(
            owner["binding"][key] != expected
            for key, expected in (
                ("project", journal.project),
                ("gateway", journal.gateway),
                ("node", instance.hostname),
            )
        )
    ):
        raise RuntimeError("Runtime route handoff crossed deployment identity")
    pending = journal.pending()
    saved = journal.snapshot()["bindings"] if pending else {}
    predecessor = deepcopy(saved.get(RESERVATION, {}).get("predecessor", observed))
    enrollment = credential_enrollment.EnrollmentJournal(journal.project, journal.gateway)
    source = managed_vm_ha_credential_path(
        project_id=journal.project,
        gateway_name=journal.gateway,
        purpose="ordinary-route-writer",
    )
    recovery = None
    recovered_binding = saved.get("credentials", {}).get("recovery")
    if recovered_binding is None and owner and not source.exists() and not source.is_symlink():
        recovered_binding = owner["binding"]
    if recovered_binding is not None:
        value = binding.validate(recovered_binding)
        if (
            target is None
            or value["infrastructure"]["gateways"]["0"]["compute_id"]
            != targets[instance.hostname][0]
        ):
            raise RuntimeError("Credential recovery requires the exact installed Compute target")
        recovery = OrdinaryCredentialRecovery(
            value, lambda: Handoff(ssh, target, instance, local).read_credential(value)
        )
    plan = inspect_managed_vm_ha_credentials(
        project_id=journal.project,
        gateway_name=journal.gateway,
        node_ids=(instance.hostname,),
        purpose="ordinary-route-writer",
        tenant_id=local.get("tenant_id"),
        region_id=local.get("region"),
        enrollment=enrollment if "credentials" in saved else None,
        recovery=recovery,
    )
    if "credentials" in saved:
        plan = enrollment.bind_observation(
            plan,
            intent=saved["credentials"],
            approval=pending["approval"],
            execution_id=pending["execution_id"],
        )
    return Publication(
        journal,
        ssh,
        instance,
        local,
        target,
        observed,
        predecessor,
        plan,
        runtime_enabled=runtime_enabled,
    )
