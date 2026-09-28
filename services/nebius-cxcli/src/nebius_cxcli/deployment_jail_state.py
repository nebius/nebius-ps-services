"""Verified jail output state, separate from immutable authored deployment intent."""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from .deployment_applications import target_bundle_digest
from .deployment_plan import soperator_target
from .deployment_state import DeploymentGeneration, digest
from .soperator_jail_observation import observe_protected_directories
from .soperator_jail_protection import (
    apply_frozen_jail_protection,
    assert_protection_extension,
    freeze_jail_protection,
    retained_rootfs_generations,
    slot_generation,
)
from .soperator_receipt_io import read_owner_only_json, write_owner_only_json

JAIL_STATE_SCHEMA = "nebius-cxcli.soperator-jail-state.v1"
APPLICATION_JOURNAL_SCHEMA = "nebius-cxcli.soperator-application-generations.v1"


def jail_values(config: Mapping[str, Any], target_ref: str) -> Mapping[str, Any]:
    selected = soperator_target(config)
    if selected is None or selected[0] != target_ref:
        raise RuntimeError("Jail state belongs to another Soperator target")
    values = selected[1].get("values", {})
    if not isinstance(values, Mapping):
        raise RuntimeError("Soperator jail values are invalid")
    return values


def with_jail_protection(
    config: Mapping[str, Any], target_ref: str, protection: str
) -> dict[str, Any]:
    result = copy.deepcopy(dict(config))
    selected = soperator_target(result)
    if selected is None or selected[0] != target_ref:
        raise RuntimeError("Jail state belongs to another Soperator target")
    selected[1]["values"] = apply_frozen_jail_protection(  # type: ignore[index]
        selected[1].get("values", {}), protection
    )
    return result


def merge_accepted_jail_state(
    authored: Mapping[str, Any],
    effective: Mapping[str, Any],
    desired: Mapping[str, Any],
) -> dict[str, Any]:
    """Advance unchanged product fields without erasing additions or accepting redirects."""
    base = apply_frozen_jail_protection(authored, freeze_jail_protection(authored))
    live = apply_frozen_jail_protection(effective, freeze_jail_protection(effective))
    incoming = apply_frozen_jail_protection(desired, freeze_jail_protection(desired))

    def physical(values: Mapping[str, Any]) -> dict[str, Any]:
        rootfs = copy.deepcopy(values["jailRootfs"])
        rootfs.pop("retainedGenerations", None)
        return {
            "rootfs": rootfs,
            "localPath": values.get("volume", {}).get("jail", {}).get("localPath"),
        }

    if physical(incoming) not in (physical(base), physical(live)):
        raise ValueError("Authored jail physical state conflicts with the accepted generation")
    # Existing bindings (including implicit volume names) cannot be removed or
    # redirected. New rows remain authored intent and receive live admission.
    assert_protection_extension(base, incoming)
    merged = apply_frozen_jail_protection(desired, freeze_jail_protection(live))
    base_mounts = {row["mountPath"]: row for row in base.get("jailPersistentMounts", [])}
    live_mounts = {row["mountPath"]: row for row in live.get("jailPersistentMounts", [])}
    incoming_mounts = {row["mountPath"]: row for row in incoming.get("jailPersistentMounts", [])}
    mounts = dict(live_mounts)
    retained = {digest(row): row for row in retained_rootfs_generations(live)}
    old_active = slot_generation(base, base["jailRootfs"]["activeSlot"])
    active = slot_generation(live, live["jailRootfs"]["activeSlot"])
    for path, row in incoming_mounts.items():
        if path not in live_mounts:
            # A folder added to an unchanged, stale authored root must bind to
            # the accepted active root. Existing mounts never change backing.
            if row["localPath"] == old_active["localPath"] + path:
                row = {**row, "localPath": active["localPath"] + path}
                retained[digest(active)] = active
            elif row["localPath"] != active["localPath"] + path:
                raise ValueError("New protected folder must bind to the accepted active root")
            else:
                retained[digest(active)] = active
            mounts[path] = row
        elif row != base_mounts.get(path):
            mounts[path] = row
    merged["jailPersistentMounts"] = list(mounts.values())
    # Only preserve authored retained rows already owned by accepted state;
    # additions created to pin a new folder are replaced by its actual backing.
    allowed = [
        *retained_rootfs_generations(base),
        *retained_rootfs_generations(live),
        old_active,
        active,
    ]
    if any(row not in allowed for row in retained_rootfs_generations(incoming)):
        raise ValueError("Authored retained rootfs state conflicts with accepted storage")
    merged["jailRootfs"]["retainedGenerations"] = list(retained.values())
    # Canonical validation rejects a new mount on an unretained generation.
    merged = apply_frozen_jail_protection(merged, freeze_jail_protection(merged))
    assert_protection_extension(live, merged)
    return merged


