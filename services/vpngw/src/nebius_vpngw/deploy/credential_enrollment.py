"""Exact IAM operation receipts beneath the managed credential owner.

The parent command selects the existing credential plan. This journal retains
that authority and public digests, never private keys or access tokens. Local
credential publication remains owned by vm_ha_managed_credentials.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..apply_approval import digest
from .apply_command import CommandJournal
from .apply_plan import CommandPlan, Progress
from .credential_enrollment_schema import REQUESTS, _actions, validate
from .replacement import Journal
from .replacement_cloud import Cloud, message_digest

if TYPE_CHECKING:
    from ..vm_ha_managed_credentials import VMHAManagedCredentialPlan, VMHAManagedCredentialResult


class EnrollmentJournal(Journal):
    schema = "nebius-vpngw.credential-enrollment.v1"

    def __init__(self, project: str, gateway: str, *, root: Path | None = None) -> None:
        super().__init__(
            project,
            gateway,
            root=root
            or Path.home() / ".local/state/nebius-vpngw/credential-enrollment" / project / gateway,
        )

    def validate(self, state: dict) -> None:
        validate(state, schema=self.schema, project=self.project, gateway=self.gateway)

    def begin(self, intent: dict, *, approval: str, execution_id: str) -> None:
        current = self.state["transaction"]
        if current is not None:
            if (
                current["intent"] == intent
                and current["approval"] == approval
                and current["execution_id"] == execution_id
            ):
                return
            if current["phase"] != "complete" or current["execution_id"] == execution_id:
                raise RuntimeError("Credential enrollment requires its original command")
            self.archive()
        self.state["transaction"] = {
            "intent": json.loads(json.dumps(intent)),
            "approval": approval,
            "execution_id": execution_id,
            "phase": "pending",
            "selected": None,
            "operations": {},
            "receipt": None,
        }
        self.save()

    def select(self, public_key: str) -> None:
        fingerprint = hashlib.sha256(public_key.encode()).hexdigest()
        current = self.state["transaction"]
        if current["selected"] is not None:
            if current["selected"] != fingerprint:
                raise RuntimeError("Selected credential key changed during enrollment")
            return
        current["selected"] = fingerprint
        self.save()

    def permits_unpaired_pending_key(self) -> bool:
        """Only an unstarted approved enrollment may repair local publication."""
        snapshot = self.snapshot()
        current = snapshot["transaction"] if snapshot else None
        return bool(
            current
            and current["phase"] == "pending"
            and current["intent"]["local_source_expected_absent"]
            and not current["operations"]
        )

    def bind_observation(
        self, observed: VMHAManagedCredentialPlan, *, intent: dict, approval: str, execution_id: str
    ) -> VMHAManagedCredentialPlan:
        """Read-only planning binds resumed observations to the original command."""
        snapshot = self.snapshot()
        current = snapshot["transaction"] if snapshot else None
        if current is None:
            if observed.observation_record() != intent:
                raise RuntimeError("Unstarted credential intent changed before enrollment")
        elif (
            current["intent"] != intent
            or current["approval"] != approval
            or current["execution_id"] != execution_id
        ):
            raise RuntimeError("Credential enrollment belongs to another command")
        else:
            self._admit_observation(observed, current)
        return replace(observed, command_intent=json.loads(json.dumps(intent)))

    def _admit_observation(
        self, observed: VMHAManagedCredentialPlan, current: dict
    ) -> VMHAManagedCredentialPlan:
        """Reconstruct original IAM actions only from this command's receipts."""
        original = current["intent"]
        actual = observed.observation_record()
        changing = {
            "action",
            "local_source_expected_absent",
            "runtime_credentials",
            "iam_resources",
        }
        if {k: v for k, v in original.items() if k not in changing} != {
            k: v for k, v in actual.items() if k not in changing
        }:
            raise RuntimeError("Credential enrollment intent changed")
        if current["selected"] is not None and observed.public_key_sha256 != current["selected"]:
            raise RuntimeError("Selected credential key changed during enrollment")
        records = {record["label"]: record for record in current["operations"].values()}
        for label, desired in _actions(original).items():
            live = _actions(actual)[label]
            record = records.get(label)
            if desired["action"] == "reuse":
                if live != desired:
                    raise RuntimeError("Approved reused IAM identity changed")
            elif live["action"] == "reuse":
                if record is None or record["status"] not in {"intent", "accepted", "settled"}:
                    raise RuntimeError("Appeared IAM resource has no command operation receipt")
                if live.get("id") and record["resource_id"] and live["id"] != record["resource_id"]:
                    raise RuntimeError("Appeared IAM identity differs from its operation receipt")
            elif live["action"] != "create" or record and record["status"] == "settled":
                raise RuntimeError("Created IAM resource disappeared during enrollment")
        if current["receipt"] is not None and (
            observed.credentials is None
            or observed.credentials.resource_bindings() != current["receipt"]
        ):
            raise RuntimeError("Published credential identity changed")
        graph = original["iam_resources"]
        return replace(
            observed,
            iam_plan=replace(
                observed.iam_plan,
                service_account_action=graph["service_account"]["action"],
                service_account_id=graph["service_account"]["id"],
                group_action=graph["group"]["action"],
                group_id=graph["group"]["id"],
                membership_action=graph["membership"]["action"],
                permit_action=graph["project_editor_permit"]["action"],
            ),
            authorized_key_action=graph["authorized_key"]["action"],
            authorized_key_id=graph["authorized_key"]["id"],
        )

    def admit(self, observed: VMHAManagedCredentialPlan) -> VMHAManagedCredentialPlan:
        current = self.state["transaction"]
        if self._lock is None or current is None:
            raise RuntimeError("Enrollment requires its active exact writer")
        if observed.command_intent is not None and observed.command_intent != current["intent"]:
            raise RuntimeError("Credential approval differs from its command")
        return self._admit_observation(observed, current)

    def cloud(self, sdk: Any, public_key: str) -> Cloud:
        self.select(public_key)

        def restore(record: dict) -> Any:
            import nebius.api.nebius.iam.v1 as iam
            from google.protobuf.json_format import ParseDict  # type: ignore[import-untyped]

            if REQUESTS.get(record["label"]) != record["request_type"]:
                raise RuntimeError("Enrollment request type changed")
            request = getattr(iam, record["request_type"])()
            ParseDict(record["request_metadata"], request.__pb2_message__)
            if record["label"] == "authorized-key":
                if record["public_key_sha256"] != self.state["transaction"]["selected"]:
                    raise RuntimeError("Enrollment public key changed")
                request.spec.data = public_key
            if message_digest(request) != record["request_sha256"]:
                raise RuntimeError("Enrollment request reconstruction changed")
            return request

        return Cloud(
            sdk,
            self,
            member_index=0,
            operation_scope=self.state["transaction"]["execution_id"],
            restore_request=restore,
            admit_request=lambda request: self._admit_iam_request(sdk, request),
        )

    def _admit_iam_request(self, sdk: Any, request: Any) -> None:
        """Recheck the dedicated group immediately before a grant or its replay."""
        import nebius.api.nebius.iam.v1 as iam

        from ..vm_ha_managed_credentials import vm_ha_ownership_labels
        from ..vpngw_sa import _list_access_permits, _list_group_member_ids, _require_named_identity
        from .vm_ha_cloud import vm_ha_request_kwargs

        kind = type(request).__name__
        if kind not in {"CreateAccessPermitRequest", "CreateGroupMembershipRequest"}:
            return
        current = self.state["transaction"]
        actions = _actions(current["intent"])
        records = {record["label"]: record for record in current["operations"].values()}
        group_id = actions["group"]["id"] or records["group"]["resource_id"]
        account_id = actions["service-account"]["id"] or records["service-account"]["resource_id"]
        if not group_id or not account_id or request.metadata.parent_id != group_id:
            raise RuntimeError("IAM grant has no exact approved parent identity")
        group = (
            iam.GroupServiceClient(sdk)
            .get(iam.GetGroupRequest(id=group_id), **vm_ha_request_kwargs())
            .wait()
        )
        if (
            _require_named_identity(
                group,
                kind="Managed VM-HA group",
                project_id=self.project,
                expected_name=current["intent"]["service_account_name"],
                ownership_labels=vm_ha_ownership_labels(
                    project_id=self.project, gateway_name=self.gateway
                ),
            )
            != group_id
        ):
            raise RuntimeError("IAM grant parent identity changed")
        members = _list_group_member_ids(iam.GroupMembershipServiceClient(sdk), group_id)
        permits = _list_access_permits(iam.AccessPermitServiceClient(sdk), group_id)
        expected = ((self.project, "editor"),)
        if kind == "CreateAccessPermitRequest":
            attempted = records["permit"]["submission_count"] > 0
            valid = (
                members == (account_id,)
                and permits in (((), expected) if attempted else ((),))
                and request.spec.resource_id == self.project
                and request.spec.role == "editor"
            )
        else:
            attempted = records["membership"]["submission_count"] > 0
            expected_permits = expected if actions["permit"]["action"] == "reuse" else ()
            valid = (
                members in (((), (account_id,)) if attempted else ((),))
                and permits == expected_permits
                and request.spec.member_id == account_id
            )
        if not valid:
            raise RuntimeError("Dedicated IAM group changed before permission submission")

    def complete(self, bindings: dict) -> dict:
        current = self.state["transaction"]
        if current["receipt"] is not None:
            if current["receipt"] != bindings:
                raise RuntimeError("Published credential identity changed")
            return self.receipt()
        candidate = {
            **self.state,
            "transaction": {**current, "phase": "complete", "receipt": bindings},
        }
        self.validate(candidate)
        self.state = candidate
        self.save()
        return self.receipt()

    def receipt(self) -> dict:
        current = self.state["transaction"]
        if current is None or current["phase"] != "complete":
            raise RuntimeError("Credential enrollment has no terminal receipt")
        return {
            "intent": digest(current["intent"]),
            "selected": current["selected"],
            "bindings": digest(current["receipt"]),
            "operations": digest(current["operations"]),
            "execution_id": current["execution_id"],
        }


