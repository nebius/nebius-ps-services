"""Transfer an installed ordinary route writer through the replacement owner.

The old writer stays reserved until its Compute is retired. The candidate stays
reserved until every replacement proof has passed. The portable replacement
journal preserves the exact release request, including loss of its reply.
"""

from __future__ import annotations

from typing import Any

from .. import ordinary_route_binding as binding
from ..apply_approval import digest
from ..vm_ha_managed_credentials import (
    OrdinaryCredentialRecovery,
    inspect_managed_vm_ha_credentials,
)
from . import credential_enrollment
from .apply_plan import Progress
from .ordinary_route_discovery import Discovery
from .ordinary_route_ownership import RouteJournal, owned
from .ordinary_runtime_handoff import (
    Handoff,
    ObservedJournal,
    import_journal,
    same_completed_routes,
)
from .route_manager import RouteManager

RESERVED = "route-writer-reservation"
PREPARED = "route-writer-preparation"
RELEASE = "route-writer-release"
ENROLLMENT = "route-writer-enrollment"


def validate(value: dict, plan: Any) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != {"predecessor", "credentials", "intent", "journal"}
        or plan.vm_ha
        or len(plan.members) != 1
    ):
        raise ValueError("Replacement runtime routes require one ordinary member")
    old = value["predecessor"]
    if set(old) != {"authority", "journal"}:
        raise ValueError("Replacement runtime predecessor is incomplete")
    authority = old["authority"]
    current = binding.validate_intent(value["intent"])
    member = plan.members[0]
    if (
        current["project"] != plan.project_id
        or current["gateway"] != plan.gateway_name
        or current["node"] != member.logical_name
        or current["generation"] != member.configured_policy_sha256
        or current["infrastructure"]["gateways"]["0"]["compute_id"] != member.compute_id
        or current["infrastructure"]["gateways"]["0"]["allocation_id"]
        != member.primary_allocations[0]
    ):
        raise ValueError("Replacement runtime predecessor crossed source authority")
    if authority is not None:
        installed = binding.validate(authority["binding"])
        if (
            authority["writer"] != "runtime"
            or authority["holder"] is not None
            or {key: item for key, item in installed.items() if key != "credentials"} != current
            or old["journal"] != value["journal"]
        ):
            raise ValueError("Replacement runtime predecessor crossed source authority")
    elif old["journal"] is not None:
        raise ValueError("Unbound source has an unexpected runtime journal")
    ObservedJournal(plan.project_id, plan.gateway_name, value["journal"])
    transaction = value["journal"]["transaction"] if value["journal"] else None
    if transaction and transaction["phase"] != "complete":
        raise ValueError("Replacement cannot capture unsettled runtime routes")
    credentials = value["credentials"]
    from .credential_enrollment_schema import _validate_intent

    _validate_intent(credentials, plan.project_id, plan.gateway_name)
    if (
        credentials.get("purpose") != "ordinary-route-writer"
        or credentials.get("node_ids") != [member.logical_name]
        or authority is not None
        and (
            credentials.get("recovery") != authority["binding"]
            or credentials.get("action") not in {"reuse", "recover"}
        )
        or authority is None
        and "recovery" in credentials
    ):
        raise ValueError("Replacement credentials differ from installed runtime authority")


def transport(owner: Any, identifier: str, logical: str) -> Handoff:
    _access, target = owner.access(identifier, logical)
    return Handoff(owner.ssh, target.address, owner.instances[logical], owner.local)


def credential_plan(owner: Any, original: dict, reader: Any, *, enrollment=None):
    return inspect_managed_vm_ha_credentials(
        project_id=owner.manager.project_id,
        gateway_name=owner.deployment.gateway_group.name,
        node_ids=(original["node"],),
        purpose="ordinary-route-writer",
        tenant_id=owner.local.get("tenant_id"),
        region_id=owner.local.get("region"),
        enrollment=enrollment,
        # Include recovery authority even while the local copy exists, so a
        # portable resume can recover the same identity without changing intent.
        recovery=OrdinaryCredentialRecovery(original, reader) if reader is not None else None,
    )


