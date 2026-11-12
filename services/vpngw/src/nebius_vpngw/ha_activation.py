"""Fenced command-repair evidence shared by SSH and systemd-owned processes.

The apply lock is the authority. The attempt record narrows its phase and scope;
it cannot enable forwarding, change policy, install packages or move ownership.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

from . import ha_repair
from .apply_approval import digest

STATE = Path("/var/lib/nebius-vpngw/vm-ha")
CONFIG = Path("/etc/nebius-vpngw/config-resolved.yaml")
BOOT = Path("/proc/sys/kernel/random/boot_id")
FORWARDING = Path("/proc/sys/net/ipv4/ip_forward")
UNITS = (
    "nebius-vpngw-agent.service",
    "strongswan.service",
    "frr.service",
    "nebius-vpngw-vm-ha.service",
    "nebius-vpngw-vm-ha-rearm.service",
)
SCHEMA = "nebius-vpngw.activation-attempt.v2"
WRITER = Path("/run/nebius-vpngw/activation-writer.lock")


def require_authority(request: dict, *, active_unlock: bool = False) -> str:
    ha_repair.exact_lock(request["lock"], STATE)
    boot = BOOT.read_text().strip()
    guard = ha_repair.read_json(STATE / "guard.json")
    configuration = CONFIG.read_bytes()
    if hashlib.sha256(configuration).hexdigest() != request["configuration"]:
        raise RuntimeError("activation repair configuration differs")
    cold = (
        set(guard) == {"schema", "guard_boot_id", "data_plane_mode", "installed_at"}
        and guard.get("data_plane_mode") == "blocked"
        and isinstance(guard.get("installed_at"), (int, float))
        and not isinstance(guard["installed_at"], bool)
        and math.isfinite(guard["installed_at"])
    )
    if cold:
        import yaml

        config = yaml.safe_load(configuration)
        vm_ha = config.get("vm_ha", {}) if isinstance(config, dict) else {}
        binding = vm_ha.get("runtime_binding", {})
        if not (
            vm_ha.get("cluster_id") == request["lock"]["cluster_id"]
            and vm_ha.get("node", {}).get("node_id") == request["lock"]["node_id"]
            and vm_ha.get("generation", {}).get("generation_id") == request["lock"]["generation_id"]
            and binding.get("cluster_id") == request["lock"]["cluster_id"]
            and binding.get("generation_id") == request["lock"]["generation_id"]
            and sum(
                node.get("node_id") == request["lock"]["node_id"]
                for node in binding.get("nodes", [])
            )
            == 1
        ):
            raise RuntimeError("activation cold guard has no exact installed node binding")
    if (
        guard.get("schema") != "nebius-vpngw/vm-ha-status-v1"
        or guard.get("guard_boot_id") != boot
        or (not cold and guard.get("node_id") != request["lock"]["node_id"])
        or guard.get("data_plane_mode")
        not in ({"active"} if active_unlock else {"blocked", "passive"})
        or FORWARDING.read_text().strip() != ("1" if active_unlock else "0")
    ):
        raise RuntimeError("activation repair requires a current forwarding fence")
    if any(
        (STATE / name).exists() or (STATE / name).is_symlink()
        for name in (
            "mtls/inhibition.json",
            "standby-replacement.inhibition.json",
            "accepted-cloud-operation.json",
            "rearm-cloud-operation.json",
            "standby-auto-healing-recovery.json",
        )
    ):
        raise RuntimeError("activation repair has another inhibition owner")
    restoration = STATE / "standby-restoration-authorization.json"
    if restoration.exists() or restoration.is_symlink():
        from .agent.vm_ha.restoration import RestorationPhase, StandbyRestorationStore

        store = StandbyRestorationStore(STATE)
        protected = ha_repair.read_json(restoration)
        receipt = ha_repair.read_json(store.receipt_path)
        record = store.load()
        if (
            record is None
            or record.to_dict() != protected
            or ha_repair.read_json(store.receipt_path) != receipt
            or record.phase is not RestorationPhase.COMPLETED
            or record.cluster_id != request["lock"]["cluster_id"]
            or record.owner_node_id != request["lock"]["node_id"]
            or record.generation_id != request["lock"]["generation_id"]
        ):
            raise RuntimeError("activation repair has unfinished or foreign restoration")
        # This exact receipt-bound completion is history, not another writer.
        # Keep the record and all current operation/forwarding fences intact.
    return boot


def unit_state(unit: str, *, deadline: float) -> dict:
    result = subprocess.run(
        [
            "systemctl",
            "show",
            "--all",
            unit,
            "--property=LoadState,ActiveState,SubState,Job,MainPID,ControlPID",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=max(0.001, min(5.0, deadline - time.monotonic())),
    )
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if (
        set(values) != {"LoadState", "ActiveState", "SubState", "Job", "MainPID", "ControlPID"}
        or values["LoadState"] != "loaded"
        or values["ActiveState"]
        not in {"active", "inactive", "failed", "activating", "deactivating"}
        or not values["MainPID"].isdigit()
        or not values["ControlPID"].isdigit()
    ):
        raise RuntimeError("activation service evidence unavailable")
    return values


def inspect(request: dict, *, deadline: float) -> dict:
    before = require_authority(request)
    units = {unit: unit_state(unit, deadline=deadline) for unit in UNITS}
    if before != require_authority(request) or time.monotonic() >= deadline:
        raise RuntimeError("activation repair observation changed")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("activation observation deadline expired")
    from .agent.main import require_vm_ha_current_boot_materialization

    materialized: bool | None
    try:
        require_vm_ha_current_boot_materialization(state_dir=STATE, handoff_attempts=1)
        materialized = True
    except RuntimeError as error:
        # Only a missing/mismatched receipt proves incomplete configuration.
        # Busy writers or unavailable authority remain unknown, not a repair cause.
        materialized = (
            False if str(error) == "VM-HA passive materialization is incomplete" else None
        )
    if before != require_authority(request):
        raise RuntimeError("activation observation authority changed")
    completion_path = STATE / "activation-complete.json"
    completion = ha_repair.read_json(completion_path) if completion_path.exists() else None
    complete = completion == {
        "schema": "nebius-vpngw.activation-complete.v1",
        "boot": before,
        "lock": request["lock"],
        "configuration": request["configuration"],
    }
    return {
        "boot": before,
        "units": units,
        "materialized": materialized,
        "complete": complete,
        "clock": time.monotonic(),
    }


def read_attempt(*, state_dir: Path = STATE) -> dict | None:
    path = state_dir / "activation-attempt.json"
    if not path.exists() and not path.is_symlink():
        return None
    value = ha_repair.read_json(path)
    if (
        set(value)
        != {"schema", "lock", "configuration", "boot", "started_at", "identity", "scope", "state"}
        or value["schema"] != SCHEMA
        or value["scope"] not in {"activation", "unlock"}
        or value["state"] not in {"active", "retired"}
        or not isinstance(value["started_at"], (int, float))
        or isinstance(value["started_at"], bool)
        or not math.isfinite(value["started_at"])
        or not isinstance(value["lock"], dict)
        or not isinstance(value["boot"], str)
        or not value["boot"]
        or any(
            not isinstance(value[key], str) or re.fullmatch(r"[a-f0-9]{64}", value[key]) is None
            for key in ("identity", "configuration")
        )
    ):
        raise RuntimeError("activation attempt is invalid")
    return value


def command_deadline(*, state_dir: Path = STATE) -> float | None:
    """Only an exact live apply lock admits a command-owned effect budget."""
    value = read_attempt(state_dir=state_dir)
    if value is None:
        return None
    path = state_dir / "apply.lock"
    if path.exists() or path.is_symlink():
        current = ha_repair.read_json(path)
        if current != value["lock"]:
            return None  # A different exact command never inherits this allowance.
    if value["boot"] != BOOT.read_text().strip():
        raise RuntimeError("activation attempt belongs to an earlier boot")
    if hashlib.sha256(CONFIG.read_bytes()).hexdigest() != value["configuration"]:
        raise RuntimeError("activation attempt configuration changed")
    if value["state"] != "active":
        raise RuntimeError("activation attempt is retired")
    return float("inf")


def arm(request: dict) -> dict:
    deadline = request["started_at"]
    if (
        not isinstance(deadline, (int, float))
        or isinstance(deadline, bool)
        or not math.isfinite(deadline)
    ):
        raise ValueError("invalid activation attempt budget")
    boot = (
        require_authority(request, active_unlock=True)
        if request.get("unlock_only") is True
        else require_authority(request)
    )
    scope = "unlock" if request.get("unlock_only") is True else "activation"
    previous = read_attempt()
    if previous is not None and previous["identity"] == request["identity"]:
        if (
            previous["lock"] != request["lock"]
            or previous["scope"] != scope
            or previous["configuration"] != request["configuration"]
            or previous["boot"] != boot
        ):
            raise RuntimeError("activation attempt authority changed")
        if previous["state"] != "active":
            raise RuntimeError("activation attempt is retired")
        return previous  # Never refresh the guest deadline on a lost reply.
    value = {
        "schema": SCHEMA,
        "scope": scope,
        "state": "active",
        "lock": request["lock"],
        "configuration": request["configuration"],
        "boot": boot,
        "started_at": request["started_at"],
        "identity": request["identity"],
    }
    ha_repair.publish(STATE / "activation-attempt.json", json.dumps(value).encode())
    return value


def repair_service(request: dict) -> dict:
    attempt = read_attempt()
    if (
        attempt is None
        or attempt["identity"] != request["identity"]
        or attempt["scope"] != "activation"
    ):
        raise RuntimeError("activation service repair has no service-scoped attempt")
    deadline = command_deadline()
    if deadline is None:
        raise RuntimeError("activation service repair deadline unavailable")
    observed = inspect(request, deadline=deadline)
    if (
        digest({key: value for key, value in observed.items() if key != "clock"})
        != request["fingerprint"]
    ):
        raise RuntimeError("activation service repair evidence changed")
    selected = [
        unit
        for unit, state in observed["units"].items()
        if state["ActiveState"] in {"failed", "inactive"}
    ]
    resume_phase = request.get("resume_phase") in {
        "verify-active-forwarding-and-routes",
        "verify-passive-unlocked-non-forwarding",
    }
    if (
        resume_phase
        and request.get("action_kind") == "configuration"
        and not selected
        and observed["materialized"] is True
        and observed["complete"] is True
        and all(
            not state["Job"] and state["ControlPID"] == "0" and state["ActiveState"] == "active"
            for state in observed["units"].values()
        )
    ):
        return observed  # Exact fenced configuration is complete; resume only the owning unlock.
    configuration_fault = request.get("action_kind") == "configuration" and (
        observed["materialized"] is False or observed["complete"] is False
    )
    if (not selected and not configuration_fault) or any(
        state["Job"]
        or state["ControlPID"] != "0"
        or state["ActiveState"] in {"activating", "deactivating"}
        for state in observed["units"].values()
    ):
        raise RuntimeError("activation service repair requires settled local failure")
    for unit in selected:
        require_authority(request)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("activation reset-failed deadline expired")
        subprocess.run(
            ["systemctl", "reset-failed", unit],
            check=True,
            capture_output=True,
            timeout=min(5, remaining),
        )
    # The controller owns native materialization. Starting it re-enters that
    # canonical path; never invoke the ordinary agent from an SSH repair.
    controller = "nebius-vpngw-vm-ha.service"
    selected = [
        controller,
        *(
            ["nebius-vpngw-vm-ha-rearm.service"]
            if "nebius-vpngw-vm-ha-rearm.service" in selected
            else []
        ),
    ]
    for unit in selected:
        require_authority(request)
        if time.monotonic() >= deadline:
            raise TimeoutError("activation service repair deadline expired")
        subprocess.run(
            ["systemctl", "reset-failed", unit],
            check=True,
            capture_output=True,
            timeout=max(0.001, min(5, deadline - time.monotonic())),
        )
        # Submission is asynchronous. A disconnect never authorizes submission
        # again: the workstation attempt remains dispatched/uncertain until readback.
        subprocess.run(
            ["systemctl", "--no-block", "--job-mode=fail", "restart", unit],
            check=True,
            capture_output=True,
            timeout=max(0.001, min(5, deadline - time.monotonic())),
        )
        while time.monotonic() < deadline:
            state = unit_state(unit, deadline=deadline)
            if not state["Job"] and state["ControlPID"] == "0" and state["ActiveState"] == "active":
                break
            if (
                not state["Job"]
                and state["ControlPID"] == "0"
                and state["ActiveState"] in {"inactive", "failed"}
            ):
                raise RuntimeError("activation service repair failed")
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))
        else:
            raise TimeoutError("activation service repair outcome unresolved")
    return inspect(request, deadline=deadline)


def main() -> None:
    writer = os.open(WRITER, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        metadata = os.fstat(writer)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o022
        ):
            raise RuntimeError("activation writer lock is unsafe")
        fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
        request = json.loads(base64.b64decode(sys.argv[1], validate=True))
        action = request.pop("action")
        if action == "inspect":
            result = inspect(request, deadline=time.monotonic() + 20)
        elif action == "inspect-unlock":
            result = {
                "boot": require_authority(request, active_unlock=True),
                "clock": time.monotonic(),
            }
        elif action == "arm":
            result = arm(request)
        elif action == "service":
            result = repair_service(request)
        elif action == "mark":
            boot = require_authority(request)
            result = {
                "schema": "nebius-vpngw.activation-complete.v1",
                "boot": boot,
                "lock": request["lock"],
                "configuration": request["configuration"],
            }
            ha_repair.publish(STATE / "activation-complete.json", json.dumps(result).encode())
        elif action == "finish":
            value = read_attempt()
            if value is not None and (
                value["configuration"] != request["configuration"]
                or hashlib.sha256(CONFIG.read_bytes()).hexdigest() != request["configuration"]
            ):
                raise RuntimeError("activation cleanup configuration or boot changed")
            if value is not None and (
                value["identity"] != request["identity"] or value["lock"] != request["lock"]
            ):
                raise RuntimeError("activation cleanup authority changed")
            if not isinstance(request.get("verified"), bool):
                raise RuntimeError("activation cleanup disposition is missing")
            if value is not None:
                if value["boot"] != BOOT.read_text().strip():
                    # Retire only under freshly proved current authority; an old
                    # boot's permit is never revived or used for another effect.
                    require_authority(request, active_unlock=value["scope"] == "unlock")
                if request["verified"]:
                    (STATE / "activation-attempt.json").unlink()
                    ha_repair.sync_directory(STATE)
                else:
                    # Retire an unsuccessful attempt without reopening native
                    # effects. Only the next durably reserved attempt replaces it.
                    value["state"] = "retired"
                    ha_repair.publish(STATE / "activation-attempt.json", json.dumps(value).encode())
            result = {"finished": True}
        else:
            raise ValueError("unknown activation action")
        print(json.dumps(result, sort_keys=True))
    except Exception:
        print(json.dumps({"error": "activation-authority-or-observation-unavailable"}))
        raise SystemExit(1) from None
    finally:
        os.close(writer)


if __name__ == "__main__":
    main()
