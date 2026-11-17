"""Proof of an obsolete HA generation before separately approved settlement.

The package owner approved the unbound YAML. The guest's bound YAML is different
by design; removing only runtime_binding reconstructs those original bytes.
Neither the caller's new configuration nor a controller status can supply this
missing predecessor authority.
"""

from __future__ import annotations

import json
import os
import re
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from ..apply_approval import digest, manifest_identity
from .recovery import ReplanRejected
from .vm_ha_lifecycle import VMHALifecycleState
from .vm_ha_upgrade_recovery import UPGRADE_RECOVERY_INSPECTION

SCHEMA = "nebius-vpngw.ha-settlement.v1"


def validate_record(value: dict, *, previous: VMHALifecycleState | None = None) -> dict:
    """A settled record is explicit successor authority, never an old approval alias."""
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "proof", "requests", "phase", "receipts"}
        or value["schema"] != SCHEMA
        or value["phase"] != "complete"
    ):
        raise ReplanRejected("HA successor lacks completed settlement evidence")
    proof = value["proof"]
    if (
        not isinstance(proof, dict)
        or set(proof) - {"recovery_frontier"}
        != {
            "action",
            "operation",
            "predecessor",
            "lifecycle",
            "prior_desired",
            "desired",
            "artifact",
            "cloud",
            "hosts",
            "guests",
            "owners",
            "ssh_trust",
            "generations",
            "sources",
        }
        or proof["action"] != "settle-ha-predecessor"
    ):
        raise ReplanRejected("HA successor settlement proof is malformed")
    for key in set(proof) - {"action", "guests", "owners", "generations", "recovery_frontier"}:
        if not isinstance(proof[key], str) or re.fullmatch(r"[a-f0-9]{64}", proof[key]) is None:
            raise ReplanRejected("HA successor settlement digest is malformed")
    if (
        not isinstance(value["requests"], dict)
        or not isinstance(value["receipts"], dict)
        or set(value["requests"]) != set(value["receipts"])
        or set(value["requests"]) != set(proof["guests"])
    ):
        raise ReplanRejected("HA successor settlement member set differs")
    if any(
        not isinstance(receipt, str) or re.fullmatch(r"[a-f0-9]{64}", receipt) is None
        for receipt in value["receipts"].values()
    ):
        raise ReplanRejected("HA successor settlement receipt is malformed")
    if not isinstance(proof["generations"], dict) or set(proof["generations"]) != set(
        value["requests"]
    ):
        raise ReplanRejected("HA successor predecessor generations are incomplete")
    for node, request in value["requests"].items():
        if (
            not isinstance(request, dict)
            or set(request)
            != {
                "action",
                "operation",
                "previous_lock",
                "children",
                "boot_id",
                "adoption_sha256",
                "package_reservation",
            }
            or request["action"] != "replan-settle"
            or request["operation"] != proof["operation"]
        ):
            raise ReplanRejected("HA successor settlement request is malformed")
        guest = proof["guests"][node]
        if any(request[name] != guest[name] for name in ("children", "boot_id", "adoption_sha256")):
            raise ReplanRejected("HA successor settlement guest authority differs")
        generation = proof["generations"][node]
        if (
            not isinstance(generation, list)
            or len(generation) != 2
            or generation[0] != request["previous_lock"]["generation_id"]
        ):
            raise ReplanRejected("HA successor predecessor generation differs")
    frontier = proof.get("recovery_frontier")
    if frontier is not None:
        if not isinstance(frontier, dict) or set(frontier) != {
            "lifecycle_artifact",
            "command_artifact",
            "manifests",
        }:
            raise ReplanRejected("Recovery settlement frontier is malformed")
        if set(frontier["manifests"]) != set(value["requests"]) or any(
            not isinstance(item, str) or re.fullmatch(r"[a-f0-9]{64}", item) is None
            for item in (
                frontier["lifecycle_artifact"],
                frontier["command_artifact"],
                *frontier["manifests"].values(),
            )
        ):
            raise ReplanRejected("Recovery settlement package authority is malformed")
        if previous is not None:
            from .vm_ha_upgrade_recovery import ARTIFACT, unfinished_recovery

            recovery = unfinished_recovery(previous)
            assert previous.transaction is not None
            if (
                recovery is None
                or recovery["artifact"] != frontier["command_artifact"]
                or dict(previous.transaction.resource_bindings)[ARTIFACT]
                != frontier["lifecycle_artifact"]
            ):
                raise ReplanRejected("Recovery settlement predecessor authority differs")
    if previous is not None and (
        previous.transaction is None
        or proof["lifecycle"] != previous.record_sha256
        or proof["prior_desired"] != previous.transaction.desired_state_digest
        or set(value["requests"]) != {member.node_id for member in previous.members}
    ):
        raise ReplanRejected("HA successor settlement has another predecessor")
    return proof


