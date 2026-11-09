"""Durable composition metadata; existing apply and specialized owners do effects."""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..apply_approval import digest
from .operator_guest import publish, read_file
from .operator_keys import protected_directory
from .replacement import metadata_only


class MissingCapability(RuntimeError):
    """Valid installed capability evidence proves a required feature is absent."""


class Composite:
    def __init__(self, root: Path, action: str) -> None:
        if action not in {"rotate-mtls", "enable-standby-auto-healing"}:
            raise ValueError("unsupported HA composite action")
        self.action = action
        self.path = root / "composite" / (action + ".json")

    def read(self) -> dict | None:
        record = read_file(self.path)
        if record is None:
            return None
        if record["mode"] != 0o600 or record["uid"] != os.getuid():
            raise RuntimeError("HA composite record is not protected")
        value = json.loads(record["content"])
        if (
            not isinstance(value, dict)
            or set(value)
            != {"schema", "approval", "intent", "phase", "receipt", "terminal", "result"}
            or value["schema"] != "nebius-vpngw.ha-composite.v1"
            or value["approval"] != digest(value["intent"])
            or value["intent"].get("action") != self.action
            or value["phase"] not in {"prerequisite", "terminal", "complete"}
            or (value["phase"] != "prerequisite" and not isinstance(value["receipt"], str))
        ):
            raise RuntimeError("HA composite authority is invalid")
        metadata_only(value)
        return value

    def save(self, expected: dict | None, value: dict) -> dict:
        current = read_file(self.path)
        if (json.loads(current["content"]) if current else None) != expected:
            raise RuntimeError("HA composite changed concurrently")
        metadata_only(value)
        protected_directory(self.path.parent)
        publish(
            self.path,
            current,
            {
                "content": json.dumps(value, sort_keys=True),
                "uid": os.getuid(),
                "gid": os.getgid(),
                "mode": 0o600,
            },
        )
        return value

    def begin(self, intent: dict) -> dict:
        current = self.read()
        if current is not None and current["phase"] != "complete":
            if current["intent"] != intent:
                raise RuntimeError("HA composite has another pending approval")
            return current
        return self.save(
            current,
            {
                "schema": "nebius-vpngw.ha-composite.v1",
                "approval": digest(intent),
                "intent": intent,
                "phase": "prerequisite",
                "receipt": None,
                "terminal": None,
                "result": None,
            },
        )

    def prepared(self, current: dict, receipt: str) -> dict:
        if current["phase"] != "prerequisite":
            if current["receipt"] != receipt:
                raise RuntimeError("HA composite prerequisite receipt changed")
            return current
        return self.save(current, {**current, "phase": "terminal", "receipt": receipt})

    def bind_terminal(self, current: dict, terminal: dict) -> dict:
        if current["phase"] != "terminal":
            raise RuntimeError("HA composite prerequisite is unverified")
        if current["terminal"] is not None:
            if current["terminal"] != terminal:
                raise RuntimeError("HA composite terminal intent changed")
            return current
        return self.save(current, {**current, "terminal": terminal})

    def record_result(self, current: dict, result: dict) -> dict:
        if current["phase"] != "terminal":
            raise RuntimeError("HA composite terminal authority is missing")
        if current["result"] is not None and current["result"] != result:
            raise RuntimeError("HA composite result identity changed")
        return self.save(current, {**current, "result": result})

    def complete(self, current: dict) -> dict:
        if current["phase"] != "terminal" or current["terminal"] is None:
            raise RuntimeError("HA composite terminal effect is unverified")
        return self.save(current, {**current, "phase": "complete"})


class ResultReady(Exception):
    def __init__(self, result) -> None:
        self.result = result


def journal_for(api, config_path: Path, action: str) -> Composite:
    from .apply_command import CommandJournal

    local = api.load_local_config(config_path)
    plan = api.merge_with_peer_configs(local, [])
    return Composite(CommandJournal(local["project_id"], plan.gateway_group.name).root, action)


def pending(api, config_path: Path, action: str, *, inspection=None) -> bool:
    from .apply_command import CommandJournal

    if inspection is None:
        local = api.load_local_config(config_path)
        project = local.get("project_id")
        gateway = (local.get("gateway_group") or {}).get("name")
        if not project or not gateway:
            return False  # The ordinary inspector remains responsible for invalid input.
    else:
        project, gateway = inspection.project_id, inspection.gateway_name
    value = Composite(CommandJournal(project, gateway).root, action).read()
    return value is not None and value["phase"] != "complete"


