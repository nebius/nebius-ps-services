"""Cooperative tracked SDK writes for reviewed adapters; not a sandbox."""

import hashlib

from live_state import StateError


class Actions:
    def __init__(self, journal):
        self.journal = journal

    def create(self, key, client, request, *, kind, dependencies=(), timeout=300):
        from sdk.runtime import submit

        j = self.journal
        project = j.data["plan"]["project"]
        parent = request.metadata.parent_id
        if parent != project and not any(
            j.data["resources"].get(dep, {}).get("id") == parent for dep in dependencies
        ):
            raise StateError("RESOURCE_SCOPE")
        if request.metadata.labels.get("live-test-run") != j.data["run_id"]:
            raise StateError("RUN_LABEL_REQUIRED")
        if kind not in j.data["evidence"].get("resource_baseline", {}):
            raise StateError("BASELINE_REQUIRED")
        request.metadata.labels["live-test-intent"] = key
        token = j.intent(
            key,
            hashlib.sha256(str(request).encode()).hexdigest(),
            descriptor={
                "kind": kind,
                "parent": parent,
                "dependencies": list(dependencies),
                "action": "create",
            },
        )

        def accepted(receipt):
            j.accepted(key, receipt.operation_id, receipt.resource_id)
            if receipt.resource_id:
                j.own(
                    key,
                    resource_id=receipt.resource_id,
                    kind=kind,
                    parent=parent,
                    owner="sdk",
                    dependencies=dependencies,
                )

        result = submit(
            client.create,
            request,
            timeout=timeout,
            idempotency_key=token,
            on_accepted=accepted,
        )
        j.own(
            key,
            resource_id=result.resource_id,
            kind=kind,
            parent=parent,
            owner="sdk",
            dependencies=dependencies,
        )
        j.terminal(key, True)
        return result.resource_id

    def delete(self, key, client, request, observed, *, timeout=300):
        from sdk.runtime import submit

        j = self.journal
        j.deletion_target(
            key,
            observed_id=observed.metadata.id,
            observed_parent=observed.metadata.parent_id,
            observed_run=observed.metadata.labels.get("live-test-run"),
            owner="sdk",
        )
        if request.id != observed.metadata.id:
            raise StateError("DELETE_REQUEST_MISMATCH")
        prefix = "delete:" + key
        previous = [
            r
            for k, r in j.data["operations"].items()
            if k == prefix or k.startswith(prefix + ":")
        ]
        if any(r["status"] != "failed" for r in previous):
            raise StateError("RECONCILE_BEFORE_RESUBMIT")
        operation_key = (
            prefix if not previous else prefix + ":" + str(len(previous) + 1)
        )
        token = j.intent(
            operation_key,
            hashlib.sha256(str(request).encode()).hexdigest(),
            deleting=True,
            descriptor={
                "kind": j.data["resources"][key]["kind"],
                "action": "delete",
                "resource_key": key,
                "target_id": observed.metadata.id,
            },
        )
        result = submit(
            client.delete,
            request,
            timeout=timeout,
            idempotency_key=token,
            on_accepted=lambda r: j.accepted(
                operation_key, r.operation_id, r.resource_id
            ),
        )
        j.terminal(operation_key, True)
        return result