def successor_operation(state: VMHALifecycleState, record: dict) -> str:
    validate_record(record, previous=state)
    return digest(["nebius-vpngw/settled-ha-replan-v1", state.record_sha256, digest(record)])


PREDECESSOR_INSPECTION = (
    UPGRADE_RECOVERY_INSPECTION.replace(
        "def project(raw):\n    ha = yaml.safe_load(raw)['vm_ha']",
        "def project(raw):\n"
        "    payload = yaml.safe_load(raw)\n"
        "    ha = payload['vm_ha']\n"
        "    unbound = dict(ha)\n"
        "    del unbound['runtime_binding']\n"
        "    unbound_raw = yaml.safe_dump({**payload, 'vm_ha': unbound}, sort_keys=False).encode()",
    )
    .replace(
        "'sha256': hashlib.sha256(raw).hexdigest(),",
        "'sha256': hashlib.sha256(raw).hexdigest(),\n"
        "        'unbound_sha256': hashlib.sha256(unbound_raw).hexdigest(),",
    )
    .replace(
        "    staged = read('/etc/nebius-vpngw/vm-ha-staged/' + generation_id + '.yaml')",
        "    original_lock = record('apply.lock')\n"
        "    generation_id = original_lock['generation_id']\n"
        "    import re\n"
        "    if not isinstance(generation_id, str) or re.fullmatch(r'[a-f0-9]{64}', generation_id) is None:\n"
        "        raise ValueError('predecessor generation is invalid')\n"
        "    staged_path = '/etc/nebius-vpngw/vm-ha-staged/' + generation_id + '.yaml'\n"
        "    staged = read(staged_path)",
    )
    .replace(
        "    print(json.dumps(result, sort_keys=True, separators=(',', ':')))",
        "    if record('apply.lock') != original_lock or read(staged_path) != staged:\n"
        "        raise ValueError('predecessor authority changed during observation')\n"
        "    print(json.dumps(result, sort_keys=True, separators=(',', ':')))",
    )
)


def predecessor_generations(
    state: VMHALifecycleState, evidence: dict, *, selected_manifests: dict | None = None
) -> dict:
    """Bind both installed/staged projections to the original approved package."""
    transaction = state.transaction
    if transaction is None:
        raise ReplanRejected("HA predecessor transaction is unavailable")
    manifests = (
        selected_manifests
        if selected_manifests is not None
        else json.loads(
            dict(transaction.resource_bindings).get("runtime-package-manifests", "null")
        )
    )
    if (
        not isinstance(manifests, dict)
        or set(manifests) != {member.instance_name for member in state.members}
        or set(evidence) != {member.node_id for member in state.members}
    ):
        raise ReplanRejected(
            "HA predecessor package authority is incomplete", code="predecessor-package-unproven"
        )
    generations = {}
    for member in state.members:
        manifest = manifests[member.instance_name]
        item = evidence[member.node_id]
        installed, staged = item.get("installed"), item.get("staged")
        expected = manifest.get("config_sha256") if isinstance(manifest, dict) else None
        if (
            not isinstance(expected, str)
            or re.fullmatch(r"[a-f0-9]{64}", expected) is None
            or not isinstance(installed, dict)
            or not isinstance(staged, dict)
            or any(value.get("unbound_sha256") != expected for value in (installed, staged))
            or installed.get("generation_id") != staged.get("generation_id")
            or installed.get("digests") != staged.get("digests")
        ):
            raise ReplanRejected(
                "HA predecessor configuration is not the approved installed generation",
                code="predecessor-package-unproven",
            )
        files = manifest.get("files")
        sources = {
            name.removeprefix("nebius_vpngw/"): value
            for name, value in (files.items() if isinstance(files, dict) else ())
            if name.startswith("nebius_vpngw/") and name.endswith(".py")
        }
        if not sources or item.get("source_sha256") != sources:
            raise ReplanRejected(
                "HA predecessor installed source differs from the approved artifact",
                code="predecessor-package-unproven",
            )
        generation, digests = staged.get("generation_id"), staged.get("digests")
        if (
            not isinstance(generation, str)
            or re.fullmatch(r"[a-f0-9]{64}", generation) is None
            or not isinstance(digests, dict)
            or set(digests) != {"configuration", "static_routes", "bgp_policy"}
            or any(
                not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None
                for value in digests.values()
            )
        ):
            raise ReplanRejected("HA predecessor generation is malformed")
        generations[member.node_id] = (generation, digests)
    if len({digest(value) for value in generations.values()}) != 1:
        raise ReplanRejected(
            "HA predecessor members do not share one settled generation",
            code="predecessor-package-unproven",
        )
    return generations


