"""Durable route-writer handoff under the existing ordinary guest operation lock.

A workstation reservation has no timeout. Disconnect, reboot or an expired
process cannot grant the runtime ownership of an unsettled external cloud write.
"""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from pathlib import Path

from .. import ordinary_route_binding as binding
from ..apply_approval import digest
from ..deploy.operator_guest import read_file
from ..deploy.operator_keys import protected_directory
from ..deploy.ordinary_route_ownership import RouteJournal
from .vm_ha.store import atomic_write_json

SCHEMA = "nebius-vpngw.ordinary-route-authority.v1"
ROOT = Path("/var/lib/nebius-vpngw/ordinary/cloud-routes")


class RouteAuthority:
    """Caller holds ordinary_operations.mutation_lock across observation/effects."""

    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.path = root / "authority.json"

    def read(self) -> dict | None:
        record = read_file(self.path)
        if record is None:
            return None
        if record["uid"] != os.geteuid() or record["mode"] != 0o600:
            raise RuntimeError("Runtime route authority is not protected")
        value = json.loads(record["content"])
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", "binding", "writer", "holder", "receipt"}
            or value["schema"] != SCHEMA
            or value["writer"] not in {"runtime", "workstation", "deployment"}
            or (value["writer"] == "workstation") != (value["holder"] is not None)
        ):
            raise RuntimeError("Runtime route authority is malformed")
        if value["binding"] is not None:
            binding.validate(value["binding"])
        elif value["writer"] != "workstation":
            raise RuntimeError("Runtime route authority has no binding")
        if value["holder"] is not None and not re.fullmatch(r"[a-f0-9]{64}", value["holder"]):
            raise RuntimeError("Runtime route reservation has no exact command")
        receipt = value["receipt"]
        if value["writer"] == "deployment" and receipt is None:
            raise RuntimeError("Retired route authority has no terminal receipt")
        if receipt is not None and (
            not isinstance(receipt, dict)
            or set(receipt) != {"holder", "request"}
            or any(
                not isinstance(item, str) or re.fullmatch(r"[a-f0-9]{64}", item) is None
                for item in receipt.values()
            )
        ):
            raise RuntimeError("Runtime route handoff receipt is malformed")
        return value

    def journal(self, value: dict) -> RouteJournal:
        return RouteJournal(value["project"], value["gateway"], root=self.root / "journal")

    def snapshot(self) -> dict:
        value = self.read()
        journal = self.journal(value["binding"]).snapshot() if value and value["binding"] else None
        return {"authority": value, "journal": journal}

    def _save(self, value: dict) -> None:
        protected_directory(self.root)
        atomic_write_json(self.path, value)

    def reserve(self, holder: str, expected: str) -> dict:
        if not re.fullmatch(r"[a-f0-9]{64}", holder):
            raise ValueError("Route writer reservation requires an exact command identity")
        snapshot = self.snapshot()
        current = snapshot["authority"]
        if current and current["writer"] == "workstation":
            if current["holder"] != holder:
                raise RuntimeError("Another command retains runtime route ownership")
            return snapshot  # Resume never relies on elapsed time or process liveness.
        if digest(snapshot) != expected:
            raise RuntimeError("Runtime routes changed since reviewed observation")
        journal = snapshot["journal"]
        if journal and journal["transaction"] and journal["transaction"]["phase"] != "complete":
            raise RuntimeError("Runtime route operation must settle before workstation handoff")
        self._save(
            {
                "schema": SCHEMA,
                "binding": current["binding"] if current else None,
                "writer": "workstation",
                "holder": holder,
                "receipt": current["receipt"] if current else None,
            }
        )
        return self.snapshot()

    def release(self, holder: str, desired: dict, journal_state: dict | None) -> dict:
        desired = binding.validate(desired)
        current = self.read()
        receipt = {"holder": holder, "request": digest([desired, journal_state])}
        if (
            current
            and current["writer"] == "runtime"
            and current["receipt"] == receipt
            and current["binding"] == desired
        ):
            return self.snapshot()
        if current is None or current["writer"] != "workstation" or current["holder"] != holder:
            raise RuntimeError("Runtime route handoff has no exact workstation reservation")
        if current["binding"] and any(
            current["binding"][key] != desired[key] for key in ("project", "gateway", "node")
        ):
            raise RuntimeError("Runtime route handoff crossed its deployment identity")
        self._import_journal(desired, journal_state)
        self._save(
            {
                "schema": SCHEMA,
                "binding": desired,
                "writer": "runtime",
                "holder": None,
                "receipt": receipt,
            }
        )
        return self.snapshot()

    def _import_journal(self, desired: dict, journal_state: dict | None) -> None:
        journal = self.journal(desired)
        if journal_state is not None:
            journal.validate(journal_state)
            if journal_state["transaction"] and journal_state["transaction"]["phase"] != "complete":
                raise RuntimeError("Unsettled cloud operations cannot hand off to runtime")
        elif journal.snapshot() is not None:
            raise RuntimeError("Runtime route handoff cannot discard creation receipts")
        # Publication is ordered: a crash after the journal write leaves the
        # durable workstation reservation, so the runtime still cannot mutate.
        if journal_state is not None:
            with journal:
                previous = journal.state
                journal.state = deepcopy(journal_state)
                journal.state["revision"] = max(previous["revision"], journal_state["revision"])
                journal.save()

    def retire(self, holder: str, generation: str, journal_state: dict | None) -> dict:
        """Transfer to the deployment owner without restoring a runtime writer."""
        if not re.fullmatch(r"[a-f0-9]{64}", generation):
            raise ValueError("Retirement requires an exact installed generation")
        current = self.read()
        receipt = {"holder": holder, "request": digest(["deployment", generation, journal_state])}
        if current and current["writer"] == "deployment" and current["receipt"] == receipt:
            return self.snapshot()
        if (
            current is None
            or current["writer"] != "workstation"
            or current["holder"] != holder
            or current["binding"] is None
        ):
            raise RuntimeError("Runtime retirement has no exact workstation reservation")
        self._import_journal(current["binding"], journal_state)
        self._save(
            {
                "schema": SCHEMA,
                "binding": current["binding"],
                "writer": "deployment",
                "holder": None,
                "receipt": receipt,
            }
        )
        return self.snapshot()

    def require_runtime(self, expected: dict) -> None:
        current = self.read()
        if current is None or current["writer"] != "runtime" or current["binding"] != expected:
            raise RuntimeError("Runtime route writer does not own this binding")