def identity(inspection, *, continuation: bool = False) -> dict:
    snapshot = inspection.snapshot
    lifecycle = snapshot.lifecycle_state
    if lifecycle is None or (snapshot.authority.condition != "exact" and not continuation):
        raise RuntimeError("HA prerequisite requires exact lifecycle and cloud ownership")
    return {
        "project": inspection.project_id,
        "gateway": inspection.gateway_name,
        "cluster": lifecycle.cluster_id,
        "allocation": lifecycle.allocation_id,
        "owner": snapshot.authority.owner_node_id,
        "members": sorted(
            [
                {
                    "node": member.node_id,
                    "compute": member.compute_id,
                    "role": member.role,
                    "interface": member.network_interface_name,
                    "address": member.public_ip,
                }
                for member in lifecycle.members
            ],
            key=lambda member: member["node"],
        ),
    }


def rotation_identity(plan) -> dict:
    return {
        "operation": plan.operation_id,
        "target_epoch": plan.target_epoch,
        "members": sorted(
            [
                {
                    "node": member.node_id,
                    "compute": member.compute_id,
                    "generation": member.generation_id,
                    "epoch": member.mtls["epoch"],
                    "fingerprint": member.mtls["certificate_fingerprint"],
                }
                for member in plan.members
            ],
            key=lambda member: member["node"],
        ),
    }


def prerequisite_matches(checkpoint: dict | None, intent: dict) -> bool:
    if not checkpoint or not checkpoint.get("transaction"):
        return False
    # The command graph approval differs from the public HA approval. snapshot()
    # validates their binding; compare the public owner, never the graph digest.
    runtime = checkpoint.get("bindings", {}).get("runtime", {})
    return runtime.get("approval") == intent["prerequisite"]


def continuation_allowed(inspection, saved: dict | None) -> bool:
    if saved is None or saved["phase"] != "prerequisite":
        return False
    from .apply_command import CommandJournal
    from .ha_preparation import command_authority

    state = inspection.snapshot.lifecycle_state
    if state is None or state.status.value != "activating" or state.transaction is None:
        return False
    checkpoint = CommandJournal(inspection.project_id, inspection.gateway_name).snapshot()
    if not prerequisite_matches(checkpoint, saved["intent"]):
        return False
    assert checkpoint is not None
    execution, approval = command_authority(state)
    return bool(
        execution == checkpoint["transaction"]["execution_id"]
        and isinstance(approval, dict)
        and approval.get("approval") == saved["intent"]["prerequisite"]
        and inspection.snapshot.authority.owner_node_id == saved["intent"]["identity"]["owner"]
        and set(inspection.snapshot.authority.reasons)
        <= {
            "lifecycle-activating",
            "lifecycle-operation-pending",
            "route-targets-not-exact",
            "route-records-not-exact",
            "route-prefixes-not-exact",
            "route-next-hop-not-exact",
        }
    )


def policy_authority(
    api, config_path, inspection, *, allow_pending: bool = False, statuses=None
) -> list[dict]:
    if statuses is None:
        statuses = api._run_vm_ha_auto_healing_statuses(
            local_config_file=config_path,
            desired=api.StandbyAutoHealing.ENABLED,
            inspection=inspection,
            require_capability=False,
        )
    if len(statuses) != 2 or {item.get("node_id") for item in statuses} != {
        member.node_id for member in inspection.snapshot.members
    }:
        raise RuntimeError("HA prerequisite requires exact policy evidence from both members")
    result = []
    for status in sorted(statuses, key=lambda item: item["node_id"]):
        record = status.get("record")
        if record is not None:
            from ..agent.vm_ha.auto_healing import AutoHealingPolicyRecord

            record = AutoHealingPolicyRecord.from_mapping(record).to_dict()
            record.pop("updated_at")
            record.pop("peer_ack_digest")
        if (
            status.get("accepted_start")
            or status.get("recovery_phase")
            or (record is not None and record["phase"] != "committed" and not allow_pending)
        ):
            raise RuntimeError("HA prerequisite has pending policy work")
        result.append({"node": status["node_id"], "record": record})
    return result