def require_same_infrastructure(
    state: VMHALifecycleState, desired: dict, generations: dict
) -> None:
    """New generation settings may change; prior infrastructure must be exact."""
    if state.transaction is None or set(generations) != {
        member.node_id for member in state.members
    }:
        raise ReplanRejected("HA predecessor generation authority is incomplete")
    prior = deepcopy(desired)
    generation, digests = next(iter(generations.values()))
    prior["generation"] = {
        "generation_id": generation,
        "configuration_digest": digests["configuration"],
        "static_routes_digest": digests["static_routes"],
        "bgp_policy_digest": digests["bgp_policy"],
    }
    if digest(prior) != state.transaction.desired_state_digest:
        raise ReplanRejected(
            "HA replanning cannot change predecessor infrastructure identity",
            code="predecessor-infrastructure-changed",
        )


def operation_id(state: VMHALifecycleState, parent: dict, desired: str, artifact: str) -> str:
    return digest([SCHEMA, state.record_sha256, digest(parent), desired, artifact])


def settled_owners(journal, state, credential_plan, ssh_policy) -> tuple[dict, dict, dict]:
    """Observe every local participant, including effects hidden by parent lag."""
    from .apply_plan import CommandPlan
    from .credential_enrollment import EnrollmentJournal
    from .ordinary_route_ownership import RouteJournal
    from .provisioning_journal import ProvisioningJournal
    from .provisioning_trust import TrustJournal, selection
    from .replacement import Journal as ReplacementJournal
    from .security_group_apply import AttachmentJournal

    previous = journal.snapshot()
    parent = previous["transaction"] if previous else None
    transaction = state.transaction
    from .ha_command import prepared_predecessor
    from .ha_preparation import command_authority

    prepared = prepared_predecessor(previous, state) if previous else None
    if parent is None or (parent["phase"] != "pending" and prepared is None) or transaction is None:
        raise ReplanRejected("HA settlement requires its exact pending or prepared command")
    plan = CommandPlan.parse(parent["plan"])
    runtime = previous["bindings"].get("runtime")
    bindings = dict(transaction.resource_bindings)
    execution, approval = command_authority(state)
    successor = runtime.get("successor") if isinstance(runtime, dict) else None
    successor_authority = None
    if successor:
        from .vm_ha_corrective import authority
        from .vm_ha_upgrade_recovery import recovery_authority

        successor_authority = (
            authority(state)
            if successor["kind"] == "corrective-release"
            else recovery_authority(state)
        )
        if (
            successor_authority is None
            or successor_authority["approval"] != runtime["approval"]
            or successor_authority["artifact"] != plan.artifact_sha256
            or successor_authority["prior_artifact"] != successor["prior_artifact"]
            or successor_authority["prior_record"] != successor["prior_lifecycle"]
            or (
                "settlement" in successor
                and digest(successor_authority.get("replan")) != successor["settlement"]
            )
        ):
            raise ReplanRejected("HA settlement successor authority differs")
    if (
        not isinstance(runtime, dict)
        or any(
            effect.owner
            not in {"ha-lifecycle", "managed-credentials", "ssh-bootstrap", "security-groups"}
            for effect in plan.effects
        )
        or sum(
            effect.key == "runtime" and effect.owner == "ha-lifecycle" for effect in plan.effects
        )
        != 1
        or tuple(sorted(next(effect for effect in plan.effects if effect.key == "runtime").targets))
        != tuple(sorted(member.compute_id for member in state.members))
        or (
            successor_authority is None
            and (
                execution != parent["execution_id"]
                or not isinstance(approval, dict)
                or approval.get("approval") != runtime["approval"]
                or approval.get("scope") != runtime["runtime_scope"]
            )
        )
    ):
        raise ReplanRejected("HA settlement command authority differs")
    if runtime.get("continuation"):
        from .ha_preparation import continuation_command, receipt

        handoff = continuation_command(bindings)
        preparation = receipt(state)
        if (
            handoff is None
            or preparation is None
            or handoff["previous_execution"] != runtime["continuation"]["previous_execution"]
            or runtime["continuation"]["prepared"] != digest(preparation)
        ):
            raise ReplanRejected("HA settlement continuation authority differs")
    if (
        credential_plan.credentials is None
        or credential_plan.action != "reuse"
        or any(
            item["action"] != "reuse"
            for item in credential_plan.observation_record()["iam_resources"].values()
        )
        or ssh_policy is None
        or ssh_policy.managed_action is not None
    ):
        raise ReplanRejected(
            "HA settlement cannot change credentials or SSH identity",
            code="predecessor-infrastructure-changed",
        )
    ssh_policy.assert_current()
    owners = {
        "credentials": EnrollmentJournal(journal.project, journal.gateway),
        "ssh": TrustJournal(journal.project, journal.gateway),
        "attachments": AttachmentJournal(journal.project, journal.gateway),
        "routes": RouteJournal(journal.project, journal.gateway),
        "provisioning": ProvisioningJournal(journal.project, journal.gateway),
        "replacement": ReplacementJournal(journal.project, journal.gateway),
    }
    snapshots = {name: owner.snapshot() for name, owner in owners.items()}
    for name, snapshot in snapshots.items():
        child = snapshot["transaction"] if snapshot else None
        if child is not None and child["phase"] != "complete":
            raise ReplanRejected(
                "HA settlement has an unresolved local child owner", code="predecessor-not-settled"
            )
        expected = previous["bindings"].get(name)
        if expected is None:
            continue
        if (
            name not in {"credentials", "ssh"}
            or child is None
            or (
                child["intent"] != expected
                or child["execution_id"] != parent["execution_id"]
                or child["approval"] != parent["approval"]
            )
        ):
            raise ReplanRejected("HA settlement prerequisite authority differs")
        if name == "credentials":
            credential_owner = owners[name]
            assert isinstance(credential_owner, EnrollmentJournal)
            credential_owner._admit_observation(credential_plan, child)
            if (
                child["receipt"] != credential_plan.credentials.resource_bindings()
                or child["selected"] != credential_plan.public_key_sha256
            ):
                raise ReplanRejected("HA settlement credential receipt differs")
        elif child["selected"] != selection(ssh_policy, tuple(child["intent"]["members"])):
            raise ReplanRejected("HA settlement SSH receipt differs")
    return previous, owners, snapshots


