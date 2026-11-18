"""Deployment-only settlement of one recorded predecessor route mutation.

The caller holds the HA writer locks and keeps both forwarding guards closed.
Run this source in a fresh predecessor process before replacing its package or
configuration. It may finish only the recorded operation, never plan new work.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

CONFIG = Path("/etc/nebius-vpngw/config-resolved.yaml")


def _require_writers_stopped() -> None:
    from nebius_vpngw import ordinary_operations as operations

    manager = operations.Manager(time.monotonic() + 10)
    if not all(manager.stopped(unit) for unit in (*operations.MANAGEMENT, *operations.HA_WRITERS)):
        raise RuntimeError("predecessor route settlement requires stopped local writers")


def settle_locked(expected: dict, *, artifact: str) -> None:
    from nebius_vpngw import ha_repair as repair

    state = repair.STATE
    pending_path = state / "route-pending.json"
    if not pending_path.exists() and not pending_path.is_symlink():
        return
    repair.exact_lock(expected, state_dir=state)
    _require_writers_stopped()
    config_path = CONFIG
    raw = repair.verified_file(config_path, repair.sha(config_path.read_bytes()))
    original = repair.verified_file(pending_path, repair.sha(pending_path.read_bytes()))
    # Import only after exact file admission. Missing/damaged predecessor code
    # blocks before installation; it never licenses speculative cloud recovery.
    from nebius_vpngw.agent.main import _read_vm_ha_config
    from nebius_vpngw.agent.vm_ha.runtime import build_runtime_ports
    from nebius_vpngw.agent.vm_ha.store import AtomicGenerationStore, atomic_write_json

    config = _read_vm_ha_config(config_path)
    if (
        config is None
        or config["cluster_id"] != expected["cluster_id"]
        or config["node"]["node_id"] != expected["node_id"]
    ):
        raise RuntimeError("pending route settlement crossed its predecessor node")
    boot = repair.BOOT.read_text().strip()
    store = AtomicGenerationStore(state / "generation-store")
    ports = build_runtime_ports(
        config, state_dir=state, replay_store=store, peer_heartbeat_store=store, boot_id=boot
    )
    try:
        routes = ports.routes
        pending = routes.store.load_pending_mutation()
        if pending is None:
            raise RuntimeError("predecessor route intent disappeared before settlement")
        context, mutation = pending.context, pending.mutation
        binding = routes.binding
        if (
            context.generation_id != binding.generation_id
            or context.configuration_digest != binding.configuration_digest
            or context.static_routes_digest != binding.static_routes_digest
            or context.bgp_policy_digest != binding.bgp_policy_digest
            or mutation.cluster_id != binding.cluster_id
            or mutation.allocation_id != binding.shared_allocation_id
            or mutation.route_target not in binding.route_targets
        ):
            raise RuntimeError("pending route intent differs from the predecessor binding")
        frozen = routes.store._mutation_payload(mutation)

        def admit() -> None:
            repair.exact_lock(expected, state_dir=state)
            _require_writers_stopped()
            guard = repair.read_json(state / "guard.json")
            if (
                repair.BOOT.read_text().strip() != boot
                or config_path.read_bytes() != raw
                or guard.get("guard_boot_id") != boot
                or guard.get("data_plane_mode") not in {"blocked", "passive"}
                or repair.FORWARDING.read_text().strip() != "0"
            ):
                raise RuntimeError("predecessor route settlement lost deployment fencing")
            for name in (
                "accepted-cloud-operation.json",
                "rearm-cloud-operation.json",
                "standby-auto-healing-recovery.json",
                "transfer-lineage.json",
            ):
                if (state / name).exists() or (state / name).is_symlink():
                    raise RuntimeError("predecessor route settlement conflicts with a cloud writer")
            current = routes.store.load_pending_mutation()
            if (
                current is None
                or current.context != context
                or routes.store._mutation_payload(current.mutation) != frozen
                or not context.matches(routes._ownership(require_takeover_fence=False))
            ):
                raise RuntimeError("predecessor route settlement lost exact mutation authority")

        admit()
        backend: Any = routes.backend
        accepted = backend._accepted_operation

        def accepted_operation(action, current, submit):
            admit()
            if routes.store._mutation_payload(current) != frozen:
                raise RuntimeError("predecessor settlement attempted another route mutation")

            def submit_original():
                admit()
                return submit()

            return accepted(action, current, submit_original)

        backend._accepted_operation = accepted_operation
        backend.admit_effect = admit
        # This object is deployment-owned and cannot enter general reconcile.
        # The runtime gate remains mandatory on every production-built object.
        routes.admission = None
        backend.set_reconciliation_operation_id(context.operation_id)
        ledger = routes.store.load_ledger()
        before_ledger = hashlib.sha256(
            json.dumps({key: repr(value) for key, value in ledger.items()}, sort_keys=True).encode()
        ).hexdigest()
        if pending.phase.value != "restored":
            try:
                routes._apply_mutation(mutation, ledger)
            except RuntimeError:
                current = routes.store.load_pending_mutation()
                if current is None or current.phase.value != "restored":
                    raise
        current = routes.store.load_pending_mutation()
        if current is not None and current.phase.value == "restored":
            admit()
            restored = backend.recover_restored_route(current.mutation)
            if not restored:
                raise RuntimeError("compensated predecessor route cannot be reobserved")
            routes._record_compensated_route(current.mutation, restored, ledger)
            routes.store.clear_pending_mutation(current)
        if routes.store.load_pending_mutation() is not None:
            raise RuntimeError("predecessor route mutation remains unresolved")
        repair.exact_lock(expected, state_dir=state)
        if config_path.read_bytes() != raw or repair.FORWARDING.read_text().strip() != "0":
            raise RuntimeError("predecessor changed before settlement receipt")
        atomic_write_json(
            state / "route-settlement.json",
            {
                "schema": "nebius-vpngw.route-settlement.v1",
                "operation": expected["operation_id"],
                "artifact": artifact,
                "config_sha256": hashlib.sha256(raw).hexdigest(),
                "pending_sha256": hashlib.sha256(original).hexdigest(),
                "context": context.to_dict(),
                "ledger_before": before_ledger,
                "ledger_after": hashlib.sha256(
                    json.dumps(
                        {key: repr(value) for key, value in routes.store.load_ledger().items()},
                        sort_keys=True,
                    ).encode()
                ).hexdigest(),
            },
        )
    finally:
        ports.close()


def command(expected: dict, artifact: str) -> list[str]:
    """Use a fresh interpreter so streamed admission cannot mask installed code."""
    import sys

    source = globals().get("_SOURCE") or Path(__file__).read_bytes()
    return [
        sys.executable,
        "-B",
        "-c",
        f"exec({source!r});settle_locked({expected!r},artifact={artifact!r})",
    ]