def merge_config_jail_state(
    authored: Mapping[str, Any],
    effective: Mapping[str, Any],
    desired: Mapping[str, Any],
    target_ref: str,
) -> dict[str, Any]:
    values = merge_accepted_jail_state(
        jail_values(authored, target_ref),
        jail_values(effective, target_ref),
        jail_values(desired, target_ref),
    )
    return with_jail_protection(desired, target_ref, freeze_jail_protection(values))


def _target_root(generation: DeploymentGeneration, target_ref: str) -> str:
    rows = [
        row
        for row in generation.manifest.get("deploy", {}).get("targets", [])
        if row.get("target_ref") == target_ref
    ]
    if len(rows) != 1:
        raise RuntimeError("Effective generation has no unique target bundle")
    return str(rows[0].get("flux_dir") or "flux").rstrip("/") + "/"


def overlay_accepted_target(
    base: DeploymentGeneration, effective: DeploymentGeneration, target_ref: str
) -> DeploymentGeneration:
    """Restore only Soperator-owned bytes; other target baselines keep their owner."""
    root = _target_root(base, target_ref)
    if root != _target_root(effective, target_ref):
        raise RuntimeError("Effective Soperator target bundle location changed")

    other_roots = [
        str(row.get("flux_dir") or "flux").rstrip("/") + "/"
        for row in base.manifest.get("deploy", {}).get("targets", [])
        if row.get("target_ref") != target_ref
    ]
    if root in other_roots:
        raise RuntimeError("Effective Soperator bundle shares another target's owned root")

    def owned(name: str) -> bool:
        return (
            name.startswith(root)
            and not name.startswith(root + "ordinary/")
            and not any(name.startswith(other) for other in other_roots)
        )

    files = {name: value for name, value in base.files.items() if not owned(name)}
    files.update({name: value for name, value in effective.files.items() if owned(name)})
    config = with_jail_protection(
        base.manifest["runtime_config"],
        target_ref,
        freeze_jail_protection(jail_values(effective.manifest["runtime_config"], target_ref)),
    )
    return replace(base, manifest={**base.manifest, "runtime_config": config}, files=files)