def action(request: dict) -> dict:
    """Pinned SSH protocol; every mutation uses the ordinary guest writer lock."""
    from .. import ordinary_operations as operations
    from . import ordinary

    if not isinstance(request, dict) or request.get("action") not in {
        "inspect",
        "reserve",
        "release",
        "retire",
    }:
        raise ValueError("Invalid ordinary route handoff request")
    expected = {
        "inspect": {"action"},
        "reserve": {"action", "holder", "expected"},
        "release": {"action", "holder", "binding", "journal"},
        "retire": {"action", "holder", "generation", "journal"},
    }[request["action"]]
    if set(request) != expected:
        raise ValueError("Expanded ordinary route handoff request")
    with operations.mutation_lock(wait=operations.HANDOFF_LOCK_WAIT):
        owner = RouteAuthority()
        if request["action"] == "inspect":
            return owner.snapshot()
        operations.require_idle()
        if request["action"] == "reserve":
            return owner.reserve(request["holder"], request["expected"])
        if request["action"] == "retire":
            import hashlib

            if hashlib.sha256(ordinary.CONFIG.read_bytes()).hexdigest() != request["generation"]:
                raise RuntimeError("Installed configuration changed before runtime retirement")
            return owner.retire(request["holder"], request["generation"], request["journal"])
        desired = binding.validate(request["binding"])
        binding.authority(
            desired, ordinary.CONFIG.read_bytes(), operations.BOOT.read_text().strip()
        )
        if binding.read() != desired:
            raise RuntimeError("Installed runtime binding differs from the handoff")
        from .ordinary_cloud_routes import OrdinaryCloudRoutes

        OrdinaryCloudRoutes._credentials(desired)
        return owner.release(request["holder"], desired, request["journal"])