def policy_preserved(before: list[dict], after: list[dict]) -> bool:
    from ..agent.vm_ha.auto_healing import AutoHealingPolicyStore, StandbyAutoHealing

    if len(before) != len(after):
        return False
    for prior, current in zip(before, after, strict=True):
        if prior["node"] != current["node"]:
            return False
        old, new = prior["record"], current["record"]
        if old == new:
            continue
        if new is None:
            return False
        if old is not None and (
            any(
                old[key] != new[key]
                for key in (
                    "cluster_id",
                    "node_id",
                    "peer_node_id",
                    "coordinator_node_id",
                    "desired",
                    "phase",
                )
            )
            or old["generation_id"] == new["generation_id"]
        ):
            return False
        if (
            new["predecessor_digest"] != (old["decision_digest"] if old else "0" * 64)
            or new["desired"] != (old["desired"] if old else "enabled")
            or new["operation_id"]
            != AutoHealingPolicyStore._operation_id(
                cluster_id=new["cluster_id"],
                generation_id=new["generation_id"],
                desired=StandbyAutoHealing(new["desired"]),
            )
        ):
            return False
    return True


def policy_continuation(terminal: dict, policies: list[dict]) -> bool:
    predecessors = {member["node"]: member["record"] for member in terminal["predecessor"]}
    if {member["node"] for member in policies} != set(predecessors):
        return False
    for member in policies:
        current, previous = member["record"], predecessors[member["node"]]
        if current == previous:
            continue
        if (
            current is None
            or previous is None
            or current["operation_id"] != terminal["operation"]
            or current["desired"] != terminal["desired"]
            or current["predecessor_digest"] != previous["decision_digest"]
            or any(
                current[key] != previous[key]
                for key in (
                    "cluster_id",
                    "node_id",
                    "peer_node_id",
                    "generation_id",
                    "coordinator_node_id",
                )
            )
        ):
            return False
    return True


def require_prerequisite_admission(api, inspection, action: str) -> None:
    lifecycle = inspection.snapshot.lifecycle_state
    if lifecycle.status is not api.VMHALifecycleStatus.ACTIVE or (
        lifecycle.transaction is None or lifecycle.transaction.pending_effect is not None
    ):
        raise RuntimeError("HA specialized prerequisite requires a stable lifecycle")
    for member in inspection.snapshot.members:
        record = member.record
        if not isinstance(record, dict) or (
            record.get("pending_operation_id") is not None
            or record.get("repair") is not None
            or record.get("rearm_phase") in {"starting", "blocked"}
            or (
                record.get("rearm_phase") == "inhibited"
                and not (
                    action == "enable-standby-auto-healing"
                    and isinstance(record.get("auto_healing"), dict)
                    and record["auto_healing"].get("state") == "disabled"
                )
            )
            or record.get("apply_locked") is not False
        ):
            raise RuntimeError("HA prerequisite conflicts with an existing workflow")


