"""Package-independent backend of the durable full-repair package worker."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import stat
import sys
import sysconfig
import zipfile
from pathlib import Path

try:
    import _vpngw_deployment_assets as assets  # type: ignore[import-not-found]
    import _vpngw_package_worker as worker  # type: ignore[import-not-found]
    import _vpngw_prior_package_worker as prior_worker  # type: ignore[import-not-found]
    import _vpngw_python_package_recovery as python  # type: ignore[import-not-found]
    import _vpngw_repair_guest as guest  # type: ignore[import-not-found]
    import _vpngw_repair_runtime as runtime  # type: ignore[import-not-found]
    import _vpngw_repair_state as state  # type: ignore[import-not-found]
except ModuleNotFoundError:
    from .. import deployment_assets as assets
    from .. import package_worker as prior_worker
    from .. import package_worker as worker
    from .. import python_package_recovery as python
    from .. import repair_guest as guest
    from .. import repair_runtime as runtime
    from .. import repair_state as state


def receive() -> dict:
    raise RuntimeError("Repair packages require durable worker transport")


def reply(_value: dict) -> None:
    raise RuntimeError("Repair packages require durable worker transport")


def admitted(request: dict, *, verification: bool = False) -> tuple[state.Journal, dict, Path]:
    if (
        set(request) != {"action", "operation", "intent", "package"}
        or request["action"] != "repair-install"
        or not isinstance(request["operation"], str)
        or not re.fullmatch(r"[a-f0-9]{32}", request["operation"])
        or not isinstance(request["package"], dict)
        or set(request["package"]) != {"native", "wheels", "product"}
    ):
        raise RuntimeError("Repair package request is incomplete")
    journal = state.Journal(guest.ROOT)
    value = journal.load()
    if (
        value is None
        or value["id"] != request["operation"]
        or state.digest(value["intent"]) != request["intent"]
    ):
        raise RuntimeError("Repair package request does not match confirmed operation")
    phases = (
        set(state.PHASES[state.PHASES.index("installed") :])
        if verification
        else {"staged", "reset", "installed"}
    )
    if value["phase"] not in phases:
        raise RuntimeError("Repair packages are outside their admitted phase")
    package = request["package"]
    runtime.migration.validate_plan(package["native"])
    if (
        not isinstance(package["wheels"], dict)
        or package["product"] not in package["wheels"]
        or package["wheels"][package["product"]] != value["intent"]["artifact"]
    ):
        raise RuntimeError("Repair Python closure has no exact selected product")
    directory = guest.ROOT / "payload" / value["id"]
    for name, expected in package["wheels"].items():
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or not name.endswith(".whl")
            or not re.fullmatch(r"[a-f0-9]{64}", expected)
        ):
            raise RuntimeError("Repair wheel identity is invalid")
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("Repair wheel is not a staged regular file")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
        if digest.hexdigest() != expected:
            raise RuntimeError("Repair staged wheel changed")
    runtime.verify_archives(package["native"], directory)
    return journal, value, directory


def recovery_admission(request: dict) -> None:
    """The transport has already proved the old worker/cgroup is quiescent."""
    _journal, value, _directory = admitted(request)
    guest.verify_guard(value["id"])
    guest.verify_quiescent()
    if guest.POLICY.read_bytes() != guest.POLICY_BYTES:
        raise RuntimeError("Repair package startup protection is missing")


def verify_installation(request: dict) -> dict:
    _journal, value, directory = admitted(request, verification=True)
    guest.verify_guard(value["id"])
    # Enrollment may have completed before the controller lost its reply.
    # Package verification is read-only; only package mutation needs quiet
    # product services. The independent boot guard still binds this repair.
    inventory = json.loads(state.read(directory / "native-inventory.json") or b"null")
    if not inventory or inventory["archives"] != state.digest(
        request["package"]["native"]["archives"]
    ):
        raise RuntimeError("Repair installation inventory has changed")
    runtime.migration.verify_packages(request["package"]["native"])
    # After enrollment the canonical activation owner renders these conffiles.
    # A lost reply can therefore leave valid generated content where the deb
    # shipped defaults. Activation rechecks the selected configuration before
    # enabling traffic; package proof still checks every non-rendered payload.
    rendered = (
        set(guest.GENERATED_FILES).intersection(inventory["conffiles"])
        if state.PHASES.index(value["phase"]) >= state.PHASES.index("enrolled")
        else set()
    )
    runtime.verify_inventory(inventory, rendered=rendered)
    for name, digest in request["package"]["wheels"].items():
        checkpoint = directory / "python" / digest
        record = python.load(checkpoint, directory / name)
        if record["state"] != "complete" or not python.complete(checkpoint, record):
            raise RuntimeError("Repair Python installation no longer verifies")
    verify_assets(request, value, directory)
    return {
        "operation": value["id"],
        "phase": "installed",
        "boot": guest.BOOT.read_text().strip(),
        "guard": hashlib.sha256(guest.GUARD).hexdigest(),
        "package": state.digest(request["package"]),
    }


def asset_payloads(request: dict, value: dict, directory: Path) -> list[tuple[Path, bytes, int]]:
    """Resolve only canonical system destinations from the admitted release wheel."""
    with zipfile.ZipFile(directory / request["package"]["product"]) as archive:
        result = [
            (Path(path), archive.read(member), mode)
            for member, path, mode in assets.entries(ha=value["intent"]["ha"])
        ]
    for path, _raw, _mode in result:
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
            raise RuntimeError("Repair service asset has an unsafe path")
        if path.exists():
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                raise RuntimeError("Repair service asset has foreign ownership or type")
    return result


def verify_assets(request: dict, value: dict, directory: Path) -> None:
    for path, expected, mode in asset_payloads(request, value, directory):
        try:
            info = path.stat()
            actual = path.read_bytes()
        except OSError as error:
            raise RuntimeError("Repair service asset is missing or unreadable") from error
        if actual != expected or stat.S_IMODE(info.st_mode) != mode or info.st_gid != os.getegid():
            raise RuntimeError("Repair service asset differs from the selected release")


@contextlib.contextmanager
def native_exclusion():
    with worker.native_lock("/var/lib/dpkg/lock-frontend"):
        with worker.native_lock("/var/lib/dpkg/lock"):
            pass
        previous = os.environ.get("DPKG_FRONTEND_LOCKED")
        os.environ["DPKG_FRONTEND_LOCKED"] = "1"
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop("DPKG_FRONTEND_LOCKED", None)
            else:
                os.environ["DPKG_FRONTEND_LOCKED"] = previous


def require_shared_dependencies(desired: dict[str, str]) -> None:
    """Check fresh consumer metadata without importing damaged local packages."""
    scope = python.dependency_scope
    source = getattr(scope, "_SOURCE", None)
    if source is None:
        source = Path(scope.__file__).read_bytes()
    script = """