def build_jail_state_receipt(
    *,
    authored: DeploymentGeneration,
    effective: DeploymentGeneration,
    target_ref: str,
    identity: Mapping[str, str],
    storage_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    body = {
        "schema": JAIL_STATE_SCHEMA,
        "targetRef": target_ref,
        "identity": dict(identity),
        "generation": authored.identity,
        "effectiveGeneration": effective.identity,
        "jailProtection": freeze_jail_protection(
            jail_values(effective.manifest["runtime_config"], target_ref)
        ),
        "desiredBundle": target_bundle_digest(effective, target_ref),
        "storageEvidence": dict(storage_evidence),
    }
    result = {**body, "receiptSha256": digest(body)}
    validate_jail_state_receipt(result)
    return result


def validate_jail_state_receipt(value: Mapping[str, Any]) -> None:
    keys = {
        "schema",
        "targetRef",
        "identity",
        "generation",
        "effectiveGeneration",
        "jailProtection",
        "desiredBundle",
        "storageEvidence",
        "receiptSha256",
    }
    if (
        set(value) != keys
        or value.get("schema") != JAIL_STATE_SCHEMA
        or not value.get("targetRef")
        or not isinstance(value.get("identity"), Mapping)
        or set(value["identity"]) != {"cluster_id", "kubernetes_uid"}
        or not all(value["identity"].values())
        or not isinstance(value.get("storageEvidence"), Mapping)
        or not value["storageEvidence"]
        or any(
            not re.fullmatch(r"sha256:[a-f0-9]{64}", str(value.get(key, "")))
            for key in ("generation", "effectiveGeneration", "desiredBundle", "receiptSha256")
        )
        or value["receiptSha256"]
        != digest({k: v for k, v in value.items() if k != "receiptSha256"})
    ):
        raise RuntimeError("Accepted jail-state receipt is incomplete or changed")
    apply_frozen_jail_protection({}, value["jailProtection"])


def accepted_effective_generation(
    state: Any, target_ref: str, evidence: Mapping[str, Any]
) -> DeploymentGeneration | None:
    receipt = evidence.get("jailState")
    if receipt is None:
        if evidence.get("effectiveGeneration"):
            raise RuntimeError("Accepted effective generation has no jail-state receipt")
        return None  # First storage admission still requires fresh physical proof.
    if not isinstance(receipt, Mapping):
        raise RuntimeError("Accepted jail-state receipt is invalid")
    validate_jail_state_receipt(receipt)
    if (
        receipt["targetRef"] != target_ref
        or receipt["identity"] != evidence.get("identity")
        or receipt["generation"] != evidence.get("generation")
        or receipt["effectiveGeneration"] != evidence.get("effectiveGeneration")
        or receipt["desiredBundle"] != evidence.get("desiredBundle")
    ):
        raise RuntimeError("Accepted jail state differs from its target evidence")
    effective = state.generation(receipt["effectiveGeneration"])
    if (
        freeze_jail_protection(jail_values(effective.manifest["runtime_config"], target_ref))
        != receipt["jailProtection"]
        or target_bundle_digest(effective, target_ref) != receipt["desiredBundle"]
    ):
        raise RuntimeError("Accepted effective generation differs from its verified jail state")
    return effective


def hydrate_accepted_generations(
    state: Any,
    accepted: Mapping[str, Any] | None,
    desired: DeploymentGeneration,
    source: DeploymentGeneration | None,
) -> tuple[DeploymentGeneration | None, dict[str, DeploymentGeneration], DeploymentGeneration]:
    targets: dict[str, DeploymentGeneration] = {}
    config = desired.manifest["runtime_config"]
    for ref, evidence in (accepted or {}).get("evidence", {}).get("targets", {}).items():
        authored = state.generation(evidence["generation"])
        effective = accepted_effective_generation(state, ref, evidence)
        targets[ref] = effective or authored
        if effective is not None:
            config = merge_config_jail_state(
                authored.manifest["runtime_config"],
                effective.manifest["runtime_config"],
                config,
                ref,
            )
            if source is not None:
                source = overlay_accepted_target(source, effective, ref)
    return (
        source,
        targets,
        replace(desired, manifest={**desired.manifest, "runtime_config": config}),
    )


def observe_jail_storage(
    cli: Any,
    *,
    flux_dir: Path,
    values: Mapping[str, Any],
    kube_context: str,
    kube_env: Mapping[str, str],
) -> dict[str, Any]:
    """Fresh physical admission for accepted state, including first adoption."""
    import json

    from .soperator_jail_mounts import jail_rootfs_active_source
    from .soperator_populate_jail import active_passive_jail_rootfs_slots

    state = cli._rendered_soperator_adapter_state(flux_dir)
    namespaces = cli._soperator_upgrade_live_slurmcluster_namespaces(extra_env=kube_env)
    if len(namespaces) != 1:
        raise RuntimeError("Jail storage acceptance requires one exact namespace")
    namespace = namespaces[0]

    def read(kind: str, name: str) -> Mapping[str, Any]:
        result = cli._run_soperator_upgrade_kubectl(
            namespace,
            ["get", kind, name, "-o", "json"],
            kube_context=kube_context,
            extra_env=kube_env,
            check=True,
        )
        payload = json.loads(result.stdout)
        if not isinstance(payload, Mapping):
            raise RuntimeError("Jail storage acceptance returned an invalid object")
        return payload

    rows = [
        *state["slots"].values(),
        *state.get("retainedGenerations", []),
        *state.get("persistentMounts", []),
    ]
    volumes = {}
    for row in rows:
        pvc, pv = read("pvc", row["pvc_name"]), read("pv", row["pv_name"])
        claim = pv.get("spec", {}).get("claimRef", {})
        nfs = values.get("externalNfs", {})
        backing = (
            {"nfs": {"server": nfs.get("server"), "path": nfs.get("path")}}
            if row.get("name") == "external-nfs-home" and nfs.get("enabled") is True
            else {"local": {"path": row["local_path"]}}
        )
        transport, expected_backing = next(iter(backing.items()))
        actual_backing = pv.get("spec", {}).get(transport, {})
        if (
            pvc.get("metadata", {}).get("name") != row["pvc_name"]
            or not pvc["metadata"].get("uid")
            or pvc.get("spec", {}).get("volumeName") != row["pv_name"]
            or pv.get("metadata", {}).get("name") != row["pv_name"]
            or not pv["metadata"].get("uid")
            or any(actual_backing.get(key) != value for key, value in expected_backing.items())
            or pv["spec"].get("persistentVolumeReclaimPolicy") != "Retain"
            or claim.get("name") != row["pvc_name"]
            or claim.get("namespace") != namespace
            or claim.get("uid") != pvc["metadata"]["uid"]
        ):
            raise RuntimeError(
                "Jail storage physical backing differs from the effective generation"
            )
        volumes[row["pvc_name"]] = {
            "pvName": row["pv_name"],
            "pvcUid": pvc["metadata"]["uid"],
            "pvUid": pv["metadata"]["uid"],
            "backing": backing,
        }
    active = (
        active_passive_jail_rootfs_slots(values).active_pvc
        if jail_rootfs_active_source(values) == "slot"
        else values["jailRootfs"]["adoption"]["legacyPvcName"]
    )
    if cli._live_soperator_jail_pvc_for_reconcile(env=kube_env) != active:
        raise RuntimeError("Live jail root differs from the accepted physical generation")
    return {
        "activePvcName": active,
        "volumes": volumes,
        "directoryIdentities": observe_protected_directories(
            cli,
            values,
            kube_context=kube_context,
            extra_env=kube_env,
        ),
    }


def verify_storage_handoff(
    *,
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    release: Mapping[str, Any],
) -> None:
    """Retained identities cannot be replaced by a later observation of the same name."""
    materialization = release.get("materialization")
    replaced_pvc = None
    if materialization:
        replaced_pvc = materialization["targetPvcName"]
        active = current.get("volumes", {}).get(replaced_pvc, {})
        if (
            current.get("activePvcName") != replaced_pvc
            or active.get("pvcUid") != materialization["targetPvcUid"]
            or active.get("pvUid") != materialization["targetPvUid"]
        ):
            raise RuntimeError("Final active jail PV/PVC differs from the completed release child")
    for name, volume in previous.get("volumes", {}).items():
        # Only the exact proven-disposable child target may have been recycled.
        # All retained roots and path-specific mounts preserve physical UIDs.
        if name != replaced_pvc and current.get("volumes", {}).get(name) != volume:
            raise RuntimeError("Accepted protected PV/PVC identity changed before acceptance")
    observed = {row["mountPath"]: row for row in current.get("directoryIdentities", [])}
    expected = [*previous.get("directoryIdentities", []), *release.get("directoryIdentities", [])]
    if any(observed.get(row["mountPath"]) != row for row in expected):
        raise RuntimeError("Protected directory identity changed before acceptance")


def validate_application_generations(value: Any) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "intent", "entries"}
        or value["schema"] != APPLICATION_JOURNAL_SCHEMA
        or not re.fullmatch(r"sha256:[a-f0-9]{64}", str(value["intent"]))
        or not isinstance(value["entries"], dict)
        or not set(value["entries"]) <= {"retire", "grow", "reconcile"}
    ):
        raise RuntimeError("Campaign application generations are invalid")
    for entry in value["entries"].values():
        if (
            not isinstance(entry, dict)
            or set(entry) != {"binding", "generation", "id"}
            or not isinstance(entry["binding"], dict)
            or set(entry["binding"]) != {"generation", "authority"}
            or not isinstance(entry["binding"]["authority"], dict)
            or entry["binding"]["authority"].get("intent") != value["intent"]
        ):
            raise RuntimeError("Campaign application generation binding is invalid")
        apply_frozen_jail_protection({}, entry["binding"]["authority"]["jailProtection"])
        DeploymentGeneration.from_payload(entry["generation"], expected_id=entry["id"])


