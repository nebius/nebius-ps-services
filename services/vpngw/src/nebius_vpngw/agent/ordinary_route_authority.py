"""Durable route-writer handoff under the existing ordinary guest operation lock.

A workstation reservation has no timeout. Disconnect, reboot or an expired
process cannot grant the runtime ownership of an unsettled external cloud write.
"""

from __future__ import annotations

import json
import os
import re
import time
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


class RouteAuthorityConflict(RuntimeError):
    """The route owner refused a request because its authority did not match."""


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
            raise RouteAuthorityConflict("Runtime route authority is not protected")
        value = json.loads(record["content"])
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", "binding", "writer", "holder", "receipt"}
            or value["schema"] != SCHEMA
            or value["writer"] not in {"runtime", "workstation", "deployment"}
            or (value["writer"] == "workstation") != (value["holder"] is not None)
        ):
            raise RouteAuthorityConflict("Runtime route authority is malformed")
        if value["binding"] is not None:
            binding.validate(value["binding"])
        elif value["writer"] != "workstation":
            raise RouteAuthorityConflict("Runtime route authority has no binding")
        if value["holder"] is not None and not re.fullmatch(r"[a-f0-9]{64}", value["holder"]):
            raise RouteAuthorityConflict("Runtime route reservation has no exact command")
        receipt = value["receipt"]
        if value["writer"] == "deployment" and receipt is None:
            raise RouteAuthorityConflict("Retired route authority has no terminal receipt")
        if receipt is not None and (
            not isinstance(receipt, dict)
            or set(receipt) != {"holder", "request"}
            or any(
                not isinstance(item, str) or re.fullmatch(r"[a-f0-9]{64}", item) is None
                for item in receipt.values()
            )
        ):
            raise RouteAuthorityConflict("Runtime route handoff receipt is malformed")
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
                raise RouteAuthorityConflict("Another command retains runtime route ownership")
            return snapshot  # Resume never relies on elapsed time or process liveness.
        if digest(snapshot) != expected:
            raise RouteAuthorityConflict("Runtime routes changed since reviewed observation")
        journal = snapshot["journal"]
        if journal and journal["transaction"] and journal["transaction"]["phase"] != "complete":
            raise RouteAuthorityConflict(
                "Runtime route operation must settle before workstation handoff"
            )
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

    def supersede_reservation(self, previous: str, holder: str, expected: str) -> dict:
        """Transfer a settled workstation reservation without releasing runtime writers."""
        if previous == holder or any(
            not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None
            for value in (previous, holder, expected)
        ):
            raise ValueError("Reservation succession requires exact distinct owners")
        current = self.snapshot()
        owner = current["authority"]
        receipt = {"holder": holder, "request": digest(["supersede", previous, holder, expected])}
        if (
            owner
            and owner["writer"] == "workstation"
            and owner["holder"] == holder
            and owner["receipt"] == receipt
        ):
            return current  # Recover a lost reply through this exact transition.
        if (
            digest(current) != expected
            or owner is None
            or owner["writer"] != "workstation"
            or owner["holder"] != previous
        ):
            raise RouteAuthorityConflict("Reservation predecessor changed before succession")
        transaction = current["journal"]["transaction"] if current["journal"] else None
        if transaction and transaction["phase"] != "complete":
            raise RouteAuthorityConflict("Unsettled runtime routes block reservation succession")
        if owner["binding"] is None and (
            (self.root / "journal" / "recovery.json").exists()
            or (self.root / "journal" / ".recovery.json.vpngw-recovery").exists()
        ):
            raise RouteAuthorityConflict("Unbound route ledger cannot be hidden during succession")
        from ..deploy.operator_guest import publish

        directory = self.root / "history"
        protected_directory(directory)
        path = directory / (expected + ".json")
        archived = {
            "content": json.dumps(current, sort_keys=True),
            "uid": os.geteuid(),
            "gid": os.getegid(),
            "mode": 0o600,
        }
        existing = read_file(path)
        if existing is None:
            publish(path, None, archived)
        elif existing != archived:
            raise RouteAuthorityConflict("Reservation archive changed")
        self._save({**owner, "holder": holder, "receipt": receipt})
        return self.snapshot()

    def repair_reserve(
        self, holder: str, expected: str, operation: str, journal_state: dict
    ) -> dict:
        """Replace an obsolete reservation under the explicit cold repair owner."""
        from .. import repair_guest, repair_state

        repair = repair_state.Journal(repair_guest.ROOT).load()
        if (
            repair is None
            or repair["id"] != operation
            or repair["intent"]["ha"]
            or repair["phase"] not in {"installed", "enrolled"}
            or not re.fullmatch(r"[a-f0-9]{64}", holder)
        ):
            raise RouteAuthorityConflict(
                "Route repair requires the confirmed ordinary reconstruction"
            )
        repair_guest.verify_guard(operation)
        repair_guest.verify_quiescent()
        snapshot = self.snapshot()
        current = snapshot["authority"]
        receipt = {
            "holder": holder,
            "request": digest(["repair", operation, expected, journal_state]),
        }
        resumed = bool(
            current
            and current["writer"] == "workstation"
            and current["holder"] == holder
            and current["receipt"] == receipt
        )
        if not resumed and digest(snapshot) != expected:
            raise RouteAuthorityConflict("Route authority changed during explicit repair")
        project, gateway = repair["intent"]["project"], repair["intent"]["gateway"]
        ledger = RouteJournal(project, gateway, root=self.root / "journal")
        ledger.validate(journal_state)
        if journal_state["transaction"] is not None:
            raise RouteAuthorityConflict(
                "Repair route handoff requires settled predecessor effects"
            )
        previous = current["binding"] if current else None
        if previous and (previous["project"] != project or previous["gateway"] != gateway):
            raise RouteAuthorityConflict("Repair route authority crossed deployment identity")
        # Persist the reservation first. A lost reply can never release the old
        # runtime writer or revive an obsolete workstation command.
        self._save(
            {
                "schema": SCHEMA,
                "binding": previous,
                "writer": "workstation",
                "holder": holder,
                "receipt": receipt,
            }
        )
        with ledger:
            if ledger._before is not None:
                ledger.archive()
            ledger.state["bindings"] = deepcopy(journal_state["bindings"])
            if journal_state.get("enforced_predecessors"):
                ledger.state["enforced_predecessors"] = deepcopy(
                    journal_state["enforced_predecessors"]
                )
            else:
                ledger.state.pop("enforced_predecessors", None)
            if journal_state.get("ha_route_source"):
                ledger.state["ha_route_source"] = journal_state["ha_route_source"]
            ledger.state["transaction"] = None
            ledger.save()
        return self.snapshot()

    def repair_metadata(self, operation: str, reset: dict | None = None) -> dict:
        """Inspect or retire old metadata without trusting its parser or holder."""
        import hashlib

        from .. import repair_guest, repair_state
        from ..deploy import repair_route_metadata as metadata
        from ..deploy.repair_activation import archive_file

        repair = repair_state.Journal(repair_guest.ROOT).load()
        if (
            repair is None
            or repair["id"] != operation
            or repair["phase"] not in {"installed", "enrolled"}
        ):
            raise RouteAuthorityConflict(
                "Route metadata repair requires the confirmed reconstruction"
            )
        repair_guest.verify_guard(operation)
        repair_guest.verify_quiescent()
        project, gateway = repair["intent"]["project"], repair["intent"]["gateway"]
        ledger = RouteJournal(project, gateway, root=self.root / "journal")
        raw = repair_state.read(self.path, allow_hardlinks=True)
        fingerprint = hashlib.sha256(raw).hexdigest() if raw is not None else None
        previous = None
        try:
            current = self.read()
            previous = current["binding"] if current else binding.read()
        except (ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            try:
                previous = binding.read()
            except (ValueError, RuntimeError, TypeError, KeyError, AttributeError):
                pass
        if previous and (
            previous["project"] != project
            or previous["gateway"] != gateway
            or previous["node"] not in {item["name"] for item in repair["intent"]["targets"]}
        ):
            raise RouteAuthorityConflict("Repair route binding crossed deployment identity")
        if reset is None:
            return {
                "authority": fingerprint,
                "binding": previous,
                "metadata": metadata.observe(ledger),
            }
        archive = repair_guest.ROOT / "archives" / operation / "route-metadata"
        request_hash = repair_state.digest(reset)
        saved = repair_state.read(archive / "authority-reset.json")
        if saved is None:
            if fingerprint != reset["observed"]["authority"]:
                raise RouteAuthorityConflict("Route authority changed before repair retirement")
            repair_state.publish(archive / "authority-reset.json", {"request": request_hash})
        elif json.loads(saved)["request"] != request_hash:
            raise RouteAuthorityConflict("Repair route retirement changed its settled input")
        metadata.retire(
            ledger,
            archive / "journal",
            reset["observed"]["metadata"]["fingerprints"],
            reset["journal"],
        )
        expected = {
            "schema": SCHEMA,
            "binding": reset["observed"]["binding"],
            "writer": "workstation",
            "holder": digest(["repair", operation]),
            "receipt": None,
        }
        raw = repair_state.read(self.path, allow_hardlinks=True)
        if raw is not None and not metadata.json_equal(raw, expected):
            if hashlib.sha256(raw).hexdigest() != reset["observed"]["authority"]:
                raise RouteAuthorityConflict("Route authority changed during repair retirement")
            archive_file(self.path, archive / "authority.json")
        self._save(expected)
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
            raise RouteAuthorityConflict(
                "Runtime route handoff has no exact workstation reservation"
            )
        if current["binding"] and any(
            current["binding"][key] != desired[key] for key in ("project", "gateway", "node")
        ):
            raise RouteAuthorityConflict("Runtime route handoff crossed its deployment identity")
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
                raise RouteAuthorityConflict(
                    "Unsettled cloud operations cannot hand off to runtime"
                )
        elif journal.snapshot() is not None:
            raise RouteAuthorityConflict("Runtime route handoff cannot discard creation receipts")
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
            raise RouteAuthorityConflict("Runtime retirement has no exact workstation reservation")
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
            raise RouteAuthorityConflict("Runtime route writer does not own this binding")


def action(request: dict) -> dict:
    """Pinned SSH protocol; every mutation uses the ordinary guest writer lock."""
    from .. import ordinary_operations as operations
    from . import ordinary

    if not isinstance(request, dict) or request.get("action") not in {
        "inspect",
        "reserve",
        "supersede-reservation",
        "release",
        "retire",
        "repair-reserve",
        "repair-inspect",
        "repair-reset",
    }:
        raise ValueError("Invalid ordinary route handoff request")
    expected = {
        "inspect": {"action"},
        "reserve": {"action", "holder", "expected"},
        "supersede-reservation": {"action", "previous", "holder", "expected"},
        "release": {"action", "holder", "binding", "journal"},
        "retire": {"action", "holder", "generation", "journal"},
        "repair-reserve": {"action", "holder", "expected", "operation", "journal"},
        "repair-inspect": {"action", "operation"},
        "repair-reset": {"action", "operation", "reset"},
    }[request["action"]]
    if set(request) != expected:
        raise ValueError("Expanded ordinary route handoff request")
    # Wait only before admission, within the existing 15-second SSH request
    # allowance. Periodic writers retain their nonblocking lock behavior.
    # Neither the action nor a potentially accepted effect is retried.
    with operations.mutation_lock(deadline=time.monotonic() + 10):
        owner = RouteAuthority()
        if request["action"] == "inspect":
            return owner.snapshot()
        if request["action"] in {"repair-inspect", "repair-reset"}:
            return owner.repair_metadata(request["operation"], request.get("reset"))
        operations.require_idle()
        if request["action"] == "repair-reserve":
            return owner.repair_reserve(
                request["holder"], request["expected"], request["operation"], request["journal"]
            )
        if request["action"] == "reserve":
            return owner.reserve(request["holder"], request["expected"])
        if request["action"] == "supersede-reservation":
            return owner.supersede_reservation(
                request["previous"], request["holder"], request["expected"]
            )
        if request["action"] == "retire":
            import hashlib

            if hashlib.sha256(ordinary.CONFIG.read_bytes()).hexdigest() != request["generation"]:
                raise RouteAuthorityConflict(
                    "Installed configuration changed before runtime retirement"
                )
            result = owner.retire(request["holder"], request["generation"], request["journal"])
            _retire_enforcement()
            return result
        desired = binding.validate(request["binding"])
        binding.authority(
            desired, ordinary.CONFIG.read_bytes(), operations.BOOT.read_text().strip()
        )
        if binding.read() != desired:
            raise RouteAuthorityConflict("Installed runtime binding differs from the handoff")
        from .ordinary_cloud_routes import OrdinaryCloudRoutes

        OrdinaryCloudRoutes._credentials(desired)
        from ..routing_semantics import bind_ordinary_locked

        bind_ordinary_locked(desired)
        result = owner.release(request["holder"], desired, request["journal"])
        _retire_enforcement()
        return result


def _retire_enforcement() -> None:
    """Called under the guest lock after importing the settled cloud receipt."""
    import yaml

    from . import ordinary

    cfg = yaml.safe_load(ordinary.CONFIG.read_bytes())
    if not isinstance(cfg, dict):
        raise RouteAuthorityConflict("Enforcement retirement requires ordinary configuration")
    if cfg.get("vm_ha") is not None:
        if ordinary.destination_protection(cfg).enforced().read():
            raise RouteAuthorityConflict(
                "HA handoff must transfer ordinary enforcement before retirement"
            )
        return
    ordinary.destination_protection(cfg).enforced().retire()
