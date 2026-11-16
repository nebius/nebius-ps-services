#!/usr/bin/env python3
"""Run Implement's lightweight, local-only verification profile."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any



# BEGIN shared runtime bootstrap
def _load_skill_support(group, anchor_file, declared_path, *, source_only=False):
    import hashlib as _hashlib
    import os as _os
    from pathlib import Path as _Path
    import stat as _stat
    import sys as _sys
    from types import ModuleType as _ModuleType

    def read_source(path):
        path = _Path(_os.path.abspath(path))
        for part in (*reversed(path.parents), path):
            metadata = part.lstat()
            if _stat.S_ISLNK(metadata.st_mode):
                if metadata.st_uid != 0 or part == path:
                    raise ImportError("shared runtime path contains an unsafe symlink")
                metadata = part.stat()
            if part != path:
                sticky = metadata.st_uid == 0 and metadata.st_mode & _stat.S_ISVTX
                if (not _stat.S_ISDIR(metadata.st_mode) or metadata.st_uid not in {0, _os.getuid()}
                        or metadata.st_mode & 0o022 and not sticky):
                    raise ImportError("unsafe shared runtime ancestry")
        path = path.resolve(strict=True)
        descriptor = _os.open(path.anchor, _os.O_RDONLY | _os.O_DIRECTORY)
        try:
            for part in path.parts[1:-1]:
                child = _os.open(part, _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW, dir_fd=descriptor)
                _os.close(descriptor)
                descriptor = child
                metadata = _os.fstat(descriptor)
                sticky = metadata.st_uid == 0 and metadata.st_mode & _stat.S_ISVTX
                if metadata.st_uid not in {0, _os.getuid()} or metadata.st_mode & 0o022 and not sticky:
                    raise ImportError("unsafe shared runtime ancestry")
            child = _os.open(path.name, _os.O_RDONLY | _os.O_NOFOLLOW | _os.O_NONBLOCK, dir_fd=descriptor)
            try:
                before = _os.fstat(child)
                if (not _stat.S_ISREG(before.st_mode) or before.st_uid != _os.getuid()
                        or before.st_mode & 0o022 or before.st_nlink != 1 or before.st_size > 1048576):
                    raise ImportError("unsafe shared runtime source")
                data = bytearray()
                while chunk := _os.read(child, min(65536, 1048577 - len(data))):
                    data.extend(chunk)
                    if len(data) > 1048576:
                        raise ImportError("shared runtime source exceeds size limit")
                after = _os.fstat(child)
                bound = _os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
                def identity(value):
                    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid,
                            value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
                if identity(before) != identity(after) or identity(after) != identity(bound):
                    raise ImportError("shared runtime source changed while reading")
                return bytes(data), identity(after)
            finally:
                _os.close(child)
        finally:
            _os.close(descriptor)

    anchor = _Path(_os.path.abspath(anchor_file))
    declared_paths = (declared_path,) if isinstance(declared_path, str) else declared_path
    candidates = []
    for declared in declared_paths:
        relative = _Path(declared)
        if tuple(anchor.parts[-len(relative.parts):]) == relative.parts:
            catalog = anchor.parents[len(relative.parts) - 1]
            candidates.append(("catalog", catalog, catalog / "global-context-management/scripts"))
    if not source_only:
        flat = anchor.parent.parent if anchor.parent.name == "lib" else anchor.parent
        candidates.append(("flat", flat, flat))
        agent = "codex" if _os.environ.get("CODEX_THREAD_ID") else _os.environ.get("SKILLS_AGENT", "codex")
        if agent not in {"codex", "claude"}:
            raise ImportError("SKILLS_AGENT must be codex or claude")
        key, default = ("CODEX_HOME", ".codex") if agent == "codex" else ("CLAUDE_CONFIG_DIR", ".claude")
        home = _Path(_os.environ.get(key, str(_Path.home() / default))).expanduser()
        if not home.is_absolute():
            raise ImportError("shared runtime home must be absolute")
        candidates.append(("flat", home / "hooks", home / "hooks"))
    for kind, root, support in candidates:
        loader_path = support / "trusted_runtime.py"
        if not loader_path.exists() and not loader_path.is_symlink():
            if ((kind == "catalog" and (support.exists() or support.is_symlink()))
                    or any((support / name).exists() or (support / name).is_symlink()
                           for name in ("agent_runtime.py", "hook_runtime.py", "task_state_permissions.py"))):
                raise ImportError("incomplete shared runtime bundle; reinstall current support")
            continue
        data, identity = read_source(loader_path)
        digest = _hashlib.sha256(data).hexdigest()
        cache_name = "_skills_trusted_runtime"
        loader = _sys.modules.get(cache_name)
        provenance = (str(loader_path), identity, digest)
        if cache_name in _sys.modules:
            if (type(loader) is not _ModuleType or getattr(loader, "_bootstrap_provenance", None) != provenance):
                raise ImportError("conflicting shared runtime loader")
        else:
            loader = _ModuleType(cache_name)
            loader.__file__ = str(loader_path)
            exec(compile(data, str(loader_path), "exec"), loader.__dict__)
            loader._bootstrap_provenance = provenance
            loader._read_source = read_source
            _sys.modules[cache_name] = loader
        return loader.load_support(group, anchor=(kind, root), source_only=source_only)
    raise ImportError("Shared skill runtime unavailable; install the complete current skill support")
# END shared runtime bootstrap
_load_skill_support('runtime', __file__, 'implement-test/scripts/verify_implement.py')

from implement_reporting import build_report, write_private  # noqa: E402 — verified bootstrap precedes runtime imports
from implement_lifecycle import _ensure_root  # noqa: E402 — verified bootstrap precedes runtime imports


from agent_runtime import agent_home, installed_skills_dir  # noqa: E402


TEST_SCRIPTS = (
    "implement/scripts/test_implement_contract.py",
    "worktree/scripts/test_workflow_objectives.py",
    "worktree/scripts/test_workflow_projects.py",
    "worktree/scripts/test_workflow_automatic.py",
    "worktree/scripts/test_workflow_contracts.py",
    "worktree/scripts/test_workflow_execution.py",
    "worktree/scripts/test_workflow_dispatch.py",
    "worktree/scripts/test_workflow_security.py",
    "worktree/scripts/test_workflow_readiness.py",
    "worktree/scripts/test_workflow_statistics.py",
    "worktree/scripts/test_workflow_overhead.py",
    "worktree/scripts/test_workflow_process.py",
    "worktree/scripts/test_workflow_native_stream.py",
    "worktree/scripts/test_workflow_verification.py",
    "worktree/scripts/test_workflow_implement_statistics.py",
    "worktree/scripts/test-worktree-manager.py",
)
HARNESS_TEST_SCRIPTS = (
    "test_app_objective.py",
    "test_native_app_server.py",
    "test_public_workflow_trial.py",
    "test_collect_live_evidence.py",
    "test_implement_lifecycle.py",
    "test_implement_reporting.py",
    "test_implement_statistics.py",
    "test_implement_browser.py",
    "test_implement_catalog.py",
    "test_implement_semantics.py",
    "test_implement_test_contract.py",
    "test_verify_implement.py",
)
IGNORED_NAMES = {"__pycache__", ".install-source-id"}
OBSERVATION_INTERVAL_SECONDS = 30


def default_private_root() -> Path:
    return agent_home() / "implement-test"


def _files(root: Path) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in IGNORED_NAMES for part in path.parts):
            continue
        found[path.relative_to(root).as_posix()] = path.read_bytes()
    return found


def parity(source: Path, installed: Path) -> tuple[str, str]:
    if not installed.is_dir():
        return "PARTIAL", "installed Implement was not found"
    return (
        ("PASS", "source and installed Implement copies match")
        if _files(source) == _files(installed)
        else ("FAIL", "source and installed Implement copies differ")
    )


def dependency_parity(source: Path, installed: Path) -> tuple[str, str]:
    if not source.is_dir():
        return "FAIL", "worktree source was not found"
    if not installed.is_dir():
        return "PARTIAL", "installed worktree was not found"
    return (
        ("PASS", "source and installed worktree copies match")
        if _files(source) == _files(installed)
        else (
            "FAIL",
            "source and installed worktree copies differ",
        )
    )


def contract(source: Path) -> tuple[str, str]:
    skill = (source / "SKILL.md").read_text(encoding="utf-8")
    metadata = (source / "agents" / "openai.yaml").read_text(encoding="utf-8")
    required_commands = (
        "$implement", "$implement <implementation request>",
        "$implement continue [project-folder]", "$implement status [project-folder]",
        "$implement -h", "$implement --help",
    )
    frontmatter = re.match(r"\A---\n(?P<body>.*?)\n---\n", skill, re.DOTALL)
    if source.name != "implement" or frontmatter is None:
        return "FAIL", "the source folder or frontmatter is invalid"
    name = re.search(r"(?m)^name:\s*([^\s]+)\s*$", frontmatter.group("body"))
    if name is None or name.group(1) != "implement":
        return "FAIL", "the source folder and frontmatter name do not match"
    observed_commands = tuple(re.findall(r"(?m)^\$implement[^\n]*$", skill))
    if observed_commands != required_commands:
        return "FAIL", "the public interface is not exactly the direct-objective actions"
    policy_block = re.search(
        r"(?m)^policy:\s*(?:#.*)?\n(?P<body>(?:(?:[ \t]+[^\n]*|[ \t]*)\n?)*)",
        metadata,
    )
    implicit_policy = (
        []
        if policy_block is None
        else re.findall(
            r"(?m)^[ \t]+allow_implicit_invocation:\s*(true|false)\s*(?:#.*)?$",
            policy_block.group("body"),
        )
    )
    if implicit_policy != ["false"]:
        return "FAIL", "Implement is not explicit-only"
    if any(
        f"$implement {name}" in skill
        for name in ("parallel", "merge", "cleanup", "upgrade")
    ):
        return "FAIL", "an unsupported public compatibility command is present"
    return (
        "PASS",
        "explicit-only metadata and the exact direct-objective surface are present",
    )


def scheduler():
    path = Path(__file__).resolve().parents[2] / "worktree/scripts/workflow_verification.py"
    previous = sys.modules.get("workflow_verification")
    if previous is not None:
        if Path(getattr(previous, "__file__", "")).resolve() != path.resolve():
            raise ImportError("conflicting verification scheduler")
        return previous
    spec = importlib.util.spec_from_file_location("workflow_verification", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_profile(source, harness=None, observation_interval_seconds=OBSERVATION_INTERVAL_SECONDS, *, on_result=None):
    owner = scheduler()
    commands = [(name, [sys.executable, str(source.parent / name)], source.parent) for name in TEST_SCRIPTS]
    missing = [name for name in TEST_SCRIPTS if not (source.parent / name).is_file()]
    if harness is not None:
        scripts = harness / "scripts"
        missing += [name for name in HARNESS_TEST_SCRIPTS if not (scripts / name).is_file()]
        commands.append(("implement-test helper suites", [sys.executable, "-m", "unittest", "discover",
                         "-s", str(scripts), "-p", "test_*.py"], harness.parent))
    if missing:
        return [{"name": "test inventory", "status": "FAIL", "detail": "required test script is missing"}], []
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHON_COLORS": "0"}
    groups = owner.groups_for_commands(commands, source.parent)
    rows = owner.schedule(groups, environment, on_result=on_result, observation_interval_seconds=observation_interval_seconds,
                          on_checkpoint=lambda row: print(json.dumps({"verification_progress": row}), flush=True))
    checks = []
    for name, _, _ in commands:
        selected = [row for row in rows if row["suite"] == name]
        states = {row["status"] for row in selected}
        status = "PASS" if states == {"PASS"} else "NOT_RUN" if states == {"NOT_RUN"} else "FAIL"
        checks.append({"name": name, "status": status,
                       "detail": ", ".join(sorted(states)) + "; " + str(sum(len(row["outcomes"]) for row in selected)) + " exact test outcomes"})
    return checks, rows


def verify(
    source: Path,
    installed: Path,
    observation_interval_seconds: int = OBSERVATION_INTERVAL_SECONDS,
    harness: Path | None = None,
    dependency_source: Path | None = None,
    dependency_installed: Path | None = None,
    *, on_result=None,
) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    groups = []
    if not source.is_dir():
        checks.append(
            {
                "name": "source",
                "status": "FAIL",
                "detail": "Implement source is missing",
            }
        )
    else:
        status, detail = contract(source)
        checks.append({"name": "public contract", "status": status, "detail": detail})
        status, detail = parity(source, installed)
        checks.append({"name": "installed parity", "status": status, "detail": detail})
        if dependency_source is not None and dependency_installed is not None:
            status, detail = dependency_parity(dependency_source, dependency_installed)
            checks.append(
                {
                    "name": "worktree parity",
                    "status": status,
                    "detail": detail,
                }
            )
        if not any(check["status"] == "FAIL" for check in checks):
            executed, groups = run_profile(source, harness, observation_interval_seconds, on_result=on_result)
            checks.extend(executed)
        else:
            groups = []
            checks.extend({"name": name, "status": "NOT_RUN", "detail": "prerequisite failure stopped admission"}
                          for name in TEST_SCRIPTS)
            if harness is not None:
                checks.append({"name": "implement-test helper suites", "status": "NOT_RUN", "detail": "prerequisite failure stopped admission"})
    deterministic = (
        "FAIL" if any(item["status"] == "FAIL" for item in checks) else "PASS"
    )
    if deterministic == "PASS" and any(item["status"] in {"PARTIAL", "NOT_RUN"} for item in checks):
        deterministic = "PARTIAL"
    return {
        "test_groups": groups,
        "mode": "lightweight",
        "overall": "FAIL" if deterministic == "FAIL" else "PARTIAL",
        "deterministic": deterministic,
        "live": "NOT_RUN",
        "lifecycle": "UNCHANGED",
        "stages": [
            {
                "id": re.sub(r"[^a-z0-9]+", "-", check["name"].lower()).strip("-"),
                **check,
            }
            for check in checks
        ]
        + [
            {
                "id": "live-application",
                "name": "live application",
                "status": "NOT_RUN",
                "detail": "no real application, Docker runtime, or workers were started",
            }
        ],
        "next_action": (
            "Resolve the first deterministic failure and rerun $implement-test."
            if deterministic == "FAIL"
            else "Run $implement-test --create only when live disposable verification is required."
        ),
    }


def main() -> int:
    skill_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root", type=Path, default=skill_root.parent / "implement"
    )
    parser.add_argument(
        "--installed-root",
        type=Path,
        default=installed_skills_dir() / "implement",
    )
    parser.add_argument(
        "--worktree-source-root",
        type=Path,
        default=skill_root.parent / "worktree",
    )
    parser.add_argument(
        "--worktree-installed-root",
        type=Path,
        default=installed_skills_dir() / "worktree",
    )
    parser.add_argument(
        "--report", type=Path, default=default_private_root() / "report.md"
    )
    parser.add_argument("--observation-interval", type=int, default=OBSERVATION_INTERVAL_SECONDS)
    args = parser.parse_args()
    if not 1 <= args.observation_interval <= 300:
        parser.error("--observation-interval must be between 1 and 300 seconds")
    import implement_catalog as catalog
    report = args.report.expanduser().absolute()
    retained_groups = {}
    def retain_group(row):
        retained_groups[row["id"]] = row
        write_private(report.with_suffix(".inventory.json"), json.dumps(
            {"schema": "workflow/verification-observations-v1", "groups": list(retained_groups.values())},
            sort_keys=True, indent=2) + "\n")
    try:
        frozen = catalog.snapshot()
        source_files = catalog.snapshot(args.source_root.resolve().parent)["files"]
        installed_files = catalog.snapshot(args.installed_root.expanduser().resolve().parent)["files"]
        if source_files != installed_files or frozen["files"] != installed_files:
            raise RuntimeError("catalog parity differs")
        summary = verify(
            args.source_root.resolve(), args.installed_root.expanduser().resolve(),
            args.observation_interval, skill_root, args.worktree_source_root.resolve(),
            args.worktree_installed_root.expanduser().resolve(),
            on_result=retain_group,
        )
        catalog.validate_record(frozen)
        if (catalog.snapshot(args.source_root.resolve().parent)["files"] != source_files
                or catalog.snapshot(args.installed_root.expanduser().resolve().parent)["files"] != installed_files):
            raise RuntimeError("source or installed catalog changed during preflight")
    except (OSError, ValueError, RuntimeError) as error:
        summary = {"mode": "lightweight", "overall": "FAIL", "deterministic": "FAIL",
                   "live": "NOT_RUN", "lifecycle": "UNCHANGED",
                   "stages": [{"id": "catalog-integrity", "name": "catalog integrity", "status": "FAIL",
                               "detail": "Source, verifier or installed catalog is missing, unsafe or changed (" + type(error).__name__ + ")."}],
                   "next_action": "Restore matching source/install payloads and rerun $implement-test."}
    summary["report_path"] = str(report.resolve())
    if report.parent == default_private_root().expanduser().absolute():
        _ensure_root(report.parent)
    groups = summary.pop("test_groups", list(retained_groups.values()))
    verification = {"schema": "workflow/verification-observations-v1", "groups": groups}
    write_private(report.with_suffix(".inventory.json"), json.dumps(verification, sort_keys=True, indent=2) + "\n")
    write_private(report, build_report(summary))
    if summary["deterministic"] == "PASS":
        catalog.publish_preflight(default_private_root(), frozen, {**summary, "verification": verification})
    print(
        json.dumps(
            {"summary": summary, "report": str(report.resolve())}, sort_keys=True
        )
    )
    return 1 if summary["deterministic"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