import base64, importlib.metadata, json, sys
try:
    sys.path.append('/usr/lib/python3/dist-packages')
    from pip._vendor.packaging.markers import default_environment
    from pip._vendor.packaging.requirements import Requirement
    payload = json.loads(sys.argv[1])
    scope = {}
    exec(compile(base64.b64decode(payload['source']), '<dependency-scope>', 'exec'), scope)
    observed = {'dependencies': {}, 'requirements': {}, 'candidates': {},
                'markers': default_environment()}
    # Discover metadata on the original guest paths without importing their code.
    for distribution in importlib.metadata.distributions(path=payload['paths']):
        try:
            name = distribution.metadata['Name']
            version = distribution.version
            requirements = list(distribution.requires or [])
        except (ValueError, TypeError, KeyError, AttributeError, OSError):
            continue
        if not isinstance(name, str) or not name:
            continue
        key = scope['name'](name)
        observed['dependencies'].setdefault(key, version)
        observed['requirements'].setdefault(key, requirements)
        observed['candidates'].setdefault(key, []).append({'requirements': requirements})
    scope['check_shared_conflicts'](observed, payload['desired'], parse_requirement=Requirement)
except Exception as error:
    code = getattr(error, 'code', 'dependency-resolver-unavailable')
    print(json.dumps({'error_code': code}))
else:
    print(json.dumps({'status': 'ok'}))
