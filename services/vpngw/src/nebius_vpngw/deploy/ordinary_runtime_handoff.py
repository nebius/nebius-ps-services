"""SSH transport for the ordinary route owner; no cloud mutation or secret logging."""

from __future__ import annotations

import hashlib
import json
import logging
import shlex
import uuid
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import ordinary_route_binding as binding
from ..apply_approval import digest
from ..vm_ha_credentials import credential_ids_from_payload, read_vm_ha_credential_source
from .ordinary_handoff import _connection
from .ordinary_route_ownership import RouteJournal


def same_completed_routes(before: dict, after: dict) -> bool:
    """Compare authority and settled ownership, excluding harmless journal churn.

    A periodic no-op can advance transaction/revision metadata while native and
    cloud reads run. Pending effects or changed route receipts remain unknown.
    """
    if before["authority"] != after["authority"]:
        return False
    owner = before["authority"]
    if owner is None:
        return before["journal"] == after["journal"]
    if owner["writer"] not in {"runtime", "deployment"} or owner["binding"] is None:
        return False
    inventories = []
    for snapshot in (before, after):
        value = owner["binding"]
        journal = ObservedJournal(value["project"], value["gateway"], snapshot["journal"])
        transaction = journal.transaction()
        if transaction and transaction["phase"] != "complete":
            return False
        inventories.append(journal.inventory())
    return inventories[0] == inventories[1]


# A read-only absent-state probe works before the first candidate installation.
# An existing binding or authority always requires the installed protocol owner.
_ENTRY = """import json, pathlib, sys
request = json.loads(sys.stdin.readline(1048577))
paths = [pathlib.Path('/etc/nebius-vpngw/ordinary-route-binding.json'), pathlib.Path('/var/lib/nebius-vpngw/ordinary/cloud-routes/authority.json')]
if request == {'action': 'inspect'} and all(not path.exists() and not path.is_symlink() for path in paths):
    result = {'authority': None, 'journal': None}
else:
    from nebius_vpngw.agent.ordinary_route_authority import action
    result = action(request)
print(json.dumps(result, sort_keys=True))
"""

_RECOVER = """import hashlib, json, os, pathlib, stat, sys
from nebius_vpngw import ordinary_route_binding as binding
from nebius_vpngw.agent.ordinary_route_authority import RouteAuthority
from nebius_vpngw.vm_ha_credentials import preflight_vm_ha_credentials, read_vm_ha_credential_source
from nebius_vpngw.vm_ha_managed_credentials import vm_ha_service_account_name
expected = binding.validate(json.loads(sys.stdin.readline(1048577)))
assert binding.read() == expected
config = pathlib.Path('/etc/nebius-vpngw/config-resolved.yaml').read_bytes()
authority = RouteAuthority().read()
assert hashlib.sha256(config).hexdigest() == expected['generation'] or (
    authority is not None and authority['writer'] == 'deployment' and authority['binding'] == expected)
source = pathlib.Path(expected['credentials']['path'])
for parent in source.parents:
    metadata = parent.lstat()
    assert stat.S_ISDIR(metadata.st_mode) and metadata.st_uid == 0 and not metadata.st_mode & 0o022
proof = preflight_vm_ha_credentials((expected['node'],), source_path=source,
    project_id=expected['project'], purpose='ordinary-route-writer',
    requested_service_account_name=vm_ha_service_account_name(expected['gateway'], purpose='ordinary-route-writer'))
identity = proof.nodes[0]
ref = expected['credentials']
assert (identity.credential_sha256, identity.service_account_id, identity.authorized_key_id) == (ref['sha256'], ref['service_account'], ref['authorized_key'])
payload = read_vm_ha_credential_source(source)
assert hashlib.sha256(payload).hexdigest() == ref['sha256']
assert binding.read() == expected
assert pathlib.Path('/etc/nebius-vpngw/config-resolved.yaml').read_bytes() == config
assert RouteAuthority().read() == authority
sys.stdout.buffer.write(payload)
"""


