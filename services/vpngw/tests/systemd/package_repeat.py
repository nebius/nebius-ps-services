"""Read-only package convergence observations, separate from full-apply no-op proof."""

from __future__ import annotations

import base64
import csv
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

EXCLUSIONS = [
    "File timestamps and inodes; payload, type, mode, UID/GID and symlink targets remain strict.",
    "Generated .pyc files and their RECORD rows; all other recorded Python payload remains strict.",
    "Guest execution owner/effects and guard installed_at; raw journals and guard are retained.",
    "Execution logs and caches outside package-owned payload inventories.",
]


def generated_bytecode(path: Path) -> bool:
    if path.suffix != ".pyc" or path.is_symlink():
        return False
    try:
        source = Path(importlib.util.source_from_cache(str(path)))
    except ValueError:
        source = path.with_suffix(".py")
    return source.is_file()


def file_identity(path: Path) -> dict:
    info = path.lstat()
    result = {"mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid}
    if stat.S_ISLNK(info.st_mode):
        result.update(kind="symlink", target=os.readlink(path))
    elif stat.S_ISDIR(info.st_mode):
        result["kind"] = "directory"
    else:
        assert stat.S_ISREG(info.st_mode), f"Unexpected package file type: {path}"
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        result.update(kind="file", sha256=digest)
        if path.name == "RECORD" and path.parent.name.endswith(".dist-info"):
            result.pop("sha256")
            with path.open(newline="") as stream:
                result["record_rows"] = sorted(
                    row for row in csv.reader(stream)
                    if not generated_bytecode(path.parent.parent / row[0])
                )
    return result


def native_payload(name: str) -> dict:
    paths = subprocess.check_output(["dpkg-query", "-L", name], text=True, timeout=30)
    files = {
        path: file_identity(Path(path)) for path in paths.splitlines()
        if path.startswith("/") and not generated_bytecode(Path(path))
    }
    assert files, f"Native package has no payload inventory: {name}"
    return files


def native_selection_payload(plan: dict) -> dict:
    # desired includes required packages already installed before preparation.
    return {name: native_payload(name) for name in plan["desired"]}


def system_distribution_roots() -> set[Path]:
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    return {
        Path(prefix) / "lib" / directory / suffix
        for prefix in ("/usr", "/usr/local")
        for directory in ("python3", version)
        for suffix in ("dist-packages", "site-packages")
    }


def tree_payload(root: Path) -> dict:
    """Walk installed payload without following directory symlinks."""
    result = {".": file_identity(root)}
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in [*directories, *files]:
            path = Path(directory) / name
            if generated_bytecode(path):
                continue
            # Generated-cache directory existence is execution metadata; any
            # non-bytecode content inside it is still visited and inventoried.
            if name == "__pycache__" and path.is_dir() and not path.is_symlink():
                continue
            result[str(path.relative_to(root))] = file_identity(path)
    return result


def python_payload() -> dict:
    result = {}
    roots = set()
    precedence = {}
    effective_dependencies = {}
    effective_requirements = {}
    for distribution in importlib.metadata.distributions():
        name = re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower()
        key = name + "==" + distribution.version + "@" + str(distribution.locate_file(""))
        assert key not in result, "Duplicate installed distribution identity"
        root = Path(distribution.locate_file(""))
        assert root.resolve() == root and root in system_distribution_roots(), "Unexpected Python payload root"
        roots.add(root)
        precedence.setdefault(name, []).append({"version": distribution.version, "root": str(root)})
        effective_dependencies.setdefault(name, distribution.version)
        effective_requirements.setdefault(name, distribution.requires or [])
        files = {}
        if distribution.read_text("RECORD") is None:
            # Ubuntu metadata-only and egg SOURCES lists are not RECORD. The
            # complete installed root below supplies their payload evidence.
            result[key] = {
                "inventory": "complete-installed-root", "root": str(root),
                "requires": sorted(distribution.requires or []),
            }
            continue
        assert distribution.files, f"Wheel distribution has no installed file inventory: {name}"
        for entry in distribution.files:
            path = Path(distribution.locate_file(entry))
            if generated_bytecode(path):
                continue
            identity = file_identity(path)
            if entry.hash is not None and identity["kind"] == "file":
                digest = hashlib.new(entry.hash.mode, path.read_bytes()).digest()
                encoded = base64.urlsafe_b64encode(digest).decode().rstrip("=")
                assert encoded == entry.hash.value, f"Installed Python payload differs: {name}"
            files[str(path)] = identity
        assert files, "Empty installed Python payload inventory"
        result[key] = {
            "inventory": "RECORD", "requires": sorted(distribution.requires or []), "files": files,
        }
    assert result, "No installed Python distributions"
    return {
        "distributions": result,
        "roots": {str(root): tree_payload(root) for root in sorted(roots)},
        "precedence": precedence,
        "effective_dependencies": effective_dependencies,
        "effective_requirements": effective_requirements,
    }


def observe(topology: str) -> dict:
    """Run only with installed system Python after the package process exits."""
    from nebius_vpngw import ha_repair as repair
    from nebius_vpngw import ordinary_operations as ops
    from nebius_vpngw import runtime_migration as migration

    assert topology in {"ordinary", "ha"}
    runtime = migration.state()
    assert runtime is not None and runtime["phase"] == "installed"
    migration.verify_packages(runtime["plan"])
    migration.verify_inhibited()
    assert not migration.TOKEN.exists(), "Activation token must remain absent"
    journal = ops.Journal().read()
    assert journal is not None and journal["state"] == "complete"
    assert not ops.alive(journal["owner"])
    assert all(effect["state"] == "settled" for effect in journal["effects"])
    assert all(not ops.alive(effect["writer"]) for effect in journal["effects"])
    assert all(
        ops.process_group_empty(effect["process"])
        for effect in journal["effects"] if effect["kind"] == "process"
    )
    manager = ops.Manager(time.monotonic() + 60)
    assert manager.quiet((*ops.MANAGEMENT, *ops.HA_WRITERS, *ops.DATAPLANE))
    native = native_selection_payload(runtime["plan"])
    assert native, "Native package selection must not be empty"
    staged = migration.verify_staged(runtime["plan"])
    staged_files = {
        str(path.relative_to(staged)): file_identity(path)
        for path in sorted(staged.rglob("*")) if not path.is_dir()
    }
    assert staged_files
    assets = {}
    for name, expected in runtime["manifest"]["assets"].items():
        identity = file_identity(Path(name))
        assert identity == {
            "kind": "file", "sha256": expected["sha256"],
            "mode": expected["mode"], "uid": 0, "gid": 0,
        }, "Installed service asset differs from the selected manifest"
        assets[name] = identity
    policy_paths = [migration.POLICY, migration.ROOT / "inhibited", migration.ROOT / "startup.py"]
    policy_paths.extend(
        Path("/etc/systemd/system") / (unit + ".d") / "20-runtime-admission.conf"
        for unit in migration.UNITS
    )
    audit = subprocess.run(["dpkg", "--audit"], capture_output=True, text=True, check=True)
    assert not audit.stdout.strip()
    subprocess.run(
        ["/usr/bin/python3", "-B", "-m", "pip", "check"],
        check=True, stdout=subprocess.DEVNULL, timeout=60,
    )
    python = python_payload()
    result = {
        "schema": "nebius-vpngw.package-repeat-observation.v1",
        "topology": topology,
        "native": native,
        "native_versions": subprocess.check_output(
            ["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\t${Status}\n"],
            text=True, timeout=30,
        ).splitlines(),
        "python": python["distributions"],
        "python_roots": python["roots"],
        "python_precedence": python["precedence"],
        "effective_dependencies": python["effective_dependencies"],
        "effective_requirements": python["effective_requirements"],
        "assets": assets,
        "runtime": runtime,
        "request": file_identity(Path("/qualification/package-request.json")),
        "selected_manifest": file_identity(Path("/qualification/selected-manifest.json")),
        "staged": staged_files,
        "config": file_identity(Path("/etc/nebius-vpngw/config-resolved.yaml")),
        "boot": ops.BOOT.read_text().strip(),
        "startup_files": {str(path): file_identity(path) for path in policy_paths},
        "unit_policy": migration.unit_policy(),
        "journal_authority": {
            key: journal[key]
            for key in ("schema", "id", "config", "artifact", "predecessor", "purpose", "network")
        },
        "journal": journal,
        "safety": {
            "inhibited": True, "activation_token_absent": True,
            "writers_quiescent": True, "journal_complete": True,
            "effects_settled": True, "dpkg_clean": True, "pip_check": True,
        },
    }
    if topology == "ha":
        expected = json.loads(Path("/qualification/expected-lock.json").read_text())
        repair.exact_lock(expected)
        guard = json.loads((repair.STATE / "guard.json").read_text())
        assert set(guard) == {"schema", "guard_boot_id", "data_plane_mode", "installed_at"}
        assert guard["guard_boot_id"] == result["boot"] and guard["data_plane_mode"] == "blocked"
        assert repair.FORWARDING.read_text().strip() == "0"
        assert not (repair.STATE / "standby-ready.json").exists()
        result.update(
            guard=guard,
            guard_authority={key: value for key, value in guard.items() if key != "installed_at"},
            ha_files={
                name: file_identity(repair.STATE / name)
                for name in ("apply.lock", "controller-checkpoint.json", "checkpoint-conversion.json")
            },
            ha_marker=file_identity(Path("/etc/nebius-vpngw/vm-ha-enabled")),
        )
        result["safety"].update(forwarding_blocked=True, standby_readiness_absent=True)
    return result


def verify_repeat(before: dict, after: dict) -> dict:
    """Exclude only named execution metadata; never normalize authority or payload."""
    assert before.keys() == after.keys(), "Repeat observation shape changed"
    for observation in (before, after):
        assert observation["schema"] == "nebius-vpngw.package-repeat-observation.v1"
        assert observation["topology"] in {"ordinary", "ha"}
        assert observation["runtime"]["phase"] == "installed"
        safety = {
            "inhibited", "activation_token_absent", "writers_quiescent", "journal_complete",
            "effects_settled", "dpkg_clean", "pip_check",
        }
        if observation["topology"] == "ha":
            safety.update({"forwarding_blocked", "standby_readiness_absent"})
            assert observation["guard_authority"] == {
                key: value for key, value in observation["guard"].items() if key != "installed_at"
            }
        assert set(observation["safety"]) == safety
        assert all(value is True for value in observation["safety"].values())
        journal = observation["journal"]
        assert journal["state"] == "complete"
        assert all(effect["state"] == "settled" for effect in journal["effects"])
        assert observation["journal_authority"] == {
            key: journal[key] for key in observation["journal_authority"]
        }
        assert journal["id"] == observation["runtime"]["operation"]
        assert observation["native"] and observation["python"] and observation["staged"]
    ignored = {"journal", "guard"}
    for key in before.keys() - ignored:
        assert before[key] == after[key], f"Package repeat changed {key}"
    return {
        "status": "passed", "kind": "package-convergence-with-retained-recovery",
        "runtime_operation": before["runtime"]["operation"],
        "native_packages": len(before["native"]),
        "python_distributions": len(before["python"]),
        "permitted_execution_changes": EXCLUSIONS,
        "full_apply_noop": "not-qualified-here-required-on-live-vms",
    }


def verify_case_identity(case: str, result: dict) -> None:
    """Bind matrix coverage to the executed result, not merely its folder name."""
    assert result["mode"] in {"static", "bgp"}
    assert result["topology"] in {"ordinary", "ha"}
    assert result["interruption"] in {None, "native-unpack", "product-wheel"}
    expected = f"{result['predecessor']}-{result['mode']}-{result['topology']}"
    if result["interruption"]:
        expected += "-interrupt-" + result["interruption"]
    assert case == expected, "Matrix label differs from executed case"
    assert result["boundary"] == {
        "ordinary": "ordinary-shared-package-components-without-apply-activation",
        "ha": "ha-guarded-package-preparation-without-cloud-activation",
    }[result["topology"]]
