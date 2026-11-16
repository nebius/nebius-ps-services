"""Bind verifier execution to one physical, immutable installed catalog."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import sys

from implement_errors import LifecycleError

SCHEMA = "implement-test/runtime-catalog-v1"
PACKAGES = (
    "implement",
    "implement-test",
    "worktree",
    "maintain-project-specs",
    "global-context-management",
    "merge-pr",
    "sdlc-classify-failure",
    "sdlc-start",
    "sdlc-prepare-execution",
    "sdlc-implement-plan",
    "code-review",
    "align",
    "sdlc-workflow-test",
)
IGNORED = {"__pycache__", ".install-source-id"}
RELATIVE = "evidence/runtime-catalog.json"


def anchor():
    return Path(__file__).resolve().parents[2]


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def safe_bytes(path):
    from implement_lifecycle import _reject_symlink_components

    _reject_symlink_components(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.getuid()
            or before.st_mode & 0o022
            or before.st_size > 8 * 1024 * 1024
        ):
            raise LifecycleError("catalog artifact is unsafe")
        data = bytearray()
        while chunk := os.read(fd, 65536):
            data.extend(chunk)
            if len(data) > 8 * 1024 * 1024:
                raise LifecycleError("catalog artifact exceeds size bound")
        after = os.fstat(fd)
        bound = path.lstat()

        def identity(value):
            return (
                value.st_dev,
                value.st_ino,
                value.st_size,
                value.st_mtime_ns,
                value.st_ctime_ns,
            )

        if identity(before) != identity(after) or identity(after) != identity(bound):
            raise LifecycleError("catalog artifact changed while reading")
        return bytes(data)
    finally:
        os.close(fd)


def snapshot(catalog=None):
    catalog = anchor() if catalog is None else Path(catalog).absolute()
    files = {}
    for name in PACKAGES:
        base = catalog / name
        if not (base / "SKILL.md").is_file():
            raise LifecycleError("required verifier dependency is missing")
        for path in sorted(base.rglob("*")):
            if set(path.parts) & IGNORED:
                continue
            if path.is_symlink():
                raise LifecycleError("catalog contains a symlink")
            if path.is_file():
                files[path.relative_to(catalog).as_posix()] = hashlib.sha256(
                    safe_bytes(path)
                ).hexdigest()
    return {"schema": SCHEMA, "catalog_root": str(catalog), "files": files}


def check_imports():
    """Loaded code must come from the physical owner, never a receipt path."""
    root = anchor()
    for name, module in tuple(sys.modules.items()):
        if (
            name.startswith(("workflow_", "implement_", "three_tier_"))
            or name == "public_workflow_trial"
        ):
            path = getattr(module, "__file__", None)
            if path and not Path(path).resolve().is_relative_to(root):
                raise LifecycleError("mixed verifier catalogs; use a fresh process")


def validate_record(record):
    if (
        not isinstance(record, dict)
        or set(record) != {"schema", "catalog_root", "files"}
        or record.get("catalog_root") != str(anchor())
        or record != snapshot()
    ):
        raise LifecycleError(
            "frozen verifier catalog changed; restore its exact bundle before cleanup"
        )
    check_imports()


def publish_preflight(root, before, summary):
    from implement_lifecycle import _atomic_json, _locked_root

    validate_record(before)
    if summary.get("deterministic") != "PASS":
        raise LifecycleError("only successful preflight can bind a native trial")
    value = {
        "schema": "implement-test/preflight-v2",
        "status": "PASS",
        "catalog": before,
        "stages_sha256": digest(summary["stages"]),
        "verification": summary["verification"],
        "verification_sha256": digest(summary["verification"]),
    }
    validate_preflight(value)
    with _locked_root(root) as owned:
        _atomic_json(owned / "preflight.json", value)
    return value


def preflight(root):
    try:
        value = json.loads(safe_bytes(root / "preflight.json"))
    except (OSError, ValueError) as error:
        raise LifecycleError(
            "current successful deterministic preflight is required"
        ) from error
    validate_preflight(value)
    return value


def validate_preflight(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "status", "catalog", "stages_sha256", "verification", "verification_sha256"}
        or value["schema"] != "implement-test/preflight-v2"
        or value["status"] != "PASS"
        or not isinstance(value["stages_sha256"], str)
        or len(value["stages_sha256"]) != 64
        or any(c not in "0123456789abcdef" for c in value["stages_sha256"])
    ):
        raise LifecycleError("deterministic preflight receipt is invalid")
    verification = value["verification"]
    if (not isinstance(verification, dict)
            or verification.get("schema") != "workflow/verification-observations-v1"
            or digest(verification) != value["verification_sha256"]
            or not isinstance(verification.get("groups"), list) or not verification["groups"]):
        raise LifecycleError("preflight exact inventory binding is invalid")
    for row in verification["groups"]:
        if (not isinstance(row, dict) or row.get("status") != "PASS"
                or not row.get("inventory") or len(row["inventory"]) != len(set(row["inventory"]))
                or set(row.get("outcomes", {})) != set(row["inventory"])
                or any(outcome not in {"passed", "skipped", "expected_failure"} for outcome in row["outcomes"].values())
                or row.get("observation", {}).get("local_group_quiescent") is not True
                or any(row["observation"].get(key, {}).get("complete") is not True for key in ("stdout", "stderr"))):
            raise LifecycleError("preflight group is incomplete or cleanup is unproven")
    validate_record(value["catalog"])
    return value


def freeze(root, run_path, state, verified):
    from implement_lifecycle import _atomic_json

    validate_record(verified["catalog"])
    path = run_path / RELATIVE
    if path.exists():
        raise LifecycleError("runtime catalog is already frozen")
    _atomic_json(path, verified)
    state["runtime_catalog"] = {
        "path": RELATIVE,
        "sha256": hashlib.sha256(safe_bytes(path)).hexdigest(),
    }


def validate(state, run_path, *, native=False):
    binding = state.get("runtime_catalog")
    if (
        not isinstance(binding, dict)
        or set(binding) != {"path", "sha256"}
        or binding["path"] != RELATIVE
    ):
        raise LifecycleError("frozen verifier catalog binding is missing")
    data = safe_bytes(run_path / RELATIVE)
    if hashlib.sha256(data).hexdigest() != binding["sha256"]:
        raise LifecycleError("frozen verifier catalog receipt changed")
    value = json.loads(data)
    validate_preflight(value)
    if native:
        from agent_runtime import installed_skills_dir

        if installed_skills_dir().resolve() != anchor():
            raise LifecycleError(
                "native verification must use the installed catalog owner"
            )
    return binding["sha256"]


def hook_inputs(home, host):
    """Use the installer's canonical host projection for the required owners."""
    loader = sys.modules["_skills_trusted_runtime"]
    projector = loader.load_support(
        "projector", anchor=("catalog", anchor()), source_only=True
    )["hook_runtime"]
    files, hooks = {}, {}
    for package in ("maintain-project-specs", "sdlc-start"):
        base = anchor() / package / "assets/hooks"
        for path in sorted(base.rglob("*")):
            relative = path.relative_to(base)
            if any(p.startswith(".") or p in {"__pycache__"} for p in relative.parts):
                continue
            if path.is_file() and path.suffix in {".py", ".json"}:
                data = safe_bytes(path)
                key = str(relative)
                if key in files and files[key] != data:
                    raise LifecycleError("required hook sources conflict")
                files[key] = data
        manifest = json.loads(safe_bytes(base.parent / "hooks.json.template"))
        projected = projector.project_hook_entries(manifest["hooks"], home, host)
        for event, entries in projected.items():
            hooks.setdefault(event, []).extend(
                entry for entry in entries if entry not in hooks.get(event, [])
            )
    for name in loader.SUPPORT_FILES:
        files[name] = safe_bytes(anchor() / "global-context-management/scripts" / name)
    return files, hooks