def execute(
    command: CommandJournal,
    child: EnrollmentJournal,
    observed: VMHAManagedCredentialPlan,
    *,
    node_ids: tuple[str, ...],
    tenant_id: str | None,
    region_id: str | None,
    key: str = "credentials",
    ensure: Callable[..., VMHAManagedCredentialResult] | None = None,
) -> VMHAManagedCredentialResult:
    """Run the existing secret owner only under the exact active parent effect."""
    from ..vm_ha_managed_credentials import ensure_managed_vm_ha_credentials

    current = command.snapshot()
    if command._lock is None or current is None or current != command.state:
        raise RuntimeError("Enrollment requires the active exact command writer")
    parent = current["transaction"]
    if parent is None:
        raise RuntimeError("Enrollment has no approved command")
    plan = CommandPlan.parse(parent["plan"])
    effect = next((item for item in plan.effects if item.key == key), None)
    intent = current["bindings"].get(key)
    if (
        effect is None
        or effect.owner != "managed-credentials"
        or intent is None
        or effect.request_sha256 != digest(intent)
        or set(effect.targets) != set(node_ids)
        or len(node_ids) != len(set(node_ids))
        or parent["progress"][key] not in {Progress.STARTED.value, Progress.VERIFIED.value}
        or child.project != command.project
        or child.gateway != command.gateway
    ):
        raise RuntimeError("Enrollment differs from its approved command")
    with child:
        if parent["progress"][key] == Progress.VERIFIED.value and (
            child.state["transaction"] is None or child.state["transaction"]["phase"] != "complete"
        ):
            raise RuntimeError("Completed enrollment lacks its original child receipt")
        child.begin(intent, approval=parent["approval"], execution_id=parent["execution_id"])
        if parent["progress"][key] == Progress.VERIFIED.value and parent["receipts"][key] != {
            "owner": effect.owner,
            "sha256": digest(child.receipt()),
        }:
            raise RuntimeError("Completed enrollment has a different child receipt")
        bound = child.bind_observation(
            observed,
            intent=intent,
            approval=parent["approval"],
            execution_id=parent["execution_id"],
        )
        result = (ensure or ensure_managed_vm_ha_credentials)(
            bound, node_ids=node_ids, tenant_id=tenant_id, region_id=region_id, enrollment=child
        )
        if command.snapshot() != current or command.state != current:
            raise RuntimeError("Enrollment command changed during credential publication")
        if (
            set(result.credentials.by_node()) != set(node_ids)
            or result.credentials.resource_bindings() != child.state["transaction"]["receipt"]
        ):
            raise RuntimeError("Published credentials differ from the command scope")
        command.verified(key, child.receipt())
        return result