def inspect(owner: Any, members: list) -> tuple[dict | None, RouteJournal | None]:
    captured = []
    for member in members:
        link = transport(owner, member.compute_id, member.logical_name)
        observed = link.inspect()
        if (
            len(members) > 1
            and observed["authority"] is not None
            and observed["authority"]["writer"] == "deployment"
        ):
            # The cardinality owner already retired this writer. Multi-member
            # replacement continues through its ordinary deployment route owner.
            continue
        if observed["authority"] is not None:
            captured.append((member, link, observed))
    if not captured:
        if len(members) != 1:
            return None, None
        default = owner.local.get("defaults", {}).get("routing", {}).get("mode", "bgp")
        if not any(
            connection.get("routing_mode", default) == "bgp"
            for connection in owner.local.get("connections", [])
        ):
            return None, None
        member = members[0]
        ledger = RouteJournal(owner.manager.project_id, owner.deployment.gateway_group.name)
        routes = RouteManager(
            owner.manager.project_id,
            auth_token=owner.manager.auth_token,
            ssh_policy=owner.ssh._ssh_policy,
        )
        observed = Discovery(owner.manager, owner.deployment, owner.local, ledger, routes).observe()
        intended = binding.intent(
            project=owner.manager.project_id,
            gateway=owner.deployment.gateway_group.name,
            node=member.logical_name,
            config=owner.instances[member.logical_name].config_yaml.encode(),
            observation=observed,
            gateway_subnet_name=routes._gateway_subnet_name(owner.local),
        )
        plan = credential_plan(owner, intended, None)
        return {
            "predecessor": {"authority": None, "journal": None},
            "credentials": plan.approval_record(),
            "intent": intended,
            "journal": ledger.snapshot(),
        }, ledger
    if len(members) != 1 or len(captured) != 1:
        raise RuntimeError("Runtime route replacement requires exact single-member authority")
    member, link, observed = captured[0]
    original = binding.validate(observed["authority"]["binding"])
    plan = credential_plan(owner, original, lambda: link.read_credential(original))
    return {
        "predecessor": observed,
        "credentials": plan.approval_record(),
        "intent": {key: item for key, item in original.items() if key != "credentials"},
        "journal": observed["journal"],
    }, ObservedJournal(
        owner.manager.project_id, owner.deployment.gateway_group.name, observed["journal"]
    )


def holder(journal: Any) -> str:
    return digest([journal.state["transaction"]["plan"]["operation_id"], "runtime-route-writer"])


def _scope(journal: Any) -> dict | None:
    return journal.state["transaction"]["plan"].get("route_runtime")


def enroll(owner: Any, journal: Any, command: Any) -> None:
    scope = _scope(journal)
    if scope is None:
        return
    current = command.state["transaction"]
    if current["progress"]["credentials"] != Progress.VERIFIED.value:
        command.started("credentials")
    authority = scope["predecessor"]["authority"]
    original = authority["binding"] if authority else scope["intent"]
    prepared = journal.state["transaction"]["results"].get(PREPARED)
    from .replacement_cloud import Cloud
    from .vm_ha_cloud import InstanceCloudState, instance_cloud_state

    source = Cloud(owner.sdk, journal, member_index=0).get_instance(
        original["infrastructure"]["gateways"]["0"]["compute_id"], absent_ok=True
    )
    if prepared and (
        authority is None
        or source is None
        or instance_cloud_state(source) is InstanceCloudState.STOPPED
    ):
        installed = prepared["binding"]
        identifier = installed["infrastructure"]["gateways"]["0"]["compute_id"]
    else:
        installed = original
        identifier = original["infrastructure"]["gateways"]["0"]["compute_id"]

    # The reader verifies the currently installed binding; the enrollment
    # continues to authorize only the original key and IAM identities.
    def read():
        return transport(owner, identifier, installed["node"]).read_credential(installed)

    child = credential_enrollment.EnrollmentJournal(command.project, command.gateway)
    results = journal.state["transaction"]["results"]
    if ENROLLMENT in results:
        child.restore_completed(
            results[ENROLLMENT],
            intent=scope["credentials"],
            approval=current["approval"],
            execution_id=current["execution_id"],
        )

    def checkpoint(state):
        if ENROLLMENT in results:
            if results[ENROLLMENT]["transaction"] != state["transaction"]:
                raise RuntimeError("Portable replacement enrollment changed")
        else:
            results[ENROLLMENT] = state
            journal.save()

    recovery_binding = (
        original if authority else installed if prepared and ENROLLMENT in results else None
    )
    observed = credential_plan(
        owner, recovery_binding or original, read if recovery_binding else None, enrollment=child
    )
    owner.route_credentials = credential_enrollment.execute(
        command,
        child,
        observed,
        node_ids=(original["node"],),
        tenant_id=owner.local.get("tenant_id"),
        region_id=owner.local.get("region"),
        checkpoint=checkpoint,
    ).credentials
    if current["progress"]["replacement"] != Progress.VERIFIED.value:
        command.started("replacement")