def runtime_hooks(home, host):
    import tomllib

    files, expected = hook_inputs(home, host)
    for path in (home / "hooks").rglob("*"):
        if path.is_symlink():
            raise LifecycleError("runtime hooks contain a symlink")
        relative = path.relative_to(home / "hooks")
        if (
            "__pycache__" not in relative.parts
            and path.is_file()
            and str(relative) not in files
        ):
            raise LifecycleError("runtime hooks contain an unexpected payload")
    for relative, data in files.items():
        if safe_bytes(home / "hooks" / relative) != data:
            raise LifecycleError("installed runtime hook differs from frozen source")
    settings_path = home / ("hooks.json" if host == "codex" else "settings.json")
    settings = json.loads(safe_bytes(settings_path))
    observed = settings.get("hooks") if isinstance(settings, dict) else None
    if not isinstance(observed, dict) or any(
        not isinstance(entries, list) for entries in observed.values()
    ):
        raise LifecycleError("runtime hook registration shape is invalid")
    # The installer retains empty retired-event lists; they execute no handlers.
    observed = {event: entries for event, entries in observed.items() if entries}
    expected = {event: entries for event, entries in expected.items() if entries}
    if observed != expected or settings.get("disableAllHooks"):
        raise LifecycleError(
            "runtime hook registrations differ from the required owner projection"
        )
    if host == "codex":
        config = tomllib.loads(safe_bytes(home / "config.toml").decode())
        if config.get("features", {}).get("hooks") is not True:
            raise LifecycleError("native hooks must remain enabled")
    return {
        "files": {
            name: hashlib.sha256(data).hexdigest() for name, data in files.items()
        },
        "registrations_sha256": digest(expected),
    }


def validate_runtime_hooks(state, run_path, *, required=False):
    if "runtime_hooks" not in state:
        native_receipts = (
            "public-trial-reservation.json",
            "initial-invocation.json",
            "public-invocations.json",
        )
        if required or any(
            (run_path / "evidence" / name).exists() for name in native_receipts
        ):
            raise LifecycleError("required frozen native hook binding is missing")
        return
    from agent_runtime import agent_name

    host = agent_name()
    if runtime_hooks(run_path / (host + "-home"), host) != state["runtime_hooks"]:
        raise LifecycleError("frozen native hook binding changed")