def converge(
    api,
    config_path: Path,
    action: str,
    *,
    dry_run: bool,
    approve: str | None,
    region: str | None = None,
    interactive: bool = False,
):
    """Compose an exact non-destructive apply and terminal action under one lock.

    Called only for a typed missing capability or an existing composite. A
    malformed/unavailable observation cannot enter this path.
    """
    import hashlib

    from .activation_recovery import POLICY
    from .apply_command import CommandJournal

    journal = journal_for(api, config_path, action)
    saved = journal.read()
    resumed = saved is not None and saved["phase"] != "complete"
    inspection = api._inspect_vm_ha_prerequisite_status(config_path, region=region)
    configuration = hashlib.sha256(config_path.read_bytes()).hexdigest()
    frozen_identity = identity(
        inspection, continuation=continuation_allowed(inspection, saved) if resumed else False
    )
    if resumed:
        assert saved is not None
        intent = saved["intent"]
        if intent["configuration"] != configuration or intent["identity"] != frozen_identity:
            raise RuntimeError("HA composite configuration or member authority changed")
    else:
        require_prerequisite_admission(api, inspection, action)
        policy = (
            policy_authority(api, config_path, inspection)
            if action == "enable-standby-auto-healing"
            else None
        )
        rotation = None
        if action == "rotate-mtls":
            before = api._inspect_vm_ha_mtls_rotation(config_path, allow_missing_capability=True)
            if any(
                member.mtls.get("operation_id") or member.mtls.get("inhibited")
                for member in before.members
            ):
                raise RuntimeError(
                    "package convergence is not allowed inside an inhibited rotation"
                )
            rotation = rotation_identity(before)
        report = api._plan_vm_ha_convergence_with_region(config_path, region=region)
        if (
            report.has_destructive_changes
            or report.kind != "apply-convergence"
            or not report.artifact_sha256
            or report.impact.resource_creation is not False
            or report.managed_ssh_action not in {None, "reuse"}
            or report.managed_credential_action not in {None, "reuse"}
        ):
            raise RuntimeError("specialized prerequisite requires a retained-pair artifact plan")
        intent = {
            "action": action,
            "configuration": configuration,
            "identity": frozen_identity,
            "prerequisite": report.digest,
            "artifact": report.artifact_sha256,
            "effects": list(report.effects),
            "rotation": rotation,
            "policy": policy,
            "recovery": POLICY,
        }
    approval = digest(intent)
    result = api.VMHACommandResult(
        outcome=api.VMHACommandOutcome.PLANNED
        if dry_run
        else api.VMHACommandOutcome.ACTION_REQUIRED,
        classification=api.VMHACommandClassification.APPLY_REQUIRED,
        health=api.VMHACommandHealth.TRANSITIONING,
        effective_config_file=config_path,
        actions=("converge-prerequisites", action),
        reasons=("ha-capability-prerequisite",),
        approval=api.VMHACommandApproval(
            kind="ha-specialized-prerequisite",
            digest=approval,
            effects=tuple(intent["effects"]) + (action,),
        ),
        impact=api.VMHACommandImpact(
            summary="Approved prerequisite upgrade may interrupt VPN traffic; recovery allows three attempts of up to 120 seconds each",
            destructive=False,
            vpn_traffic_interruption=True,
            resource_creation=False,
        ),
        next_action="rerun the same command with --approve " + approval,
    )
    if approve is not None and approve != approval:
        raise RuntimeError("HA composite approval digest differs")
    if dry_run:
        return result
    if not resumed and approve is None:
        if not interactive:
            return result
        if not api._confirm_vm_ha_apply_plan(result):
            return api._vm_ha_operator_declined(result)
    with api.VMHAApplyLock(project_id=inspection.project_id, gateway_name=inspection.gateway_name):
        current = api._inspect_vm_ha_prerequisite_status(config_path, region=region)
        if (
            identity(
                current, continuation=continuation_allowed(current, saved) if resumed else False
            )
            != intent["identity"]
            or hashlib.sha256(config_path.read_bytes()).hexdigest() != configuration
        ):
            raise RuntimeError("HA composite changed before execution")
        if not resumed:
            require_prerequisite_admission(api, current, action)
            if (
                action == "enable-standby-auto-healing"
                and policy_authority(api, config_path, current) != intent["policy"]
            ):
                raise RuntimeError("HA prerequisite policy changed before execution")
            if action == "rotate-mtls":
                rotation = api._inspect_vm_ha_mtls_rotation(
                    config_path, allow_missing_capability=True
                )
                if (
                    any(
                        member.mtls.get("operation_id") or member.mtls.get("inhibited")
                        for member in rotation.members
                    )
                    or rotation_identity(rotation) != intent["rotation"]
                ):
                    raise RuntimeError("HA rotation prerequisite changed before execution")
        record = journal.begin(intent)
        command = CommandJournal(inspection.project_id, inspection.gateway_name)
        checkpoint = command.snapshot()
        transaction = checkpoint.get("transaction") if checkpoint else None
        prepared = bool(
            transaction
            and prerequisite_matches(checkpoint, intent)
            and transaction["phase"] == "complete"
            and "runtime" in transaction["receipts"]
        )
        if record["phase"] == "prerequisite" and not prepared:
            report = api._plan_vm_ha_convergence_with_region(config_path, region=region)
            if (
                report.digest != intent["prerequisite"]
                or report.artifact_sha256 != intent["artifact"]
            ):
                raise RuntimeError("HA composite prerequisite plan changed")
            if report.artifact is not None:
                report.artifact.verify_current()
            api._execute_vm_ha_convergence_with_region(
                config_path, report, region=region, progress_sink=None
            )
            checkpoint = command.snapshot()
            transaction = checkpoint.get("transaction") if checkpoint else None
        if (
            not transaction
            or not prerequisite_matches(checkpoint, intent)
            or transaction["phase"] != "complete"
            or "runtime" not in transaction["receipts"]
        ):
            raise RuntimeError("HA composite prerequisite has no exact completion receipt")
        receipt = digest(
            {
                "execution": transaction["execution_id"],
                "runtime": transaction["receipts"]["runtime"],
            }
        )
        record = journal.prepared(record, receipt)
        current = api._inspect_vm_ha_status_with_region(config_path, region=region)
        if identity(current) != intent["identity"]:
            raise RuntimeError("HA composite prerequisite changed protected authority")
        if action == "rotate-mtls":
            terminal = api._inspect_vm_ha_mtls_rotation(config_path)
            if record["terminal"] is None:
                if rotation_identity(terminal) != intent["rotation"]:
                    raise RuntimeError("HA prerequisite changed the approved rotation identity")
                record = journal.bind_terminal(
                    record, {"operation": terminal.operation_id, "epoch": terminal.target_epoch}
                )
            completed_rotation = bool(
                record["result"]
                and all(
                    member.mtls.get("operation_id") is None
                    and member.mtls.get("inhibited") is False
                    and member.mtls.get("epoch") == record["terminal"]["epoch"]
                    and member.mtls.get("certificate_fingerprint")
                    == record["result"].get(member.node_id)
                    for member in terminal.members
                )
            )
            if not completed_rotation:
                if (
                    terminal.operation_id != record["terminal"]["operation"]
                    or terminal.target_epoch != record["terminal"]["epoch"]
                ):
                    raise RuntimeError("HA rotation continuation changed exact operation or epoch")

                def prepared_rotation(receipts):
                    nonlocal record
                    record = journal.record_result(record, receipts)

                api._execute_vm_ha_mtls_rotation(terminal, prepared_callback=prepared_rotation)
        else:
            desired = api.StandbyAutoHealing.ENABLED
            statuses = api._run_vm_ha_auto_healing_statuses(
                local_config_file=config_path,
                desired=desired,
                inspection=current,
                require_capability=True,
            )
            policies = policy_authority(
                api,
                config_path,
                current,
                allow_pending=record["terminal"] is not None,
                statuses=statuses,
            )
            if record["terminal"] is None:
                if not policy_preserved(intent["policy"], policies):
                    raise RuntimeError("HA prerequisite changed the approved policy predecessor")
            elif not policy_continuation(record["terminal"], policies):
                raise RuntimeError("HA policy continuation changed the approved predecessor")
            if any(
                status.get("accepted_start") or status.get("recovery_phase") for status in statuses
            ):
                raise RuntimeError("HA policy continuation conflicts with standby recovery")
            if api._vm_ha_auto_healing_is_terminal(statuses, desired):
                terminal_intent = {
                    "operation": statuses[0]["operation_id"],
                    "desired": desired.value,
                    "predecessor": record["terminal"]["predecessor"]
                    if record["terminal"]
                    else policies,
                }
                record = journal.bind_terminal(record, terminal_intent)
            else:
                policy = api._vm_ha_auto_healing_transaction_for_statuses(
                    desired=desired,
                    inspection=current,
                    statuses=statuses,
                )
                record = journal.bind_terminal(
                    record,
                    {
                        "operation": policy.operation_id,
                        "desired": desired.value,
                        "predecessor": record["terminal"]["predecessor"]
                        if record["terminal"]
                        else policies,
                    },
                )

                def guard():
                    observed = api._inspect_vm_ha_status_with_region(config_path, region=region)
                    if observed.snapshot.authority_digest != current.snapshot.authority_digest:
                        raise RuntimeError("HA policy authority changed")

                api._execute_vm_ha_auto_healing_policy(
                    config_path=config_path,
                    desired=desired,
                    transaction=policy,
                    initial_statuses=statuses,
                    authority_guard=guard,
                )
        final = api._inspect_vm_ha_status_with_region(config_path, region=region)
        if identity(final) != intent["identity"]:
            raise RuntimeError("HA composite terminal authority changed")
        final = api._confirm_vm_ha_healthy(config_path, final, region=region, progress_sink=None)
        if final.snapshot.view.overall != "HEALTHY":
            raise RuntimeError("HA composite terminal health remains unverified")
        journal.complete(record)
        return api._vm_ha_result_from_snapshot(
            config_path=config_path,
            snapshot=final.snapshot,
            actions=("converge-prerequisites", action),
            dry_run=False,
        )