def reserve_source(owner: Any, journal: Any) -> None:
    scope = _scope(journal)
    if scope is None:
        return
    member = owner.member(journal)
    original = scope["predecessor"]
    link = transport(owner, member["compute_id"], member["logical_name"])
    current = link.reserve(holder(journal), original) if original["authority"] else link.inspect()
    if (
        original["authority"] is None
        and current != original
        or original["authority"]
        and (
            current["journal"] != original["journal"]
            or current["authority"]["binding"] != original["authority"]["binding"]
        )
    ):
        raise RuntimeError("Reserved replacement runtime receipts changed")
    results = journal.state["transaction"]["results"]
    receipt = {"holder": holder(journal), "predecessor": digest(original)}
    if RESERVED not in results:
        import_journal(RouteJournal(journal.project, journal.gateway), scope["journal"])
        results[RESERVED] = receipt
        journal.save()
    elif results[RESERVED] != receipt:
        raise RuntimeError("Replacement writer reservation receipt changed")


def verify_source(owner: Any, journal: Any, *, step: str) -> None:
    scope = _scope(journal)
    if scope is None:
        return
    member = owner.member(journal)
    current = transport(owner, member["compute_id"], member["logical_name"]).inspect()
    old = scope["predecessor"]
    if old["authority"] is None:
        if current != old:
            raise RuntimeError("Unreviewed runtime writer appeared on replacement source")
        return
    if step == "prepare" and RESERVED not in journal.state["transaction"]["results"]:
        # A lost reservation reply may already have selected this same holder.
        if current == old:
            return
    authority = current["authority"]
    if (
        authority is None
        or authority["writer"] != "workstation"
        or authority["holder"] != holder(journal)
        or authority["binding"] != old["authority"]["binding"]
        or current["journal"] != old["journal"]
    ):
        raise RuntimeError("Replacement source runtime writer is not durably reserved")


def before_routes(owner: Any, journal: Any) -> None:
    """Prove both writers excluded before replaying any external route effect."""
    scope = _scope(journal)
    if scope is None:
        return
    from .replacement_cloud import Cloud

    cloud = Cloud(owner.sdk, journal, member_index=0)
    source = journal.state["transaction"]["plan"]["members"][0]["compute_id"]
    if cloud.get_instance(source, absent_ok=True) is None:
        if cloud.completed("retire-compute") is None:
            raise RuntimeError("Runtime route source disappeared without retirement proof")
    else:
        cloud.require_stopped(source)
    results = journal.state["transaction"]["results"]
    if RESERVED not in results or PREPARED not in results:
        raise RuntimeError("Replacement routes lack their writer handoff checkpoints")
    value = results[PREPARED]["binding"]
    link = transport(owner, value["infrastructure"]["gateways"]["0"]["compute_id"], value["node"])
    current = link.inspect()["authority"]
    if (
        current is None
        or current["writer"] != "workstation"
        or current["holder"] != holder(journal)
    ):
        raise RuntimeError("Replacement candidate route writer is not reserved")
    ledger = RouteJournal(journal.project, journal.gateway)
    if ledger.snapshot() is None:
        # A portable resume reconstructs ownership from the frozen predecessor
        # plus settled replacement cloud receipts, never from route names.
        snapshot = results.get(RELEASE, {}).get("journal", scope["journal"])
        import_journal(ledger, snapshot)


def before_settle(owner: Any, journal: Any) -> None:
    if _scope(journal) is not None and any(
        record["request_type"] in {"CreateRouteRequest", "DeleteRouteRequest"}
        and record["status"] in {"intent", "accepted"}
        for record in journal.state["transaction"]["operations"].values()
    ):
        before_routes(owner, journal)


class _CandidateManager:
    def __init__(self, manager, target):
        self.manager, self.target = manager, target

    def __getattr__(self, key):
        return getattr(self.manager, key)

    def inspect_ordinary_targets(self, names):
        if tuple(names) != (self.target.hostname,):
            raise RuntimeError("Replacement route discovery expanded member scope")
        return {self.target.hostname: (self.target.compute_id, self.target.address)}


def discovery(owner: Any, journal: Any, target: Any, ledger: RouteJournal) -> Discovery:
    scope = _scope(journal)
    if scope is None:
        raise RuntimeError("Replacement has no runtime route discovery authority")
    original = scope["intent"]
    return Discovery(
        _CandidateManager(owner.manager, target),
        owner.deployment,
        owner.local,
        ledger,
        RouteManager(
            journal.project, auth_token=owner.manager.auth_token, ssh_policy=owner.ssh._ssh_policy
        ),
        summarize=original["policy"]["summarize"],
    )