@dataclass
class Handoff:
    ssh: Any
    target: str
    instance: Any
    local: dict

    def call(self, request: dict) -> dict:
        payload = json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n"
        if len(payload.encode()) > 1024 * 1024:
            raise RuntimeError("Ordinary runtime handoff exceeds its bounded payload")
        with _connection(self.ssh, self.target, self.instance, self.local) as client:
            stdin, stdout, _stderr = client.exec_command(
                "sudo /usr/bin/python3 -B -c " + shlex.quote(_ENTRY), timeout=15
            )
            stdin.write(payload)
            stdin.flush()
            stdin.channel.shutdown_write()
            response = stdout.read(1024 * 1024 + 1)
            if len(response) > 1024 * 1024 or stdout.channel.recv_exit_status() != 0:
                raise RuntimeError("Ordinary runtime handoff is unavailable")
            value = json.loads(response)
        if not isinstance(value, dict) or set(value) != {"authority", "journal"}:
            raise RuntimeError("Ordinary runtime handoff returned incomplete evidence")
        return value

    def inspect(self) -> dict:
        return self.call({"action": "inspect"})

    def read_credential(self, value: dict) -> bytes:
        """Read authenticated secret bytes only through the pinned transport."""
        from ..vm_ha_credentials import VM_HA_CREDENTIAL_MAX_BYTES

        value = binding.validate(value)
        if value["node"] != self.instance.hostname:
            raise RuntimeError("Credential recovery target changed")
        request = json.dumps(value, sort_keys=True) + "\n"
        if len(request.encode()) > 1024 * 1024:
            raise RuntimeError("Credential recovery binding exceeds its bound")
        with _connection(self.ssh, self.target, self.instance, self.local) as client:
            stdin, stdout, _stderr = client.exec_command(
                "sudo -n /usr/bin/python3 -B -c " + shlex.quote(_RECOVER), timeout=45
            )
            stdin.write(request)
            stdin.flush()
            stdin.channel.shutdown_write()
            payload = stdout.read(VM_HA_CREDENTIAL_MAX_BYTES + 1)
            if len(payload) > VM_HA_CREDENTIAL_MAX_BYTES or stdout.channel.recv_exit_status() != 0:
                raise RuntimeError("Pinned runtime credential recovery is unavailable")
        return payload

    def install(self, holder: str, value: dict, identity: Any) -> None:
        """Publish protected files while the durable guest reservation is held."""
        value = binding.validate(value)
        observed = self.inspect()["authority"]
        if observed is None or observed["writer"] != "workstation" or observed["holder"] != holder:
            raise RuntimeError("Ordinary binding publication has no exact writer reservation")
        payload = read_vm_ha_credential_source(identity.source_path)
        ref = value["credentials"]
        if hashlib.sha256(payload).hexdigest() != ref["sha256"] or credential_ids_from_payload(
            payload
        ) != (ref["service_account"], ref["authorized_key"]):
            raise RuntimeError("Ordinary route credential changed before publication")
        documents = (
            (ref["path"], payload),
            (str(binding.PATH), (json.dumps(value, sort_keys=True) + "\n").encode()),
        )
        # The existing SSH owner performs protected upload, install and exact
        # readback. Secret bytes travel only over pinned SSH, never argv/logs.
        temporary = "/tmp/nebius-vpngw-route-upload-" + uuid.uuid4().hex
        with _connection(self.ssh, self.target, self.instance, self.local) as client:

            def checked(command):
                _, stdout, _ = client.exec_command(command, timeout=15)
                if stdout.channel.recv_exit_status() != 0:
                    raise RuntimeError("Ordinary runtime binding publication failed")

            checked(f"install -d -m 0700 {temporary}")
            failure = None
            try:
                for index, (destination, content) in enumerate(documents):
                    uploaded = temporary + "/" + str(index)
                    with client.open_sftp() as sftp:
                        with sftp.file(uploaded, "wb") as stream:
                            stream.write(content)
                        sftp.chmod(uploaded, 0o600)
                    for command in self.ssh._credential_install_commands(
                        base=str(Path(destination).parent),
                        temporary=uploaded,
                        target=destination,
                        digest=hashlib.sha256(content).hexdigest(),
                    ):
                        checked(command)
            except BaseException as error:
                failure = error
                raise
            finally:
                try:
                    checked(f"rm -f {temporary}/0 {temporary}/1 && rmdir {temporary}")
                except Exception:
                    if failure is None:
                        raise
                    logging.getLogger(__name__).error(
                        "Protected credential upload cleanup could not be verified"
                    )

    def reserve(self, holder: str, observed: dict) -> dict:
        return self.call({"action": "reserve", "holder": holder, "expected": digest(observed)})

    def release(self, holder: str, value: dict, journal: dict | None) -> dict:
        return self.call(
            {"action": "release", "holder": holder, "binding": value, "journal": journal}
        )

    def retire(self, holder: str, generation: str, journal: dict | None) -> dict:
        return self.call(
            {"action": "retire", "holder": holder, "generation": generation, "journal": journal}
        )


class ObservedJournal(RouteJournal):
    """Read-only preview of the runtime's authoritative creation receipts."""

    def __init__(self, project: str, gateway: str, state: dict | None) -> None:
        super().__init__(project, gateway)
        if state is not None:
            self.validate(state)
        self.observed = deepcopy(state)

    def snapshot(self) -> dict | None:
        return deepcopy(self.observed)

    def __enter__(self):
        raise RuntimeError("Runtime route preview cannot write creation receipts")

    def save(self):
        raise RuntimeError("Runtime route preview cannot publish creation receipts")


def import_journal(journal: RouteJournal, snapshot: dict | None) -> None:
    """Called only after guest reservation; preserve this workstation's pending work."""
    if snapshot is not None:
        journal.validate(snapshot)
    current = journal.snapshot()
    if current and current["transaction"] and current["transaction"]["phase"] == "pending":
        raise RuntimeError("Pending workstation routes must resume before importing runtime state")
    if snapshot is None:
        if current is not None:
            raise RuntimeError("Runtime handoff cannot discard route creation receipts")
        return
    with journal:
        previous = journal.state
        journal.state = deepcopy(snapshot)
        journal.state["revision"] = max(previous["revision"], snapshot["revision"])
        journal.save()
