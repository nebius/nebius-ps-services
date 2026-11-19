"""Deployment-owned admission of routing semantics, separate from HA authority.

Only normal deployment publishes admission. Runtime readers must still prove
their ordinary writer reservation or HA ownership, current underlay, and the
drop-only destination guard before effects. Admission is not apply completion.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import yaml

from .agent import underlay
from .agent.vm_ha.forwarding import DestinationProtection
from .agent.vm_ha.store import atomic_write_json
from .ha_tunnel_policy import connection_policies

PATH = Path("/etc/nebius-vpngw/routing-semantics.json")
REVISION = "persistent-underlay-private-supernets-v1"
SCHEMA = "nebius-vpngw.routing-admission.v1"


def _configuration() -> tuple[bytes, dict[str, Any]]:
    raw = underlay._read(underlay.CONFIG)
    cfg = yaml.safe_load(raw)
    if not isinstance(cfg, dict):
        raise RuntimeError("routing admission configuration is malformed")
    return raw, cfg


def _package() -> str:
    from .agent.ordinary import package_identity

    return package_identity()


def _protection(cfg: dict[str, Any]) -> DestinationProtection:
    ha = cfg.get("vm_ha")
    return DestinationProtection(
        underlay.STATE.parent
        / ("vm-ha" if ha is not None else "ordinary")
        / "destination-protection.json",
        cluster=ha["cluster_id"] if ha is not None else "ordinary",
        policies=connection_policies(cfg),
        local_prefixes=tuple((cfg.get("gateway") or {}).get("local_prefixes", ())),
        run=underlay.run,
    )


def _save(record: dict[str, Any]) -> None:
    underlay._directory(PATH.parent)
    if PATH.exists() or PATH.is_symlink():
        underlay._read(PATH)
    atomic_write_json(PATH, record)


def _read() -> dict[str, Any]:
    try:
        record = json.loads(underlay._read(PATH))
    except FileNotFoundError:
        raise RuntimeError("routing semantics require normal apply admission") from None
    if (
        not isinstance(record, dict)
        or set(record)
        != {
            "schema",
            "revision",
            "mode",
            "operation",
            "artifact",
            "config_sha256",
            "package_identity",
            "runtime_binding",
            "preparation_proof",
        }
        or record["schema"] != SCHEMA
        or record["revision"] != REVISION
    ):
        raise RuntimeError("routing semantics admission is missing or incompatible")
    return record


def require(*, runtime_binding: dict[str, Any] | None = None) -> str:
    """Reprove installed/configured behavior; never refresh admission automatically."""
    record = _read()
    raw, cfg = _configuration()
    if (
        record["config_sha256"] != hashlib.sha256(raw).hexdigest()
        or record["package_identity"] != _package()
        or record["mode"] != ("ha" if cfg.get("vm_ha") is not None else "ordinary")
    ):
        raise RuntimeError("routing semantics admission changed; resume normal apply")
    if runtime_binding is not None and record["runtime_binding"] != underlay.digest(
        runtime_binding
    ):
        raise RuntimeError("runtime route binding lacks exact deployment admission")
    underlay.verify(cfg)
    _protection(cfg).verify()
    return underlay.digest(record)


def _publish(*, mode: str, operation: str, artifact: str, runtime_binding: dict | None) -> None:
    raw, cfg = _configuration()
    if not operation or len(operation) > 128 or not artifact or len(artifact) != 64:
        raise RuntimeError("routing admission requires exact deployment identity")
    proof = underlay.verify(cfg)
    _protection(cfg).verify()
    record = {
        "schema": SCHEMA,
        "revision": REVISION,
        "mode": mode,
        "operation": operation,
        "artifact": artifact,
        "config_sha256": hashlib.sha256(raw).hexdigest(),
        "package_identity": _package(),
        "runtime_binding": underlay.digest(runtime_binding)
        if runtime_binding is not None
        else None,
        "preparation_proof": proof,
    }
    if PATH.exists() or PATH.is_symlink():
        previous = _read()
        if all(
            previous[key] == record[key]
            for key in ("revision", "mode", "config_sha256", "package_identity")
        ):
            # An unchanged apply renews the independent readiness proof, not
            # the semantic identity of an already admitted runtime/journal.
            if runtime_binding is None or previous["runtime_binding"] == record["runtime_binding"]:
                return
    _save(record)


def admit_ordinary_locked(*, config_sha256: str, package_identity: str, operation: str) -> None:
    """Called only by final deployment confirmation under its routing lock."""
    raw, cfg = _configuration()
    if (
        cfg.get("vm_ha") is not None
        or hashlib.sha256(raw).hexdigest() != config_sha256
        or _package() != package_identity
    ):
        raise RuntimeError("ordinary routing admission does not match the deployment")
    _publish(mode="ordinary", operation=operation, artifact=package_identity, runtime_binding=None)


def bind_ordinary_locked(desired: dict[str, Any]) -> None:
    """Deployment handoff binds the finalized generated cloud policy before release."""
    from .ordinary_route_binding import validate

    desired = validate(desired)
    require()
    record = _read()
    if record["mode"] != "ordinary" or desired["generation"] != record["config_sha256"]:
        raise RuntimeError("ordinary route publication differs from admitted configuration")
    if record["runtime_binding"] != underlay.digest(desired):
        _save({**record, "runtime_binding": underlay.digest(desired)})


def pending_admission(
    store: Any, mutation: Any, context: Any, admission: str, *, publish: bool = False
) -> None:
    """Bind an exact route intent before journal publication; never adopt a replay."""
    path = store.pending_path.with_name("route-pending-admission.json")
    expected = {
        "schema": "nebius-vpngw.route-pending-admission.v1",
        "admission": admission,
        "mutation": store._mutation_payload(mutation),
        "context": context.to_dict(),
    }
    if publish:
        if store.load_pending_mutation() is not None:
            raise RuntimeError("cannot admit a previously pending route mutation")
        underlay._directory(path.parent)
        atomic_write_json(path, expected)
    try:
        actual = json.loads(underlay._read(path))
    except FileNotFoundError:
        raise RuntimeError(
            "pending route mutation lacks semantic admission; resume normal apply"
        ) from None
    if actual != expected:
        raise RuntimeError("pending route mutation belongs to a different routing admission")


def prepare_ha_locked(expected: dict[str, Any], artifact: str) -> None:
    """Fenced deployment pre-start preparation, never a controller/agent writer."""
    from . import ha_repair
    from .schema import VMHARuntimeBinding

    ha_repair.exact_lock(expected)
    _, cfg = _configuration()
    ha = cfg.get("vm_ha") or {}
    binding = VMHARuntimeBinding.model_validate(ha.get("runtime_binding"))
    if (
        binding.cluster_id != expected["cluster_id"]
        or binding.generation_id != expected["generation_id"]
        or (ha.get("node") or {}).get("node_id") != expected["node_id"]
        or underlay._command(["sysctl", "-n", "net.ipv4.ip_forward"]).strip() != "0"
    ):
        raise RuntimeError("HA routing preparation lost exact fenced deployment authority")
    # Pending predecessor effects must be settled by their deployment owner,
    # never relabeled as admitted by a newly installed semantic revision.
    if (ha_repair.STATE / "route-pending.json").exists():
        raise RuntimeError("predecessor route mutation must settle before routing admission")
    underlay.prepare_network_locked(cfg)
    _protection(cfg).install()
    _publish(
        mode="ha",
        operation=expected["operation_id"],
        artifact=artifact,
        runtime_binding=binding.model_dump(mode="json"),
    )


def prepare_ha(expected: dict[str, Any], artifact: str) -> None:
    from . import ha_repair

    with ha_repair.writer_locks(deadline=time.monotonic() + 30):
        prepare_ha_locked(expected, artifact)
