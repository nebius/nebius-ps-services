"""One-member package bootstrap behind the existing HA package safeguards."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from . import checkpoint_conversion, ordinary_apply


def identity(binding: Any, owner: Any, peer: Any) -> dict:
    return {
        "cluster_id": binding.cluster_id,
        "owner_node_id": owner.vm_ha_node.node_id,
        "peer_node_id": peer.vm_ha_node.node_id,
        "allocation_id": binding.shared_allocation_id,
        "generation_id": binding.generation_id,
        "digests": {
            "configuration": binding.configuration_digest,
            "static_routes": binding.static_routes_digest,
            "bgp_policy": binding.bgp_policy_digest,
        },
    }


def control(ssh: Any, target: str, instance: Any, local: dict, request: dict) -> dict:
    script = (
        checkpoint_conversion.source()
        + "\nrequest="
        + repr(request)
        + "\n"
        + r"""
import fcntl,json,os
from pathlib import Path
state=checkpoint_repair.STATE
expected=request['identity']
operation=request['operation']
path=state/restoration_upgrade.RECOVERY
def verify_fence():
    guard=json.loads((state/'guard.json').read_text())
    if (guard.get('guard_boot_id')!=checkpoint_repair.BOOT.read_text().strip()
        or guard.get('data_plane_mode') not in {'passive','blocked'}
        or checkpoint_repair.FORWARDING.read_text().strip()!='0'):
        raise ValueError('restoration package fence is unproven')