@dataclass
class Settlement:
    journal: Any
    lifecycle_store: Any
    lifecycle: VMHALifecycleState
    predecessor: dict
    owners: dict
    snapshots: dict
    proof: dict
    requests: dict

    @property
    def approval(self) -> str:
        return digest(self.proof)

    @property
    def path(self):
        return self.journal.root / "ha-replanning" / (self.proof["operation"] + ".json")

    def read(self) -> dict | None:
        from .operator_guest import read_file

        raw = read_file(self.path)
        if raw is None:
            return None
        if raw["uid"] != os.getuid() or raw["mode"] != 0o600:
            raise ReplanRejected("HA settlement record is not protected")
        value = json.loads(raw["content"])
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", "proof", "requests", "phase", "receipts"}
            or value["schema"] != SCHEMA
            or value["proof"] != self.proof
            or value["requests"] != self.requests
            or value["phase"] not in {"settling", "complete"}
            or not isinstance(value["receipts"], dict)
            or not set(value["receipts"]) <= set(self.requests)
            or value["phase"] == "complete"
            and set(value["receipts"]) != set(self.requests)
        ):
            raise ReplanRejected("HA settlement retained authority changed")
        return value

    def save(self, record) -> None:
        from .operator_guest import publish, read_file
        from .operator_keys import protected_directory

        protected_directory(self.path.parent)
        publish(
            self.path,
            read_file(self.path),
            {
                "content": json.dumps(record, sort_keys=True),
                "uid": os.getuid(),
                "gid": os.getgid(),
                "mode": 0o600,
            },
        )

    def execute(self, *, refresh, settle_guest, verify_stopped) -> None:
        """Caller approved only settlement; fresh deployment is a separate plan."""
        from .recovery import WriterBusy
        from .vm_ha_corrective import archive

        with ExitStack() as stack:
            try:
                stack.enter_context(self.journal)
                for owner in self.owners.values():
                    stack.enter_context(owner)
            except BlockingIOError as error:
                raise WriterBusy("HA predecessor still has a local writer") from error
            if self.journal.state != self.predecessor:
                raise ReplanRejected("HA settlement command changed")
            for name, owner in self.owners.items():
                expected = self.snapshots[name]
                if expected is None:
                    if owner.state["transaction"] is not None or owner.state["bindings"]:
                        raise ReplanRejected("HA settlement child appeared")
                elif owner.state != expected:
                    raise ReplanRejected("HA settlement child changed")
            fresh = refresh()
            if fresh.proof != self.proof or fresh.requests != self.requests:
                raise ReplanRejected("HA settlement approval became stale")
            record = self.read() or {
                "schema": SCHEMA,
                "proof": self.proof,
                "requests": self.requests,
                "phase": "settling",
                "receipts": {},
            }
            self.save(record)
            # Archive via the respective owners while their canonical pending
            # authority remains intact. No unsubmitted effect is completed.
            self.journal.archive()
            archive(self.lifecycle, self.lifecycle_store.path)
            for name, owner in self.owners.items():
                if self.snapshots[name] is not None:
                    owner.archive()
            for node, request in self.requests.items():
                result = settle_guest(node, request)
                if result["settlement"]["state"] != "complete":
                    raise ReplanRejected(
                        "HA predecessor guest did not settle", code="predecessor-not-settled"
                    )
                receipt = digest(result)
                if node in record["receipts"] and record["receipts"][node] != receipt:
                    raise ReplanRejected("HA settlement receipt changed")
                record["receipts"][node] = receipt
                self.save(record)
            verify_stopped()
            record["phase"] = "complete"
            self.save(record)


