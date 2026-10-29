"""Bounded Nebius SDK adapter. Payloads and provider errors never enter diagnostics."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from nebius.api.nebius.common.v1 import ResourceMetadata
from nebius.api.nebius.iam import v1 as iam
from nebius.api.nebius.mysterybox import v1 as box

from .nebius_api_helpers import (
    bounded_nebius_request_kwargs,
    sdk_message_to_mapping,
    wait_nebius_operation,
)


class WireGuardRequestError(RuntimeError):
    """A sanitized provider status; service text is never retained."""

    def __init__(self, status: str, *, context: str = ""):
        self.status_code = status
        self.context = context
        super().__init__(
            f"WireGuard Nebius request failed ({status})"
            f"{': ' + context if context else ''}; check the selected identity and resource permissions"
        )


@contextmanager
def wireguard_api(*, credentials_file: str | None = None) -> Iterator[SecretStashAPI]:
    """Human identity is strict. Automation must explicitly select a credential file."""
    from nebius.sdk import SDK

    from .sdk_auth import NEBIUS_SDK_USER_AGENT_PREFIX, acquire_operator_access_token

    if credentials_file:
        sdk = SDK(
            credentials_file_name=credentials_file,
            domain="api.nebius.cloud",
            user_agent_prefix=NEBIUS_SDK_USER_AGENT_PREFIX,
        )
    else:
        token = acquire_operator_access_token(context="WireGuard SecretStash")
        sdk = SDK(
            credentials=token,
            domain="api.nebius.cloud",
            user_agent_prefix=NEBIUS_SDK_USER_AGENT_PREFIX,
        )
    try:
        yield SecretStashAPI(sdk)
    finally:
        sdk.sync_close()


class SecretStashAPI:
    def __init__(self, sdk: Any):
        self.sdk = sdk
        self.secrets = box.SecretServiceClient(sdk)
        self.versions = box.SecretVersionServiceClient(sdk)
        self.payloads = box.PayloadServiceClient(sdk)
        self.groups = iam.GroupServiceClient(sdk)

    def call(self, method: Any, request: Any, *, operation_key: str = "") -> Any:
        kwargs = bounded_nebius_request_kwargs()
        if operation_key:
            kwargs["metadata"] = [("x-idempotency-key", operation_key)]
        try:
            return method(request, **kwargs).wait()
        except Exception as error:
            from grpc import StatusCode  # type: ignore[import-untyped]

            code = getattr(getattr(error, "status", None), "code", None)
            status = code.name if isinstance(code, StatusCode) else "UNKNOWN"
            raise WireGuardRequestError(status) from None

    def pages(
        self, method: Any, request_type: Any, parent: str, items_field: str = "items"
    ) -> list[Any]:
        if not parent:
            raise RuntimeError("WireGuard discovery requires an exact parent identity")
        token = ""
        seen: set[str] = set()
        values: dict[str, Any] = {}
        for _ in range(1000):
            response = self.call(
                method, request_type(parent_id=parent, page_size=100, page_token=token)
            )
            for value in getattr(response, items_field):
                meta = value.metadata
                if not meta.id or meta.parent_id != parent:
                    raise RuntimeError(
                        "WireGuard discovery returned duplicate or out-of-scope identities"
                    )
                if meta.id in values and sdk_message_to_mapping(
                    values[meta.id]
                ) != sdk_message_to_mapping(value):
                    raise RuntimeError(
                        "WireGuard discovery returned conflicting duplicate identities"
                    )
                values[meta.id] = value
                if len(values) > 100000:
                    raise RuntimeError("WireGuard discovery exceeded its completeness bound")
            token = response.next_page_token
            if not isinstance(token, str):
                raise RuntimeError("WireGuard discovery returned malformed pagination")
            if not token:
                return list(values.values())
            if token in seen:
                raise RuntimeError("WireGuard discovery repeated a pagination token")
            seen.add(token)
        raise RuntimeError("WireGuard discovery did not complete within its page bound")

    def admin_groups(self, tenant: str, project: str) -> list[dict[str, str]]:
        if not tenant or not project or tenant == project:
            raise RuntimeError("WireGuard requires exact tenant and project identities")
        result: dict[str, dict[str, str]] = {}
        for scope, parent in (("tenant", tenant), ("project", project)):
            for group in self.pages(self.groups.list, iam.ListGroupsRequest, parent):
                meta = group.metadata
                item = {"id": meta.id, "name": meta.name, "parent_id": parent, "scope": scope}
                if not meta.name or (meta.id in result and result[meta.id] != item):
                    raise RuntimeError(
                        "WireGuard IAM group discovery returned conflicting identity"
                    )
                result[meta.id] = item
        return sorted(result.values(), key=lambda item: (item["name"], item["id"]))

    def validate_group(self, group_id: str, tenant: str, project: str) -> None:
        group = self.call(self.groups.get, iam.GetGroupRequest(id=group_id))
        if group.metadata.id != group_id or group.metadata.parent_id not in {tenant, project}:
            raise RuntimeError("WireGuard administrator group is outside the selected scope")

    def direct_broad_payload_grants(
        self, group_id: str, tenant: str, project: str
    ) -> list[dict[str, str]]:
        """Inspect direct ancestor grants, without claiming effective-access isolation."""
        permits = iam.AccessPermitServiceClient(self.sdk)
        scopes = {tenant: "tenant", project: "project"}
        return [
            {"id": permit.metadata.id, "role": permit.spec.role, "scope": scopes[resource]}
            for permit in self.pages(permits.list, iam.ListAccessPermitRequest, group_id)
            if (resource := permit.spec.resource_id) in scopes
            and permit.spec.role in {"admin", "mysterybox.payload-viewer"}
        ]

    def admit_backend(self, settings: Any) -> None:
        from nebius.api.nebius.storage.v1 import BucketServiceClient, GetBucketByNameRequest

        from .object_storage_admission import validate_backend_bucket

        bucket = self.call(
            BucketServiceClient(self.sdk).get_by_name,
            GetBucketByNameRequest(parent_id=settings.project_id, name=settings.bucket),
        )
        validate_backend_bucket(settings, bucket)

    def verify_instance_binding(
        self, own: Mapping[str, str], record: Mapping[str, Any], *, require_running: bool = True
    ) -> None:
        import ipaddress

        from nebius.api.nebius.compute.v1 import GetInstanceRequest, InstanceServiceClient

        vm = self.call(
            InstanceServiceClient(self.sdk).get, GetInstanceRequest(id=own["instance_id"])
        )
        if (
            vm.metadata.id != own["instance_id"]
            or vm.metadata.parent_id != record["project_id"]
            or not vm.metadata.resource_version
            or vm.spec.boot_disk.existing_disk.id != own["boot_disk_id"]
            or vm.spec.service_account_id != record["bootstrap"]["service_account_id"]
            or (require_running and vm.status.state.name != "RUNNING")
            or len(vm.status.network_interfaces) != 1
        ):
            raise RuntimeError("WireGuard Compute identity differs from deployment intent")
        public = vm.status.network_interfaces[0].public_ip_address
        if public.allocation_id != own["public_allocation_id"] or ipaddress.ip_interface(
            public.address
        ).ip != ipaddress.ip_address(own["public_ip"]):
            raise RuntimeError("WireGuard endpoint differs from deployment intent")

    def verify_instance_absent(self, instance_id: str) -> None:
        from nebius.api.nebius.compute.v1 import GetInstanceRequest, InstanceServiceClient

        try:
            self.call(InstanceServiceClient(self.sdk).get, GetInstanceRequest(id=instance_id))
        except WireGuardRequestError as error:
            if error.status_code == "NOT_FOUND":
                return
            raise
        raise RuntimeError("WireGuard instance still exists; access retention is not verified")

    def reconcile_effect(self, journal: Any, *, operation_id: str = "") -> None:
        """Called only with explicit stopped-writer authority and the exact held lock."""
        from nebius.api.nebius.common.v1 import GetOperationRequest

        pending = journal.value.get("in_flight")
        if not pending:
            return
        recorded = pending.get("operation_id", "")
        if recorded and operation_id and recorded != operation_id:
            raise RuntimeError("WireGuard recovery operation differs from its journal")
        operation_id = recorded or operation_id
        if not operation_id:
            raise RuntimeError(
                "Cloud dispatch acknowledgement was lost; supply the exact provider operation ID after independent investigation. No key will be regenerated"
            )
        if pending["collection"] not in {"versions", "publication"}:
            raise RuntimeError("WireGuard SDK recovery cannot adopt Terraform-owned resources")
        clients = {
            "SecretServiceClient": box.SecretServiceClient,
            "SecretVersionServiceClient": box.SecretVersionServiceClient,
        }
        if pending["client"] not in clients:
            raise RuntimeError("Unsupported WireGuard recovery service")
        client = clients[pending["client"]](self.sdk)
        operation = self.call(client.operation_service().get, GetOperationRequest(id=operation_id))
        if operation.id != operation_id:
            raise RuntimeError("WireGuard recovery returned another operation")
        if not recorded:
            headers = operation.raw().request_headers
            if "x-idempotency-key" not in headers or list(headers["x-idempotency-key"].values) != [
                pending["nonce"]
            ]:
                raise RuntimeError(
                    "Supplied provider operation does not match the frozen request identity"
                )
        if not operation.done():
            raise RuntimeError(
                "WireGuard provider operation is still running; remote lock retained"
            )
        if not operation.successful():
            raise RuntimeError(
                "WireGuard provider operation failed; retain the journal and inspect its exact resources before explicit repair"
            )
        if pending["collection"] == "versions":
            if not operation.resource_id:
                raise RuntimeError("WireGuard completed operation has no resource identity")
            intent = journal.value[pending["collection"]][pending["key"]]
            intent.update(id=operation.resource_id, operation_id=operation_id)
        journal.value.pop("in_flight")
        journal.save()

    def payload(self, secret_id: str, version_id: str = "") -> tuple[str, dict[str, str]]:
        result = self.call(
            self.payloads.get, box.GetPayloadRequest(secret_id=secret_id, version_id=version_id)
        )
        values: dict[str, str] = {}
        if not result.version_id or (version_id and version_id != result.version_id):
            raise RuntimeError("WireGuard SecretStash returned a different version")
        for entry in result.data:
            if not entry.key or entry.key in values or not entry.check_presence("string_value"):
                raise RuntimeError("WireGuard SecretStash payload schema is invalid")
            values[entry.key] = entry.string_value
        if len(json.dumps(values)) > 32768:
            raise RuntimeError("WireGuard SecretStash payload exceeds its size bound")
        return result.version_id, values

    def _wait(self, operation: Any, journal: Any, intent: dict[str, Any]) -> str:
        intent["operation_id"] = operation.id
        journal.value["in_flight"]["operation_id"] = operation.id
        journal.save()
        try:
            wait_nebius_operation(
                operation, timeout_seconds=180, action="WireGuard resource operation"
            )
        except Exception:
            raise RuntimeError(
                "WireGuard resource operation is unconfirmed; retain its journal and reconcile before retry"
            ) from None
        resource = operation.resource_id
        if not resource:
            raise RuntimeError("WireGuard operation returned no resource identity")
        intent["id"] = resource
        journal.value.pop("in_flight")
        journal.save()
        return resource

    def verify_access_resources(
        self, resources: dict[str, str], *, project: str, admin_group: str, names: dict[str, str]
    ) -> None:
        """Independent exact-ID metadata/IAM verification; never read payloads here."""
        required = {
            "login",
            "host",
            "connection",
            "account",
            "group",
            "membership",
            "host-permit",
            "login-permit",
            "connection-permit",
        }
        if set(resources) != required or len(set(resources.values())) != len(required):
            raise RuntimeError("WireGuard Terraform outputs have invalid resource identities")
        for role in ("login", "host", "connection"):
            item = self.call(self.secrets.get, box.GetSecretRequest(id=resources[role]))
            if (
                item.metadata.id != resources[role]
                or item.metadata.parent_id != project
                or item.metadata.name != names[role]
                or item.status.state != box.SecretStatus.State.ACTIVE
            ):
                raise RuntimeError("WireGuard secret metadata does not match its Terraform owner")
        account = self.call(
            iam.ServiceAccountServiceClient(self.sdk).get,
            iam.GetServiceAccountRequest(id=resources["account"]),
        )
        group = self.call(self.groups.get, iam.GetGroupRequest(id=resources["group"]))
        if (
            account.metadata.id != resources["account"]
            or account.metadata.parent_id != project
            or group.metadata.id != resources["group"]
            or group.metadata.parent_id != project
        ):
            raise RuntimeError("WireGuard VM identity scope differs from its Terraform owner")
        memberships = self.pages(
            iam.GroupMembershipServiceClient(self.sdk).list_members,
            iam.ListGroupMembershipsRequest,
            resources["group"],
            "memberships",
        )
        if (
            len(memberships) != 1
            or memberships[0].metadata.id != resources["membership"]
            or memberships[0].spec.member_id != resources["account"]
        ):
            raise RuntimeError("WireGuard VM group membership differs from its narrow binding")
        permits = iam.AccessPermitServiceClient(self.sdk)
        group_permits = self.pages(
            permits.list, iam.ListAccessPermitRequest, resources["group"], "items"
        )
        if len(group_permits) != 1 or group_permits[0].metadata.id != resources["host-permit"]:
            raise RuntimeError("WireGuard VM group has unexpected access permits")
        for role, parent in (
            ("host", resources["group"]),
            ("login", admin_group),
            ("connection", admin_group),
        ):
            permit = self.call(
                permits.get, iam.GetAccessPermitRequest(id=resources[role + "-permit"])
            )
            if (
                permit.metadata.id != resources[role + "-permit"]
                or permit.metadata.parent_id != parent
                or permit.spec.resource_id != resources[role]
                or permit.spec.role != "mysterybox.payload-viewer"
            ):
                raise RuntimeError("WireGuard payload permit differs from its narrow binding")

    def version(
        self, journal: Any, record: dict[str, Any], key: str, secret: str, payload_factory: Any
    ) -> str:
        """Create an immutable, nonprimary version with exact operation reconciliation."""
        import uuid

        intents = record.setdefault("versions", {})
        intent = intents.get(key)
        first = intent is None
        if first:
            intent = {"nonce": str(uuid.uuid4()), "secret": secret}
            intents[key] = intent
            journal.save()
        if intent["secret"] != secret:
            raise RuntimeError("WireGuard version parent changed")
        if intent.get("id"):
            # Version labels are not retained by SecretStash. Ownership comes
            # from the successful operation's resource ID recorded by _wait or
            # explicit operation reconciliation, never from payload discovery.
            version = self.call(self.versions.get, box.GetSecretVersionRequest(id=intent["id"]))
            if version.metadata.id != intent["id"] or version.metadata.parent_id != secret:
                raise RuntimeError("WireGuard version identity conflicts with journal")
            if version.status.state != box.SecretVersionStatus.State.ACTIVE:
                raise RuntimeError("WireGuard recorded version is not active")
            return intent["id"]
        if intent.get("operation_id") or journal.value.get("in_flight"):
            raise RuntimeError(
                "WireGuard version creation is ambiguous; reconcile recorded operation before retry"
            )
        payload = payload_factory()
        journal.begin_effect(
            client="SecretVersionServiceClient",
            nonce=intent["nonce"],
            collection="versions",
            key=key,
        )
        operation = self.call(
            self.versions.create,
            box.CreateSecretVersionRequest(
                metadata=ResourceMetadata(parent_id=secret),
                spec=box.SecretVersionSpec(
                    payload=[box.Payload(key=k, string_value=v) for k, v in payload.items()],
                    set_primary=False,
                ),
            ),
            operation_key=intent["nonce"],
        )
        return self._wait(operation, journal, intent)

    def publish(self, journal: Any, secret: str, version: str, expected: str) -> None:
        import uuid

        current = self.call(self.secrets.get, box.GetSecretRequest(id=secret))
        # The SDK represents an unset primary as None on a new secret.
        current_primary = current.spec.primary_version_id or ""
        if current_primary == version:
            return
        if current_primary != expected:
            raise RuntimeError("WireGuard secret primary changed concurrently")
        # SecretStash may omit resource_version. Preserve the complete returned
        # metadata (including a version when supplied) and serialize cxcli writers
        # through the journal; do not mistake an absent version for a conflict.
        current.spec.primary_version_id = version
        nonce = str(uuid.uuid4())
        journal.begin_effect(
            client="SecretServiceClient", nonce=nonce, collection="publication", key=secret
        )
        operation = self.call(
            self.secrets.update,
            box.UpdateSecretRequest(
                metadata=current.metadata,
                spec=current.spec,
            ),
            operation_key=nonce,
        )
        journal.value["in_flight"]["operation_id"] = operation.id
        journal.save()
        try:
            wait_nebius_operation(operation, timeout_seconds=180, action="WireGuard publication")
        except Exception:
            raise RuntimeError(
                "WireGuard publication is unconfirmed; reconcile its primary version"
            ) from None
        observed = self.call(self.secrets.get, box.GetSecretRequest(id=secret))
        if observed.spec.primary_version_id != version:
            raise RuntimeError("WireGuard publication readback changed")
        journal.value.pop("in_flight")
        journal.save()
