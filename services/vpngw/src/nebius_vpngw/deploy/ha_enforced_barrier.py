"""Read back both stopped HA writers immediately before workload-table CAS."""

from __future__ import annotations

import base64
from pathlib import Path


def retire_prior_ledger(installed: dict, staged: dict, repair) -> None:
    """Relinquish old table targets while the deployment owns stopped writers.

    Cloud rows remain covered by the table transaction's source snapshots and
    retired-route history. The new runtime discovers the copied rows through
    their exact cloud authority labels; it cannot manage the former tables.
    """
    from nebius_vpngw.agent.vm_ha.runtime import RuntimeStateStore
    from nebius_vpngw.schema import VMHARuntimeBinding

    store = RuntimeStateStore(repair.STATE)
    if not store.ledger_path.exists() and not store.ledger_path.is_symlink():
        return
    repair.read_json(store.ledger_path)  # Require the canonical owned regular file.
    ledger = store.load_ledger()
    if not ledger:
        return
    prior_ha, desired_ha = installed.get("vm_ha", {}), staged["vm_ha"]
    prior = VMHARuntimeBinding.model_validate(prior_ha.get("runtime_binding", {}))
    desired = VMHARuntimeBinding.model_validate(desired_ha["runtime_binding"])

    def subnet_identity(target):
        return target.project_id, target.network_id, target.workload_subnet_id

    if (
        prior.cluster_id != desired.cluster_id
        or prior.shared_allocation_id != desired.shared_allocation_id
        or prior_ha.get("node", {}).get("node_id") != desired_ha["node"]["node_id"]
        or not {subnet_identity(t) for t in prior.route_targets}
        <= {subnet_identity(t) for t in desired.route_targets}
        or any(
            not route_id
            or owner.cluster_id != prior.cluster_id
            or owner.route_target not in prior.route_targets
            for route_id, owner in ledger.items()
        )
    ):
        raise RuntimeError("HA table cutover ledger differs from its exact predecessor binding")
    retained = {
        route_id: owner
        for route_id, owner in ledger.items()
        if owner.route_target in desired.route_targets
    }
    if retained != ledger:
        store.save_ledger(retained)
        if store.load_ledger() != retained:
            raise RuntimeError("HA table cutover ledger retirement did not persist")


def observe(request: dict, *, continuity) -> dict:
    import time

    import yaml

    from nebius_vpngw import checkpoint_upgrade, ha_repair
    from nebius_vpngw.ordinary_operations import Manager

    with ha_repair.writer_locks(deadline=time.monotonic() + 5):
        lock = ha_repair.exact_lock(request["expected"])
        boot = ha_repair.BOOT.read_text().strip()
        guard = ha_repair.read_json(ha_repair.STATE / "guard.json")
        if (
            guard.get("schema") != ha_repair.STATUS_SCHEMA
            or guard.get("guard_boot_id") != boot
            or guard.get("data_plane_mode") not in {"blocked", "passive"}
            or ha_repair.FORWARDING.read_text().strip() != "0"
        ):
            raise RuntimeError("HA table cutover lost the current-boot forwarding fence")
        staged = yaml.safe_load(
            ha_repair.verified_file(Path(request["staged_path"]), request["staged_sha256"])
        )
        path = ha_repair.STATE / "route-pending.json"
        if path.exists() or path.is_symlink():
            raise RuntimeError("HA table cutover has an unresolved predecessor effect")
        installed_path = Path("/etc/nebius-vpngw/config-resolved.yaml")
        installed = (
            yaml.safe_load(
                ha_repair.verified_file(installed_path, ha_repair.sha(installed_path.read_bytes()))
            )
            if installed_path.exists() or installed_path.is_symlink()
            else {}
        )
        continuity(
            ha_repair,
            checkpoint_upgrade,
            boot=boot,
            expected=request["expected"],
            allocation_id=request["allocation_id"],
            installed=installed.get("vm_ha", {}),
            unstarted=True,
        )
        manager = Manager(time.monotonic() + 15)
        for unit in request["units"]:
            if not manager.stopped(unit):
                raise RuntimeError("HA table cutover has a live native writer or job")
        from nebius_vpngw.agent.vm_ha.forwarding import DestinationProtection
        from nebius_vpngw.ha_tunnel_policy import connection_policies

        binding = staged["vm_ha"]["runtime_binding"]
        if (
            staged["vm_ha"]["cluster_id"] != request["expected"]["cluster_id"]
            or staged["vm_ha"]["node"]["node_id"] != request["expected"]["node_id"]
            or binding["shared_allocation_id"] != request["allocation_id"]
            or binding["route_runtime_id"] != request["route_runtime_id"]
        ):
            raise RuntimeError("HA table cutover staged route authority differs")
        protection = DestinationProtection(
            ha_repair.STATE / "destination-protection.json",
            cluster=request["expected"]["cluster_id"],
            policies=connection_policies(staged),
            local_prefixes=tuple(staged.get("gateway", {}).get("local_prefixes", ())),
        ).enforced()
        if request["install"]:
            protection.install()
        protection.verify()
        if request["install"]:
            retire_prior_ledger(installed, staged, ha_repair)
        return {"boot_id": boot, "lock": lock}


def verify(
    ssh,
    *,
    instance,
    target: str,
    local: dict,
    binding,
    receipt,
    operation: str,
    install: bool = False,
) -> dict:
    from . import stopped_upgrade
    from .vm_ha_upgrade_recovery import UNITS

    request = {
        "expected": {
            "cluster_id": binding.cluster_id,
            "node_id": instance.vm_ha_node.node_id,
            "generation_id": receipt.generation_id,
            "operation_id": operation,
        },
        "staged_path": f"/etc/nebius-vpngw/vm-ha-staged/{receipt.generation_id}.yaml",
        "staged_sha256": receipt.staged_file_sha256,
        "allocation_id": binding.shared_allocation_id,
        "route_runtime_id": binding.route_runtime_id,
        "install": install,
        "units": UNITS,
    }
    encoded = base64.b64encode(Path(__file__).read_bytes()).decode()
    continuity_source = base64.b64encode(Path(stopped_upgrade.__file__).read_bytes()).decode()
    script = (
        "import base64,json\nnamespace={}\ncontinuity={}\n"
        + f"exec(compile(base64.b64decode({encoded!r}),'<ha-table-barrier>','exec'),namespace)\n"
        + f"exec(compile(base64.b64decode({continuity_source!r}),'<stopped-upgrade>','exec'),continuity)\n"
        + f"print(json.dumps(namespace['observe']({request!r},continuity=continuity['verify_continuity'])))\n"
    )
    result = ssh._inspect_vm_ha_host(target, instance.hostname, local, script=script)
    if (
        not isinstance(result, dict)
        or set(result) != {"boot_id", "lock"}
        or not result["boot_id"]
        or any(result["lock"].get(k) != v for k, v in request["expected"].items())
    ):
        raise RuntimeError("HA table cutover stopped-writer readback differs")
    return result
