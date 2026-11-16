"""Confirmed repair retries after terminal provider failures, preserving receipts."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..nebius_pagination import collect_nebius_pages, nebius_resource_id
from .replacement_cloud import Cloud, message_digest
from .vm_ha_cloud import nebius_request_error_code_is, vm_ha_request_kwargs


class RepairCloud(Cloud):
    def settle(self, *, require_success: bool = False) -> None:
        # A terminal failure is settled evidence, not a successful effect. The
        # repair owner independently rereads state before its next mutation.
        super().settle(require_success=require_success)

    def _latest(self, label: str) -> tuple[str, dict | None, int]:
        prefix = label + "-repair-retry-"
        matches = []
        for record in self.records.values():
            candidate = record["label"]
            if candidate == label:
                matches.append((0, record))
            elif candidate.startswith(prefix) and candidate[len(prefix) :].isdigit():
                matches.append((int(candidate[len(prefix) :]), record))
        if not matches:
            return label, None, 0
        ordinal, record = max(matches, key=lambda item: item[0])
        return record["label"], record, ordinal

    def _creation_absent(self, request: Any) -> None:
        from nebius.api.nebius.vpc.v1 import (
            ListSecurityGroupsRequest,
            ListSecurityRulesRequest,
            SecurityGroupServiceClient,
            SecurityRuleServiceClient,
        )

        group = type(request).__name__ == "CreateSecurityGroupRequest"
        service = (
            SecurityGroupServiceClient(self.sdk) if group else SecurityRuleServiceClient(self.sdk)
        )
        request_type = ListSecurityGroupsRequest if group else ListSecurityRulesRequest
        values: tuple[Any, ...] = collect_nebius_pages(
            lambda token: service.list(
                request_type(parent_id=request.metadata.parent_id, page_size=500, page_token=token),
                **vm_ha_request_kwargs(),
            ),
            context="Repair failed quarantine creation",
            item_identity=nebius_resource_id,
        )
        if any(value.metadata.name == request.metadata.name for value in values):
            raise RuntimeError(
                "Failed quarantine creation left an existing resource; retain its receipt for settlement"
            )

    def _deleted(self, request: Any) -> bool:
        from nebius.api.nebius.vpc.v1 import (
            GetSecurityGroupRequest,
            GetSecurityRuleRequest,
            SecurityGroupServiceClient,
            SecurityRuleServiceClient,
        )

        group = type(request).__name__ == "DeleteSecurityGroupRequest"
        service = (
            SecurityGroupServiceClient(self.sdk) if group else SecurityRuleServiceClient(self.sdk)
        )
        request_type = GetSecurityGroupRequest if group else GetSecurityRuleRequest
        try:
            service.get(request_type(id=request.id), **vm_ha_request_kwargs()).wait()
        except Exception as error:
            if nebius_request_error_code_is(error, "NOT_FOUND"):
                return True
            raise
        return False

    def mutate(
        self,
        label: str,
        request: Any,
        submit: Callable[..., Any],
        *,
        allow_terminal_predecessors: bool = True,
    ) -> str:
        self.settle()
        selected, previous, ordinal = self._latest(label)
        if previous is not None and previous["status"] in {"failed", "rejected"}:
            kind = type(request).__name__
            # An update is rebuilt from fresh Compute state and its revision.
            # Every other retry retains the exact original request bytes.
            if kind != "UpdateInstanceRequest" and previous["request_sha256"] != message_digest(
                request
            ):
                raise RuntimeError("Repair retry changed its retained resource request")
            if kind in {"CreateSecurityGroupRequest", "CreateSecurityRuleRequest"}:
                self._creation_absent(request)
            elif kind in {"DeleteSecurityGroupRequest", "DeleteSecurityRuleRequest"}:
                if self._deleted(request):
                    # Do not rewrite failed evidence as success. Typed absence
                    # independently proves cleanup, and is reread on every retry.
                    return str(request.id)
            selected = label + "-repair-retry-" + str(ordinal + 1)
        return super().mutate(
            selected, request, submit, allow_terminal_predecessors=allow_terminal_predecessors
        )

    def completed(self, label: str) -> str | None:
        self.settle()
        _, record, _ = self._latest(label)
        return record["resource_id"] if record and record["status"] == "settled" else None