fd=os.open(state/'rearm.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
try:
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    action=request['action']
    value=None
    if path.exists():
        value=restoration_upgrade.recovery_transaction(state,expected,operation_id=operation)
        if value['artifact']!=request['artifact']:
            raise ValueError('restoration package artifact differs')
    if action=='progress':
        if value is None or value['phase']!='released':
            raise ValueError('restoration progress has no released package transaction')
        progress=restoration_upgrade.recovery_progress(state,expected,operation_id=operation)
        authority=restoration_upgrade.StandbyRestorationStore(state).load()
        print(json.dumps({'progress':progress,'restoration_phase':authority.phase.value,
            'deadline':authority.standby_deadline_at}))
    elif action=='accepted':
        restoration_upgrade.recovery_evidence(state,expected,operation_id=operation)
        accepted=state/'rearm-cloud-operation.json'
        print(json.dumps({'accepted':json.loads(accepted.read_text()) if accepted.exists() else None}))
    elif action=='settle':
        restoration_upgrade.settle_accepted(state,expected,accepted=request['accepted'],
            instance_id=request['instance_id'],successful=request['successful'])
    elif action=='prepare':
        value=restoration_upgrade.park_for_upgrade(state,expected,
            operation_id=operation,artifact=request['artifact'])
    elif action=='inspect':
        if value is not None:
            restoration_upgrade.recovery_evidence(state,expected,operation_id=operation)
            lock=state/'apply.lock'
            if value['phase']=='prepared' and lock.exists():
                checkpoint_repair.exact_lock(request['lock'])
            if value['phase'] in {'locked','installed','activated'}:
                if lock.exists():
                    checkpoint_repair.exact_lock(request['lock'])
                    verify_fence()
                elif value['phase']!='activated':
                    raise ValueError('restoration package lock is missing')
            if value['phase'] in {'activated','released'}:
                from nebius_vpngw import runtime_migration
                installed=runtime_migration.state()
                if (installed is None or installed['phase']!='complete'
                    or installed['manifest']['wheel_sha256']!=request['artifact']):
                    raise ValueError('restoration runtime release is unproven')
        else:
            digest=restoration_upgrade.recovery_evidence(state,expected)
    elif action=='activate':
        from nebius_vpngw import runtime_migration
        checkpoint_repair.exact_lock(request['lock'])
        runtime_migration.activate_ha(request['lock'])
    elif action=='advance':
        assert value is not None
        phases=('prepared','locked','installed','activated','released')
        if phases.index(request['phase'])<phases.index(value['phase']):
            raise ValueError('restoration package transaction regressed')
        value={**value,'phase':request['phase']}
        restoration_upgrade.atomic_write_json(path,value)
    elif action=='release':
        assert value is not None and value['phase'] in {'activated','released'}
        lock=state/'apply.lock'
        if lock.exists():
            checkpoint_repair.exact_lock(request['lock'])
            # The release intent is durable before unlink; replay may see either.
            verify_fence()
            lock.unlink()
            directory=os.open(state,os.O_DIRECTORY)
            try: os.fsync(directory)
            finally: os.close(directory)
        value={**value,'phase':'released'}
        restoration_upgrade.atomic_write_json(path,value)
    elif action=='complete':
        if value is not None:
            if value['phase']!='released' or (state/'apply.lock').exists():
                raise ValueError('restoration parent completed before release')
            archive=state/('standby-restoration-apply-'+operation+'.json')
            if archive.exists() and json.loads(archive.read_text())!=value:
                raise ValueError('restoration completion history changed')
            restoration_upgrade.atomic_write_json(archive,value)
            path.unlink()
            directory=os.open(state,os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
    else:
        raise ValueError('unknown restoration package action')
    if action not in {'accepted','progress'}:
        print(json.dumps({'phase':None if value is None else value['phase'],
            'digest':digest if value is None and action=='inspect' else None,
            'locked':(state/'apply.lock').exists()}))
finally:
    os.close(fd)
"""
    )
    return ssh._inspect_vm_ha_host(target, instance.hostname, local, script=script)


def bootstrap(
    ssh: Any,
    target: str,
    instance: Any,
    local: dict,
    *,
    binding: Any,
    peer: Any,
    artifact: Any,
    operation: str,
    prepare_package: Callable[[dict], Any],
    verify_fenced: Callable[[], None],
    observe_start: Callable[[str, str], bool],
) -> None:
    expected = checkpoint_conversion.context(instance, operation, cluster_id=binding.cluster_id)
    request = {
        "identity": identity(binding, instance, peer),
        "operation": operation,
        "artifact": artifact.sha256,
        "lock": expected,
    }

    def run(action: str, **extra: Any) -> dict:
        return control(ssh, target, instance, local, {**request, "action": action, **extra})

    accepted = run("accepted")["accepted"]
    if accepted is not None:
        peer_compute = next(
            n.compute_id for n in binding.nodes if n.node_id == peer.vm_ha_node.node_id
        )
        successful = observe_start(accepted["cloud_operation_id"], peer_compute)
        run("settle", accepted=accepted, instance_id=peer_compute, successful=successful)
    phase = run("prepare")["phase"]
    if phase == "prepared":
        ssh.install_vm_ha_apply_lock(
            target, instance, local, runtime_binding=binding, operation_id=operation
        )
        run("advance", phase="locked")
        phase = "locked"
    if phase == "locked":
        ssh.verify_vm_ha_apply_inhibition_quiescent(
            target,
            instance.hostname,
            local,
            node_id=instance.vm_ha_node.node_id,
            operation_id=operation,
        )
        ssh.stop_vm_ha_mutation_services(target, instance.hostname, local)
        prepare_package(expected)
        checkpoint_conversion.prepare_restoration_recovery(
            ssh, target, instance, local, request["identity"], operation
        )
        run("advance", phase="installed")
        phase = "installed"
    if phase == "installed":
        run("activate")
        ssh.refresh_vm_ha_control_services(target, instance, local)
        verify_fenced()
        ordinary_apply.remote(
            ssh, target, instance, local, {"action": "release-runtime", "artifact": artifact.sha256}
        )
        run("advance", phase="activated")
        phase = "activated"
    if phase == "activated":
        run("release")


def wait_ready(
    ssh: Any,
    target: str,
    instance: Any,
    local: dict,
    *,
    request: dict,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    """Do not install pair locks while its sole recovery still awaits standby."""
    deadline = clock() + 300.0
    while True:
        observed = control(ssh, target, instance, local, {**request, "action": "progress"})
        if observed["progress"] is not True:
            raise RuntimeError("standby recovery has no continuing approved authority")
        if observed["restoration_phase"] == "completed":
            return
        remaining = deadline - clock()
        if remaining <= 0:
            raise RuntimeError("standby readiness has not completed; resume the same apply")
        sleeper(min(5.0, remaining))