def candidate_binding(owner: Any, journal: Any, target: Any) -> dict:
    scope = _scope(journal)
    if scope is None:
        raise RuntimeError("Replacement has no runtime route binding authority")
    original = scope["intent"]
    observation = discovery(
        owner, journal, target, RouteJournal(journal.project, journal.gateway)
    ).observe()
    before, after = original["infrastructure"], observation["infrastructure"]
    if {k: v for k, v in before.items() if k != "gateways"} != {
        k: v for k, v in after.items() if k != "gateways"
    } or digest(original["policy"]) != digest(observation["policy"].mapping()):
        raise RuntimeError("Replacement route infrastructure or policy changed")
    value = binding.build(
        project=journal.project,
        gateway=journal.gateway,
        node=target.hostname,
        config=owner.instances[target.hostname].config_yaml.encode(),
        observation=observation,
        credentials=owner.route_credentials,
        gateway_subnet_name=original["gateway_subnet_name"],
    )
    old = scope["predecessor"]["authority"]
    if old is not None and value["credentials"] != old["binding"]["credentials"]:
        raise RuntimeError("Replacement runtime credential identity changed")
    return value


def prepare_candidate(owner: Any, journal: Any, target: Any) -> None:
    if _scope(journal) is None:
        return
    link = transport(owner, target.compute_id, target.hostname)
    value = candidate_binding(owner, journal, target)
    results = journal.state["transaction"]["results"]
    receipt = {"binding": value}
    if PREPARED in results and results[PREPARED] != receipt:
        raise RuntimeError("Replacement candidate runtime binding changed")
    link.reserve(holder(journal), {"authority": None, "journal": None})
    # Persist before installing so remote credential recovery survives a lost
    # publication response. No candidate writer is enabled by this publication.
    if PREPARED not in results:
        results[PREPARED] = receipt
        journal.save()
    link.install(holder(journal), value, owner.route_credentials.nodes[0])


def released(owner: Any, journal: Any, target: Any) -> bool:
    if _scope(journal) is None:
        return False
    receipt = journal.state["transaction"]["results"].get(RELEASE)
    if receipt is None:
        return False
    link = transport(owner, target.compute_id, target.hostname)
    current = link.inspect()
    authority = current["authority"]
    expected = {
        "holder": holder(journal),
        "request": digest([receipt["binding"], receipt["journal"]]),
    }
    if (
        authority
        and authority["writer"] == "workstation"
        and authority["holder"] == holder(journal)
    ):
        return False
    if (
        not authority
        or authority["writer"] != "runtime"
        or authority["binding"] != receipt["binding"]
        or authority["receipt"] != expected
    ):
        raise RuntimeError("Replacement runtime release lost its exact receipt")
    ledger = ObservedJournal(journal.project, journal.gateway, current["journal"])
    transaction = ledger.transaction()
    if transaction and transaction["phase"] != "complete":
        raise RuntimeError("Runtime route operation is settling; retry replacement verification")
    observed = discovery(owner, journal, target, ledger).observe()
    if observed["infrastructure"] != receipt["binding"]["infrastructure"] or digest(
        observed["policy"].mapping()
    ) != digest(receipt["binding"]["policy"]):
        raise RuntimeError("Replacement runtime authority changed after release")
    records = ledger.inventory()
    found = {
        str(route.metadata.id)
        for rows in observed["routes"].values()
        for route in rows
        if owned(route, records, project=journal.project, gateway=journal.gateway)
    }
    if found != set(records) or not same_completed_routes(current, link.inspect()):
        raise RuntimeError("Replacement runtime route readback changed or is incomplete")
    return True


def finish(owner: Any, journal: Any, target: Any) -> None:
    if _scope(journal) is None or released(owner, journal, target):
        return
    results = journal.state["transaction"]["results"]
    if RELEASE not in results:
        value = candidate_binding(owner, journal, target)
        results[RELEASE] = {
            "binding": value,
            "journal": RouteJournal(journal.project, journal.gateway).snapshot(),
        }
        journal.save()
    receipt = results[RELEASE]
    link = transport(owner, target.compute_id, target.hostname)
    link.release(holder(journal), receipt["binding"], receipt["journal"])
    if not released(owner, journal, target):
        raise RuntimeError("Replacement runtime release is not independently verified")