class ApplicationGenerationJournal:
    """Write-ahead exact bytes for one campaign's application publications."""

    def __init__(self, path: Path, intent: str) -> None:
        self.path, self.intent = path, intent

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema": APPLICATION_JOURNAL_SCHEMA, "intent": self.intent, "entries": {}}
        value = read_owner_only_json(self.path, label="Campaign application generations")
        validate_application_generations(value)
        assert isinstance(value, dict)
        if value["intent"] != self.intent:
            raise RuntimeError("Campaign application generations belong to another intent")
        return value

    def seal(
        self,
        stage: str,
        *,
        binding: Mapping[str, Any],
        build: Callable[[], DeploymentGeneration],
        assert_authority: Callable[[], object],
        required: bool = False,
    ) -> DeploymentGeneration:
        from .deployment_recovery import checkpoint_execution

        assert_authority()
        payload = self._load()
        entry = payload["entries"].get(stage)
        if entry is not None:
            if not isinstance(entry, Mapping) or entry.get("binding") != dict(binding):
                raise RuntimeError("Sealed application generation binding changed")
            generation = DeploymentGeneration.from_payload(
                entry["generation"], expected_id=entry["id"]
            )
            checkpoint_execution()
            assert_authority()
            return generation
        if required:
            raise RuntimeError("The sealed campaign application generation is missing")
        generation = build()
        assert_authority()
        payload["entries"][stage] = {
            "binding": dict(binding),
            "generation": generation.as_payload(),
            "id": generation.identity,
        }
        validate_application_generations(payload)
        write_owner_only_json(self.path, payload)
        # The remote checkpoint must acknowledge these bytes before any publication.
        checkpoint_execution()
        assert_authority()
        return generation
