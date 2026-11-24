"""Connect private HA table preparation to staged activation and fenced cutover."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .vm_ha_lifecycle import VMHALifecycleJournal
    from .vm_manager import VMManager

from ..schema import VMHARuntimeBinding
from .ha_enforced_rebind import PLAN_KEY, PROOF_KEY, successor
from .ha_enforced_tables import TableJournal, Transition


@dataclass
class BoundTransition:
    transition: Transition
    manager: VMManager
    lifecycle: VMHALifecycleJournal
    binding: VMHARuntimeBinding

    def cutover(
        self, *, ssh, locked, local: dict, operation: str, handoffs: dict | None = None
    ) -> None:
        from .ha_enforced_barrier import verify

        barrier: dict[str, dict] = {}
        tx = self.lifecycle.state.transaction
        if tx is None:
            raise RuntimeError("HA route cutover lacks its lifecycle transaction")

        def quiescent(*, install=False):
            barrier.clear()
            for instance, target, receipt, _lock in locked:
                node = instance.vm_ha_node.node_id
                handoff = (handoffs or {}).get(instance.hostname)
                result = (
                    handoff.enforcement_barrier(
                        receipt=receipt,
                        binding=self.binding,
                        operation=operation,
                        install=install,
                    )
                    if handoff is not None
                    else verify(
                        ssh,
                        instance=instance,
                        target=target,
                        local=local,
                        binding=self.binding,
                        receipt=receipt,
                        operation=operation,
                        install=install,
                    )
                )
                member = next(m for m in self.lifecycle.state.members if m.node_id == node)
                barrier[node] = {
                    "compute_id": member.compute_id,
                    "operation": tx.operation_id,
                    **result,
                }
            if set(barrier) != {n.node_id for n in self.binding.nodes}:
                raise RuntimeError("HA route cutover requires both exact stopped writers")

        # Install the desired plus retained predecessor scopes on both fenced
        # guests before publishing any workload-subnet steering.
        quiescent(install=True)
        targets = self.transition.cutover(quiescent=quiescent)
        quiescent()
        wanted = tuple(
            sorted(
                json.dumps(t.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
                for t in targets
            )
        )
        state = self.lifecycle.state
        if state.transaction is None:
            raise RuntimeError("HA route cutover lost its lifecycle transaction")
        recorded = json.loads(dict(state.transaction.resource_bindings).get(PROOF_KEY, "null"))
        if (
            state.route_targets != wanted
            or not recorded
            or recorded["plan"] != self.transition.plan
        ):
            proof = {
                "plan": self.transition.plan,
                "operations": self.transition.journal.state["transaction"]["operations"],
                "operation": state.transaction.operation_id,
                "barrier": {"kind": "stopped-pair", "nodes": barrier},
            }
            if self.manager._vm_ha_effect_spec is None:
                raise RuntimeError("HA route cutover lacks its exact deployment spec")
            self.lifecycle.transition(
                successor(
                    state, proof=proof, observation=self.manager._stable_vm_ha_effect_observation()
                )
            )
        else:
            if (
                not recorded
                or recorded["plan"] != self.transition.plan
                or recorded["operations"]
                != self.transition.journal.state["transaction"]["operations"]
            ):
                raise RuntimeError("HA route cutover completion lacks its exact lifecycle receipt")
        self.manager._vm_ha_route_targets = targets
        self.transition.commit()


def prepare(manager, lifecycle, binding: VMHARuntimeBinding) -> BoundTransition | None:
    tx = lifecycle.state.transaction
    raw = dict(tx.resource_bindings).get(PLAN_KEY) if tx else None
    if raw is None:
        return None
    plan = json.loads(raw)
    journal = TableJournal(lifecycle.state.project_id, lifecycle.state.gateway_name)
    saved = journal.snapshot()
    if (
        saved
        and saved["transaction"]
        and saved["transaction"]["plan"] == plan
        and saved["transaction"]["phase"] == "complete"
    ):
        if saved["bindings"]["targets"] != [
            t.model_dump(mode="json") for t in binding.route_targets
        ]:
            raise RuntimeError("Completed HA route transition differs from current binding")
        return None
    if manager is None:
        raise RuntimeError("Pending HA table transition requires its deployment manager")
    transition = Transition(
        manager._get_client(),
        journal,
        plan,
        allocation=binding.shared_allocation_id,
        ordinary_bindings=dict(tx.resource_bindings),
    )

    def admission():
        if lifecycle.state.transaction.pending_effect is not None:
            from .ha_enforced_resume import admit

            observation = manager._stable_vm_ha_effect_observation()
            if admit(lifecycle.state, observation, saved=journal.snapshot()):
                return
        manager._verify_vm_ha_transaction_preconditions(
            manager._vm_ha_effect_spec, manager._vm_ha_effect_prefixes
        )

    targets = transition.prepare(admit=admission)
    candidate = VMHARuntimeBinding.model_validate(
        {
            **binding.model_dump(mode="json"),
            "route_targets": [t.model_dump(mode="json") for t in targets],
            "route_runtime_id": VMHARuntimeBinding.derive_route_runtime_id(
                binding.cluster_id, binding.shared_allocation_id, targets
            ),
            "migration_routes": [
                r.model_dump(mode="json")
                for r in binding.migration_routes
                if r.route_target in targets
            ],
        }
    )
    return BoundTransition(transition, manager, lifecycle, candidate)
