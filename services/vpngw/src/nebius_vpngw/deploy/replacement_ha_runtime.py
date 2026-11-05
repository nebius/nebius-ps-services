"""Pinned guest operations for a journalled HA replacement.

All pair changes use the existing apply locks, owner adoption, VM-local mTLS,
and controller promotion. No retired guest is returned to the runtime pair.
"""

from __future__ import annotations

import base64
import hashlib
import os
import shlex
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .. import cli
from ..agent.vm_ha.auto_healing import (
    AUTO_HEALING_REQUEST_SCHEMA,
    AutoHealingPolicyRecord,
    StandbyAutoHealing,
    encode_policy_request,
)
from .replacement import Journal, digest
from .ssh_push import VMHAAgentArtifact, VMHAApplyLockReceipt, VMHAStageReceipt


class Runtime:
    def __init__(self, ports: Any, journal: Journal) -> None:
        self.ports, self.journal = ports, journal
        self.tx = journal.state["transaction"]
        self.index = self.tx["member"]
        self.key = f"ha-runtime-{self.index}"
        self.record = self.tx["results"].setdefault(self.key, {})
        self.operation = digest([self.tx["approval"], self.index, "ha-pair-apply"])
        self.artifact = VMHAAgentArtifact.from_wheel(ports.wheel, source="staged-replacement")

    def json(self, compute: str, logical: str, code: str) -> dict:
        access, target = self.ports.access(compute, logical)
        client = access.connect(target)
        try:
            return access._read_json(client, "sudo -n /usr/bin/python3 -c " + shlex.quote(code))
        finally:
            client.close()

    def command(self, compute: str, logical: str, command: str, *, timeout: int = 90) -> dict:
        access, target = self.ports.access(compute, logical)
        client = access.connect(target)
        try:
            # No remote command output is interpolated into error messages.
            return access._read_json(client, command, timeout=timeout)
        finally:
            client.close()

    def config(self, compute: str, logical: str) -> dict:
        return self.json(
            compute,
            logical,
            "import json,os,stat,yaml;from pathlib import Path;"
            "p=Path('/etc/nebius-vpngw/config-resolved.yaml');s=p.lstat();"
            "assert stat.S_ISREG(s.st_mode) and s.st_uid==0 and not s.st_mode&0o077;"
            "b=p.read_bytes();import hashlib;"
            "print(json.dumps({'sha256':hashlib.sha256(b).hexdigest(),'config':yaml.safe_load(b)}))",
        )

    def stage(
        self, compute: str, logical: str, binding: Any, credential_source: tuple[str, str, str]
    ) -> Any:
        source_compute, source_logical, source_path = credential_source
        credential = self.json(
            source_compute,
            source_logical,
            "import base64,json,os,stat;from pathlib import Path;"
            f"p=Path({source_path!r});s=p.lstat();"
            "assert stat.S_ISREG(s.st_mode) and s.st_uid==0 and not s.st_mode&0o077 and s.st_size<1048576;"
            "print(json.dumps({'value':base64.b64encode(p.read_bytes()).decode('ascii')}))",
        )
        payload = base64.b64decode(credential["value"], validate=True)
        if hashlib.sha256(payload).hexdigest() != binding.nodes[0].nebius_credentials_sha256:
            raise RuntimeError("Replacement runtime credential changed from the approved identity")
        _access, target = self.ports.access(compute, logical)
        # Only this intended protected runtime file contains credential bytes.
        with tempfile.TemporaryDirectory(prefix="vpngw-replacement-credential-") as directory:
            path = Path(directory) / "credential.json"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            return self.ports.ssh.stage_vm_ha_config(
                target.address,
                self.ports.instances[logical],
                self.ports.local,
                runtime_binding=binding,
                nebius_credentials_path=path,
            )

    def prepare(self) -> dict:
        ports, journal = self.ports, self.journal
        cloud, member, access, target = ports._candidate(journal)
        snapshot = access.inspect(target, operation=self.tx["plan"]["operation_id"])
        operation = self.tx["plan"]["operation_id"]
        proof = self.json(
            target.compute_id,
            target.hostname,
            "import json,subprocess;from pathlib import Path;"
            f"assert json.loads(Path('/etc/nebius-vpngw/replacement-guard.json').read_text())=={{'operation':{operation!r}}};"
            "assert all(subprocess.check_output(['sysctl','-n',key]).strip()==b'0' for key in "
            "['net.ipv4.ip_forward','net.ipv6.conf.all.forwarding']);"
            "units=['strongswan.service','strongswan-starter.service','frr.service','nebius-vpngw-agent.service',"
            "'nebius-vpngw-vm-ha.service','nebius-vpngw-vm-ha-rearm.service'];"
            "assert all(subprocess.run(['systemctl','is-active','--quiet',u]).returncode!=0 for u in units);"
            "print(json.dumps({'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip()}))",
        )
        if proof["boot_id"] != snapshot["context"]["boot"]:
            raise RuntimeError("HA candidate rebooted during passive preparation")
        ports.ssh.ensure_vm_ha_agent_package(
            target.address, ports.instances[target.hostname], ports.local, artifact=self.artifact
        )
        binding = ports.runtime_binding(journal, candidate=True)
        source = self.config(member["compute_id"], member["logical_name"])["config"]["vm_ha"][
            "runtime_binding"
        ]
        node = next(item for item in source["nodes"] if item["compute_id"] == member["compute_id"])
        staged = self.stage(
            target.compute_id,
            target.hostname,
            binding,
            (member["compute_id"], member["logical_name"], node["nebius_credentials_path"]),
        )
        self.record["stages"] = {**self.record.get("stages", {}), target.hostname: asdict(staged)}
        # Stage the future apply lock while the earlier boot guard remains in force.
        encoded, lock = ports.ssh._vm_ha_apply_lock_payload(
            cluster_id=binding.cluster_id,
            node_id=ports.instances[target.hostname].vm_ha_node.node_id,
            generation_id=binding.generation_id,
            operation_id=self.operation,
        )
        self.json(
            target.compute_id,
            target.hostname,
            "import json;from pathlib import Path;from nebius_vpngw.deploy.operator_guest import publish,read_file;"
            f"assert json.loads(Path('/etc/nebius-vpngw/replacement-guard.json').read_text())=={{'operation':{operation!r}}};"
            "p=Path('/var/lib/nebius-vpngw/vm-ha/apply.lock');p.parent.mkdir(mode=0o700,parents=True,exist_ok=True);"
            f"before=read_file(p);value={encoded.decode()!r};"
            "assert before is None or before['content']==value;"
            "publish(p,before,{'content':value,'uid':0,'gid':0,'mode':0o600});print(json.dumps({'verified':True}))",
        )
        self.record["locks"] = {**self.record.get("locks", {}), target.hostname: asdict(lock)}
        journal.save()
        return {
            "compute_id": target.compute_id,
            "boot_id": proof["boot_id"],
            "config_sha256": staged.staged_file_sha256,
            "artifact_sha256": self.artifact.sha256,
        }

    def fence_sources(self) -> None:
        """Fence both current members before stopping the source nonowner."""
        ports, journal = self.ports, self.journal
        members = ports.members(journal, candidate=False)
        binding = ports.runtime_binding(journal, candidate=False)
        owner = ports.owner(members)
        # Nonowner first; once both locks exist, drain every service writer.
        for member in sorted(members, key=lambda item: item.compute_id == owner.compute_id):
            logical = member.instance_name
            _, target = ports.access(member.compute_id, logical)
            if f"fenced:{logical}" not in self.record:
                lock = ports.ssh.install_vm_ha_apply_lock(
                    target.address,
                    ports.instances[logical],
                    ports.local,
                    runtime_binding=binding,
                    operation_id=self.operation,
                )
                ports.ssh.stop_vm_ha_mutation_services(target.address, logical, ports.local)
                self.record.setdefault("locks", {})[logical] = asdict(lock)
                self.record[f"fenced:{logical}"] = {"compute_id": member.compute_id}
                journal.save()
            else:
                # Reprove the service barrier even on an accepted-stop retry.
                ports.ssh.stop_vm_ha_mutation_services(target.address, logical, ports.local)

    def status(self, member: Any, binding: Any, *, locked: bool | None = None) -> dict:
        _, target = self.ports.access(member.compute_id, member.instance_name)
        value = self.command(
            member.compute_id,
            member.instance_name,
            "sudo -n /usr/bin/python3 -m nebius_vpngw.agent.main --vm-ha-status",
        )
        return cli._validate_vm_ha_agent_status(
            value,
            inst_cfg=self.ports.instances[member.instance_name],
            runtime_binding=binding,
            expected_apply_locked=locked,
        )

    def wait(
        self, member: Any, binding: Any, predicate: Any, *, locked: bool | None = None
    ) -> dict:
        deadline = time.monotonic() + 180
        while True:
            try:
                value = self.status(member, binding, locked=locked)
                if predicate(value):
                    return value
            except (OSError, RuntimeError, ValueError):
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "HA replacement runtime proof remains incomplete; resume the recovery bundle"
                )
            time.sleep(2)

    def policy_action(
        self,
        *,
        action: str,
        requests: dict | None = None,
        node_ids: frozenset | None = None,
        **_ignored: Any,
    ) -> list[dict]:
        result = []
        for member in self.ports.members(self.journal, candidate=True):
            if node_ids is not None and member.node_id not in node_ids:
                continue
            request = (requests or {}).get(member.node_id)
            command = (
                "sudo -n /usr/bin/python3 -m nebius_vpngw.agent.main --vm-ha-auto-healing-action "
                + action
            )
            if request:
                command += " --vm-ha-auto-healing-request " + shlex.quote(request)
            response = self.command(member.compute_id, member.instance_name, command)
            # Same response contract as the canonical policy client.
            record = AutoHealingPolicyRecord.from_mapping(response.get("record"))
            if (
                response.get("schema") != cli.AUTO_HEALING_STATUS_SCHEMA
                or response.get("cluster_id") != self.ports.deployment.vm_ha.cluster_id
                or response.get("node_id") != member.node_id
                or response.get("configured_role") != member.role
                or response.get("generation_id")
                != self.ports.deployment.vm_ha.generation.generation_id
                or record.node_id != member.node_id
                or record.generation_id != response["generation_id"]
                or response.get("decision_digest") != record.decision_digest
                or response.get("phase") != record.phase.value
                or response.get("desired") != record.desired.value
                or response.get("operation_id") != record.operation_id
                or not isinstance(response.get("accepted_start"), bool)
                or response.get("recovery") is not None
                or response.get("recovery_phase") is not None
            ):
                raise RuntimeError("HA replacement policy evidence is not exact and quiescent")
            result.append(response)
        return result

    def activate(self) -> dict:
        ports, journal = self.ports, self.journal
        members = ports.members(journal, candidate=True)
        binding = ports.runtime_binding(journal, candidate=True)
        owner = ports.owner(members)
        candidate = next(member for member in members if member.compute_id != owner.compute_id)
        targets = {
            member.instance_name: ports.access(member.compute_id, member.instance_name)[1].address
            for member in members
        }
        instances = [ports.instances[member.instance_name] for member in members]
        # Steps are individually checkpointed. Resume must never reinstall locks
        # or restart an owner after a later unlock has been accepted.
        if "owner-staged" not in self.record:
            source = self.config(owner.compute_id, owner.instance_name)["config"]["vm_ha"][
                "runtime_binding"
            ]
            node = next(item for item in source["nodes"] if item["compute_id"] == owner.compute_id)
            ports.ssh.ensure_vm_ha_agent_package(
                targets[owner.instance_name],
                ports.instances[owner.instance_name],
                ports.local,
                artifact=self.artifact,
            )
            staged = self.stage(
                owner.compute_id,
                owner.instance_name,
                binding,
                (owner.compute_id, owner.instance_name, node["nebius_credentials_path"]),
            )
            self.record["stages"][owner.instance_name] = asdict(staged)
            self.record["owner-staged"] = True
            journal.save()
        if "candidate-guard-released" not in self.record:
            access, target, snapshot = ports._verify_access(journal)
            ports.request(
                access,
                target,
                "activate-replacement",
                operation=self.tx["plan"]["operation_id"],
                expected_digest=snapshot["digest"],
            )
            self.record["candidate-guard-released"] = True
            journal.save()
        owner_cfg = ports.instances[owner.instance_name]
        if "owner-adoption" not in self.record:
            ports.ssh.install_vm_ha_apply_owner_adoption(
                targets[owner.instance_name],
                owner_cfg,
                ports.local,
                runtime_binding=binding,
                lock_receipt=VMHAApplyLockReceipt(**self.record["locks"][owner.instance_name]),
            )
            self.record["owner-adoption"] = True
            journal.save()
        mtls = cli._prepare_vm_ha_managed_mtls(
            ssh=ports.ssh,
            ordered_instances=instances,
            targets=targets,
            local_cfg=ports.local,
            runtime_binding=binding,
        )
        # Rebind the surviving owner's generation first, behind its apply lock.
        for member in (owner, candidate):
            key = f"activated:{member.instance_name}"
            if key not in self.record:
                request = None
                if member == candidate:
                    statuses = self.policy_action(
                        action="status", node_ids=frozenset({owner.node_id})
                    )
                    policy = AutoHealingPolicyRecord.from_mapping(statuses[0]["record"])
                    if policy.desired is not StandbyAutoHealing.ENABLED:
                        raise RuntimeError(
                            "HA replacement requires the inspected enabled restoration policy"
                        )
                    self.record.setdefault("original-policy", policy.to_dict())
                    journal.save()
                    request = encode_policy_request(
                        {
                            "schema": AUTO_HEALING_REQUEST_SCHEMA,
                            "apply_operation_id": self.operation,
                            "mtls_apply_operation_id": mtls.operation_id,
                            "mtls_inhibition_operation_id": None,
                            "operation_id": policy.operation_id,
                            "peer_record": policy.to_dict(),
                        }
                    )
                ports.ssh.push_config_and_reload(
                    targets[member.instance_name],
                    ports.instances[member.instance_name],
                    ports.local,
                    staged_receipt=VMHAStageReceipt(**self.record["stages"][member.instance_name]),
                    runtime_binding=binding,
                    agent_artifact=self.artifact,
                    replacement_policy_request=request,
                    apply_operation_id=self.operation,
                    initialize_checkpoint=member == candidate,
                )
                self.record[key] = True
                journal.save()
        if "mtls-finalized" not in self.record:
            mtls_statuses = {
                member.node_id: self.wait(
                    member,
                    binding,
                    lambda value: bool(
                        value.get("data_plane_mode") == "passive"
                        and value.get("heartbeat", {}).get("peer_v5_fresh") is True
                        and cli._vm_ha_mtls_agent_evidence_matches(
                            mtls, str(value.get("node_id")), value
                        )
                    ),
                    locked=True,
                )
                for member in members
            }
            cli._finalize_vm_ha_managed_mtls(
                ssh=ports.ssh, transaction=mtls, local_cfg=ports.local, agent_statuses=mtls_statuses
            )
            self.record["mtls-finalized"] = True
            journal.save()
        for member in (owner, candidate):
            key = f"unlocked:{member.instance_name}"
            if key not in self.record:
                ports.require_owner(journal, owner.compute_id)
                # An unlock may have succeeded before the journal write. Reconcile
                # only a fresh, exact generation/configuration/identity status;
                # the serving proof below must still pass before progressing.
                if self.status(member, binding).get("apply_locked") is not False:
                    ports.ssh.clear_vm_ha_apply_lock(
                        targets[member.instance_name],
                        ports.instances[member.instance_name],
                        ports.local,
                        receipt=VMHAApplyLockReceipt(**self.record["locks"][member.instance_name]),
                    )
                self.record[key] = True
                journal.save()
            self.wait(
                member,
                binding,
                lambda value, active=member == owner: bool(
                    value.get("data_plane_mode") == ("active" if active else "passive")
                    and value.get("observed_owner_node_id") == owner.node_id
                    and value.get("pending_operation_id") is None
                    and (
                        not active
                        or cli._vm_ha_active_route_receipt_matches(
                            value, active_node_id=owner.node_id, runtime_binding=binding
                        )
                    )
                ),
                locked=False,
            )
        if "policy-verified" not in self.record:
            statuses = self.policy_action(action="status")
            if not cli._vm_ha_auto_healing_is_terminal(statuses, StandbyAutoHealing.ENABLED):
                by_node = {
                    item["node_id"]: AutoHealingPolicyRecord.from_mapping(item["record"])
                    for item in statuses
                }
                transaction = cli._vm_ha_replacement_policy_reproof_transaction(
                    owner=by_node[owner.node_id],
                    replacement=by_node[candidate.node_id],
                    original=AutoHealingPolicyRecord.from_mapping(self.record["original-policy"]),
                )
                if transaction is None:
                    raise RuntimeError(
                        "HA replacement restoration policy requires exact two-member agreement"
                    )
                cli._execute_vm_ha_auto_healing_policy(
                    config_path=ports.config_path,
                    desired=StandbyAutoHealing.ENABLED,
                    transaction=transaction,
                    initial_statuses=statuses,
                    action_runner=self.policy_action,
                    authority_guard=lambda: ports.require_owner(journal, owner.compute_id),
                )
            self.record["policy-verified"] = True
            journal.save()
        return {"compute_id": candidate.compute_id, "generation_id": binding.generation_id}