def recovery_member_manifest(state, name, children, reservation, original):
    """Select A or B from exact recorded owners, never from the requested C wheel."""
    from .vm_ha_upgrade_recovery import ARTIFACT, unfinished_recovery

    record = unfinished_recovery(state)
    if record is None or not isinstance(original, dict) or name not in original:
        raise ReplanRejected("Recovery predecessor manifests are unavailable")
    installed = children["runtime"]["artifact"]
    if installed == dict(state.transaction.resource_bindings)[ARTIFACT]:
        manifest = original[name]
    elif installed == record["artifact"] and reservation is not None:
        manifest = reservation["request"]["package"]["desired"]
        package = json.loads(record["packages"])[name]
        if (
            digest(
                {key: manifest[key] for key in ("expected_dependencies", "expected_requirements")}
            )
            != package["closure"]
            or manifest["dependency_wheels"] != package["wheels"]
            or digest(manifest["runtime_plan"]) != package.get("runtime")
        ):
            raise ReplanRejected("Recovery reservation changed its approved package closure")
    else:
        raise ReplanRejected("Recovery guest has an unproven package artifact")
    if manifest.get("wheel_sha256") != installed:
        raise ReplanRejected("Recovery installed artifact differs from its original manifest")
    return manifest


def inspect(
    journal,
    lifecycle_store,
    state,
    *,
    desired,
    artifact,
    cloud,
    evidence,
    credential_plan,
    ssh_policy,
    inspect_guest,
) -> tuple[Settlement, dict]:
    from . import package_reservation, vm_ha_corrective
    from .vm_ha_upgrade_recovery import (
        ARTIFACT,
        host_digest,
        unfinished_recovery,
        validate_host_evidence,
    )

    unfinished = unfinished_recovery(state)
    try:
        if unfinished is None:
            vm_ha_corrective.validate_checkpoint(state)
    except ValueError as error:
        raise ReplanRejected(
            "HA predecessor is not an exact settled host-only checkpoint",
            code="predecessor-not-settled",
        ) from error
    previous, owners, snapshots = settled_owners(journal, state, credential_plan, ssh_policy)
    parent = previous["transaction"]
    runtime = previous["bindings"]["runtime"]
    root = package_reservation.recovery_owner(journal.root, parent["execution_id"], digest(runtime))
    # recovery_owner validates the exact protected child/parent mapping. Bind
    # that authority into the proof so lock-time refresh detects any change.
    operation = operation_id(state, previous, digest(desired), artifact)
    requests, guests, selected_manifests = {}, {}, {}
    locks = {node: value["apply_lock"] for node, value in evidence.items()}
    prior_manifests = json.loads(
        dict(state.transaction.resource_bindings).get("runtime-package-manifests", "null")
    )
    for member in state.members:
        node = member.node_id
        reservation = package_reservation.read(*root, member.instance_name)
        observed = inspect_guest(
            node,
            {
                "action": "replan-inspect",
                "operation": operation,
                "package_reservation": reservation,
            },
        )
        installed_artifact = dict(state.transaction.resource_bindings)[ARTIFACT]
        if unfinished is not None:
            baseline = recovery_member_manifest(
                state, member.instance_name, observed["children"], reservation, prior_manifests
            )
            selected_manifests[member.instance_name] = baseline
            installed_artifact = baseline["wheel_sha256"]
        vm_ha_corrective.completed_children(
            observed["children"],
            parent=state.transaction.operation_id,
            artifact=installed_artifact,
        )
        if observed["boot_id"] != evidence[node]["boot_id"]:
            raise ReplanRejected("HA predecessor boot changed during inspection")
        if (
            evidence[node]["forwarding"] != "0"
            or evidence[node]["guard"].get("guard_boot_id") != observed["boot_id"]
        ):
            raise ReplanRejected(
                "HA settlement requires the retained current-boot fence",
                code="predecessor-not-settled",
            )
        requests[node] = {
            "action": "replan-settle",
            "operation": operation,
            "previous_lock": locks[node],
            "children": observed["children"],
            "boot_id": observed["boot_id"],
            "adoption_sha256": observed["adoption_sha256"],
            "package_reservation": reservation,
        }
        guests[node] = {
            key: observed[key]
            for key in ("children", "boot_id", "adoption_sha256", "package_worker")
        }
    generations = predecessor_generations(
        state, evidence, selected_manifests=selected_manifests if unfinished else None
    )
    require_same_infrastructure(state, desired, generations)
    validate_host_evidence(
        state,
        evidence,
        generations,
        lock_operations={node: value["operation_id"] for node, value in locks.items()},
    )
    proof = {
        "action": "settle-ha-predecessor",
        "operation": operation,
        "predecessor": digest(previous),
        "lifecycle": state.record_sha256,
        "prior_desired": state.transaction.desired_state_digest,
        "desired": digest(desired),
        "artifact": artifact,
        "cloud": digest(cloud),
        "hosts": host_digest(evidence),
        "guests": guests,
        "generations": json.loads(json.dumps(generations)),
        "sources": digest({node: item["source_sha256"] for node, item in evidence.items()}),
        "owners": {
            **{name: digest(value) for name, value in snapshots.items()},
            "package_owner": digest({"execution": root[1], "authority": root[2]}),
        },
        "ssh_trust": ssh_policy.known_hosts_sha256,
    }
    if unfinished is not None:
        proof["recovery_frontier"] = {
            "lifecycle_artifact": dict(state.transaction.resource_bindings)[ARTIFACT],
            "command_artifact": unfinished["artifact"],
            "manifests": {
                m.node_id: manifest_identity(selected_manifests[m.instance_name])
                for m in state.members
            },
        }
    result = Settlement(
        journal, lifecycle_store, state, previous, owners, snapshots, proof, requests
    )
    saved = result.read()
    if saved is not None and saved["phase"] == "complete":
        validate_host_evidence(
            state,
            evidence,
            generations,
            require_stopped=True,
            lock_operations={node: value["operation_id"] for node, value in locks.items()},
        )
    return result, generations