"""
    code, output = guest.command(
        [sys.executable, "-I", "-S", "-B", "-c", script, json.dumps({
            "source": base64.b64encode(source).decode(),
            "paths": sys.path,
            "desired": desired,
        })],
        required=False,
    )
    try:
        result = json.loads(output)
    except (TypeError, ValueError):
        result = None
    if code == 0 and result == {"status": "ok"}:
        return
    failure = result.get("error_code") if isinstance(result, dict) else None
    if not isinstance(failure, str) or failure not in scope.DependencyFailure.CODES:
        failure = "dependency-resolver-unavailable"
    raise scope.DependencyFailure(failure, "Repair shared dependency verification failed")


def execute(request: dict) -> dict:
    with guest.lock():
        journal, value, directory = admitted(request)
        operation = value["id"]

        def check() -> None:
            current = journal.load()
            if (
                current is None
                or current["id"] != operation
                or current["phase"] not in {"staged", "reset", "installed"}
            ):
                raise RuntimeError("Repair package authority changed")
            guest.verify_guard(operation)
            guest.verify_quiescent()
            if guest.POLICY.read_bytes() != guest.POLICY_BYTES:
                raise RuntimeError("Repair package startup protection changed")

        check()
        if value["phase"] == "staged":
            journal.effect("reset", {"operation": operation})
            receipt = guest.reset(operation)
            journal.effect("reset", {"operation": operation}, state="complete", receipt=receipt)
            value = journal.advance("reset")
        with native_exclusion():
            inventory_path = directory / "native-inventory.json"
            raw = state.read(inventory_path)
            if raw is None:
                inventory = runtime.archive_inventory(
                    request["package"]["native"], directory, guest.command
                )
                state.publish(inventory_path, inventory)
            else:
                inventory = json.loads(raw)
            package_request = request["package"]
            effect = value["effects"].get("native-packages")
            if effect is None or effect["state"] != "complete":
                journal.effect("native-packages", package_request["native"])
                runtime.install(
                    package_request["native"],
                    directory,
                    inventory,
                    command=guest.command,
                    check=check,
                )
                journal.effect(
                    "native-packages",
                    package_request["native"],
                    state="complete",
                    receipt={"inventory": state.digest(inventory)},
                )
            else:
                runtime.migration.verify_packages(package_request["native"])
                runtime.verify_inventory(inventory)
            names = sorted(set(package_request["wheels"]) - {package_request["product"]}) + [
                package_request["product"]
            ]
            desired = {}
            for name in names:
                package, version = python.wheel_identity(directory / name)
                key = python.dependency_scope.name(package)
                if key in desired:
                    raise RuntimeError("Repair Python wheels have duplicate package identities")
                desired[key] = version

            def check_python() -> None:
                check()
                require_shared_dependencies(desired)

            check_python()
            claimed: set[str] = set()
            for name in names:
                digest = package_request["wheels"][name]
                checkpoint = directory / "python" / digest
                python.prepare(checkpoint, directory / name, python_command, reconstruct=True)
                record = python.load(checkpoint, directory / name)
                paths = set(record["before"]) | set(record["target"])
                if claimed & paths:
                    raise RuntimeError("Repair Python wheels have overlapping ownership")
                claimed.update(paths)
            for name in names:
                digest = package_request["wheels"][name]
                effect_name = "python-" + digest
                journal.effect(effect_name, {"name": name, "sha256": digest})
                python.install(
                    directory / "python" / digest, directory / name, python_command, check_python
                )
                journal.effect(
                    effect_name,
                    {"name": name, "sha256": digest},
                    state="complete",
                    receipt={"sha256": digest},
                )
        # Wheels keep these bytes inside site-packages. Fresh retained-host
        # reconstruction must also publish their canonical system locations;
        # normal HA activation deliberately does not reinstall service assets.
        check()
        payloads = asset_payloads(request, value, directory)
        asset_request = {"artifact": request["package"]["wheels"][request["package"]["product"]]}
        journal.effect("service-assets", asset_request)
        for path, raw, mode in payloads:
            check()
            guest.publish_file(path, raw, mode=mode)
        guest.command(["systemctl", "daemon-reload"])
        check()
        verify_assets(request, value, directory)
        journal.effect("service-assets", asset_request, state="complete", receipt=asset_request)
        check()
        if value["phase"] != "installed":
            journal.advance("installed")
        return {
            "operation": operation,
            "phase": "installed",
            "boot": guest.BOOT.read_text().strip(),
            "package": state.digest(request["package"]),
        }


def python_command(args: list[str], **kwargs) -> tuple[int, str]:
    """Use the rebuilt distro pip even if the previous local pip is broken."""
    if args[:4] == [sys.executable, "-B", "-m", "pip"]:
        args = [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "-c",
            "import sys,runpy;sys.path.append('/usr/lib/python3/dist-packages');runpy.run_module('pip',run_name='__main__')",
            *args[4:],
        ]
    return guest.command(args, **kwargs)


def main(*, durable: bool = False) -> None:
    if not durable or not worker.ACTIVE:
        raise RuntimeError("Repair package execution requires its durable worker")
    reply(execute(receive()))


def inspect() -> dict:
    """No imports of installed product/dependency code and no old journal reads."""
    dependencies, requirements = {}, {}
    for distribution in importlib.metadata.distributions():
        try:
            name = distribution.metadata["Name"]
            version = distribution.version
            required = distribution.requires or []
        except (ValueError, TypeError, KeyError, AttributeError, OSError):
            # Broken metadata cannot be used as a version pin. Exact installed
            # path ownership is checked independently at replacement time.
            continue
        if not isinstance(name, str) or not name:
            continue
        normalized = re.sub(r"[-_.]+", "-", name).lower()
        dependencies[normalized] = version
        requirements[normalized] = required
    return {
        "boot": guest.BOOT.read_text().strip(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "implementation": sys.implementation.name,
        "soabi": sysconfig.get_config_var("SOABI"),
        "libc": list(platform.libc_ver()),
        "dependencies": dependencies,
        "requirements": requirements,
        "runtime_plan": runtime.migration.plan_current(reinstall=True),
    }


def admit(request: dict) -> dict:
    operation, intent = request["operation"], request["intent"]
    if not re.fullmatch(r"[a-f0-9]{32}", operation):
        raise RuntimeError("Repair operation identity is invalid")
    state.validate_intent(intent)
    journal = state.Journal(guest.ROOT)
    previous = journal.load()
    if previous is not None and previous["id"] == operation:
        if previous["intent"] != intent:
            raise RuntimeError("Confirmed repair intent changed")
        return {"operation": operation, "phase": previous["phase"]}
    if previous is not None and previous["phase"] != "complete":
        settled = json.loads(state.read(guest.ROOT / "settled.json") or b"null")
        if (
            not settled
            or settled.get("predecessor") != state.digest(previous)
            or previous["intent"]["targets"] != intent["targets"]
        ):
            raise RuntimeError("Prior repair effects must settle before guest succession")
        guest.verify_guard(previous["id"])
        guest.verify_quiescent()
    prior = None
    if previous:
        policy = json.loads(state.read(guest.ROOT / "package-policy.json") or b"null")
        prior = state.digest(previous)
        state.publish(guest.ROOT / "history" / (previous["id"] + ".json"), previous)
        for name in ("package-policy.json", "reset.json", "startup.json"):
            path = guest.ROOT / name
            if path.exists():
                archive = guest.ROOT / "archives" / previous["id"] / ("repair-" + name)
                guest.archive_directory(archive.parent)
                if archive.exists():
                    if archive.read_bytes() != path.read_bytes():
                        raise RuntimeError("Repair succession archive changed")
                    path.unlink()
                else:
                    os.rename(path, archive)
                state.sync(archive.parent)
                state.sync(guest.ROOT)
        # An interrupted repair may still own policy-rc.d. Carry the exact
        # original policy forward; do not adopt our own sentinel as the original.
        if policy and guest.POLICY.exists() and guest.POLICY.read_bytes() == guest.POLICY_BYTES:
            state.publish(
                guest.ROOT / "package-policy.json",
                {"operation": operation, "original": policy["original"]},
            )
    state.publish(
        journal.path,
        {
            "schema": state.SCHEMA,
            "id": operation,
            "intent": intent,
            "phase": "confirmed",
            "effects": {},
            "previous": prior,
        },
    )
    return {"operation": operation, "phase": "confirmed"}


def stage(request: dict) -> dict:
    journal = state.Journal(guest.ROOT)
    value = journal.load()
    if (
        value is None
        or value["id"] != request["operation"]
        or value["phase"] not in {"confirmed", "isolated", "staged"}
    ):
        raise RuntimeError("Repair staging has no confirmed operation")
    package = request["package"]
    if (
        set(package) != {"native", "wheels", "product"}
        or package["wheels"].get(package["product"]) != value["intent"]["artifact"]
    ):
        raise RuntimeError("Repair staging differs from confirmed artifact")
    journal.effect("stage-payload", package)
    directory = guest.ROOT / "payload" / value["id"]
    state.directory(directory)
    for name, expected in package["wheels"].items():
        if (
            Path(name).name != name
            or not name.endswith(".whl")
            or not re.fullmatch(r"[a-f0-9]{64}", expected)
        ):
            raise RuntimeError("Repair wheel filename is invalid")
        path = directory / name
        if path.exists():
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise RuntimeError("Previously staged repair wheel changed")
            continue
        raw = base64.b64decode(request["wheels"][name], validate=True)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise RuntimeError("Repair wheel upload identity changed")
        guest.publish_file(path, raw, mode=0o600)
    native = runtime.migration.stage(package["native"], root=guest.ROOT / "native" / value["id"])
    for name in package["native"]["archives"]:
        path = directory / name
        if not path.exists():
            os.link(native / name, path, follow_symlinks=False)
    state.sync(directory)
    runtime.verify_archives(package["native"], directory)
    state.publish(directory / "package.json", package)
    journal.effect(
        "stage-payload", package, state="complete", receipt={"package": state.digest(package)}
    )
    return {"operation": value["id"], "package": state.digest(package)}


def control(request: dict) -> dict:
    action = request.get("action")
    if action == "inspect":
        return inspect()
    if action in {"worker-start", "worker-status", "worker-find", "worker-receipts"}:
        return worker.control(request)
    if action == "retire-idle-handoffs":
        operation, intent = request["operation"], request["intent"]
        if not re.fullmatch(r"[a-f0-9]{32}", operation):
            raise RuntimeError("Repair operation identity is invalid")
        state.validate_intent(intent)
        targets = {item["name"] for item in intent["targets"]}
        if not intent["ha"] or not isinstance(request["reservations"], list):
            raise RuntimeError("Prior handoff is outside the confirmed repair")
        results = []
        for reservation in request["reservations"]:
            if (reservation["host"] not in targets
                    or reservation["request"]["package"]["host"] != reservation["host"]):
                raise RuntimeError("Prior handoff is outside the confirmed repair")
            results.append(prior_worker.retire_idle_for_repair(reservation, operation))
        return {"operation": operation, "retirements": results}
    with guest.lock(guest.DEPLOY_LOCK):
        if action == "admit":
            return admit(request)
        if action == "stage":
            return stage(request)
        if action == "verify-installed":
            return verify_installation(request["request"])
        journal = state.Journal(guest.ROOT)
        value = journal.load()
        if value is None or value["id"] != request.get("operation"):
            raise RuntimeError("Repair guest action lacks its exact operation")
        operation = value["id"]
        if action == "settle":
            # No new package work is admitted. An existing durable worker must
            # finish or die before the new confirmed release can supersede it.
            for directory in worker.ROOT.iterdir() if worker.ROOT.exists() else ():
                if not directory.is_dir() or directory.is_symlink():
                    raise RuntimeError("Repair worker storage is unsafe")
                stored = worker.read(directory / "request.json")
                if stored.get("operation") == operation:
                    _path, transport = worker.record(directory.name)
                    if worker.unit_running(transport):
                        raise RuntimeError("Prior repair package worker is still settling")
            with native_exclusion():
                # Preserve installed units; only revoke grants and fence writers.
                guest.install_guard(operation, preserve_services=True)
                state.publish(
                    guest.ROOT / "startup.json",
                    {"operation": operation, "boot": guest.BOOT.read_text().strip(), "units": []},
                )
                guest.fence()
                guest.stop_writers()
                guest.verify_guard(operation)
                guest.inhibit_package_starts(operation)
                receipt = {
                    "boot": guest.BOOT.read_text().strip(),
                    "guard": hashlib.sha256(guest.GUARD).hexdigest(),
                }
                settled = journal.settle_pending(receipt)
                state.publish(
                    guest.ROOT / "settled.json", {**receipt, "predecessor": state.digest(settled)}
                )
                return {"operation": operation, **receipt, "predecessor": state.digest(settled)}
        if action == "guard":
            if value["phase"] != "confirmed":
                raise RuntimeError("Repair guard requires its confirmed preparation phase")
            guest.install_guard(operation)
            guest.inhibit_package_starts(operation)
            guest.stop_writers()
            guest.verify_guard(operation)
            return {
                "operation": operation,
                "boot": guest.BOOT.read_text().strip(),
                "guard": hashlib.sha256(guest.GUARD).hexdigest(),
            }
        if action == "isolated":
            guest.verify_guard(operation)
            guest.verify_quiescent()
            if value["phase"] == "confirmed":
                journal.advance("isolated")
            if journal.load()["phase"] == "isolated":
                journal.advance("staged")
            return {
                "operation": operation,
                "phase": journal.load()["phase"],
                "boot": guest.BOOT.read_text().strip(),
            }
        if action in {"prepare-activation", "grant"}:
            if value["phase"] not in {
                "installed",
                "enrolled",
                "prepared",
                "originating",
                "verified",
            }:
                raise RuntimeError("Repair startup requires verified installation")
            guest.verify_guard(operation)
            if action == "prepare-activation":
                # Finish the repair worker's policy ownership before the next
                # owner inspects it. Service startup remains independently gated.
                guest.restore_package_policy(operation)
                return {"operation": operation, "boot": guest.BOOT.read_text().strip()}
            units = request["units"]
            if not isinstance(units, list) or not set(units).issubset(guest.UNITS):
                raise RuntimeError("Repair startup grant exceeds its service footprint")
            state.publish(
                guest.ROOT / "startup.json",
                {"operation": operation, "boot": guest.BOOT.read_text().strip(), "units": units},
            )
            return {"operation": operation, "boot": guest.BOOT.read_text().strip(), "units": units}
        if action == "advance":
            value = journal.advance(request["phase"])
            return {"operation": operation, "phase": value["phase"]}
        if action == "finish":
            if value["phase"] != "complete":
                raise RuntimeError("Repair cleanup requires verified completion")
            guest.finish(operation)
            return {"operation": operation, "phase": "complete"}
        raise RuntimeError("Unknown repair guest action")


def control_main() -> None:
    raw = sys.stdin.buffer.read(worker.LIMIT + 1)
    request: dict = {}
    try:
        if len(raw) > worker.LIMIT:
            raise RuntimeError("Repair request exceeds its limit")
        request = json.loads(raw)
        result = control(request)
    except Exception as error:
        # Child/package stderr and config values never cross the public boundary.
        reasons = {
            "Signed Ubuntu repository metadata is unavailable": "repair_repository_unavailable",
            "An existing guest deployment writer is still settling": "repair_writer_busy",
            "Repair guest command did not complete": "repair_native_command",
            "Effective repair service inhibition changed": "repair_service_inhibition",
            "Independent repair boot protection is not persistent": "repair_boot_persistence",
            "Independent repair boot protection changed": "repair_boot_protection",
            "Repair service original changed during guard installation": "repair_service_original",
            "A product service is still settling": "package_quiescence_unproven",
            "A product service descendant is still running": "package_quiescence_unproven",
            "Prior handoff still has executing children": "package_quiescence_unproven",
            "Prior handoff is frozen by another operation": "repair_handoff_foreign_freeze",
            "Prior handoff reservation differs from original authority": "repair_handoff_authority",
            "Prior handoff bootstrap differs from original authority": "repair_handoff_authority",
            "Prior handoff retirement authority changed": "repair_handoff_authority",
            "Prior handoff process identity changed": "repair_handoff_authority",
            "Prior handoff retirement process changed": "repair_handoff_authority",
            "Prior handoff retirement evidence changed": "repair_handoff_authority",
            "Prior handoff is outside the confirmed repair": "repair_handoff_authority",
        }
        action = request.get("action") if isinstance(request, dict) else None
        if not isinstance(action, str):
            action = ""
        stage = {
            "admit": "predecessor",
            "retire-idle-handoffs": "predecessor",
            "inspect": "inspect",
            "stage": "stage",
            "guard": "quiesce",
            "settle": "quiesce",
            "verify-installed": "verify",
            "grant": "activate",
            "prepare-activation": "activate",
            "finish": "verify",
            "advance": "verify",
        }.get(action, "install")
        print(
            json.dumps(
                {
                    "status": "failed",
                    "stage": stage,
                    "error_code": error.code
                    if isinstance(error, python.dependency_scope.DependencyFailure)
                    else reasons.get(str(error), "repair_handoff_unverified"
                                     if action == "retire-idle-handoffs" else "receipt"),
                }
            )
        )
        raise SystemExit(1) from None
    print(json.dumps(result, sort_keys=True))
