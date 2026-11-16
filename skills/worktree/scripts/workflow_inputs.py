"""Committed input declarations and automatic admission; never copy source dirt."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from workflow_state import WorkflowError, read, write

SCHEMA = "workflow/input-dependencies-v1"


def fingerprint(value: dict) -> str:
    # Execution bases use the spec owner's compact canonical JSON convention.
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def validate(value: dict, base: str | None = None) -> dict:
    if (set(value) != {"schema", "base_head", "complete", "claims"}
            or value.get("schema") != SCHEMA or value.get("complete") is not True
            or not isinstance(value.get("base_head"), str)
            or re.fullmatch(r"[0-9a-f]{40,64}", value["base_head"]) is None
            or base is not None and value["base_head"] != base
            or not isinstance(value.get("claims"), list)):
        raise WorkflowError("UNCOMMITTED_INPUT", "complete input dependencies must match the captured source commit")
    for claim in value["claims"]:
        if (not isinstance(claim, dict) or set(claim) != {"kind", "path"}
                or claim["kind"] not in {"exact", "prefix"}
                or not isinstance(claim["path"], str) or not claim["path"]):
            raise WorkflowError("UNCOMMITTED_INPUT", "invalid input dependency claim")
        path = PurePosixPath(claim["path"])
        if (path.is_absolute() or ".." in path.parts or "\\" in claim["path"]
                or path.as_posix() != claim["path"]
                or claim["path"] == "." and claim["kind"] != "prefix"):
            raise WorkflowError("UNCOMMITTED_INPUT", "input paths must be canonical repository-relative claims")
    return value


def archive(run_dir: Path, value: dict) -> str:
    validate(value)
    key = fingerprint(value)
    path = run_dir / "input-declarations" / (key + ".json")
    try:
        write(path, value, exclusive=True)
    except FileExistsError:
        if read(path) != value:
            raise WorkflowError("UNCOMMITTED_INPUT", "immutable input declaration changed")
    return key


def frozen(run_dir: Path, key: str) -> dict:
    if not isinstance(key, str) or re.fullmatch(r"[0-9a-f]{64}", key) is None:
        raise WorkflowError("UNCOMMITTED_INPUT", "invalid input declaration digest")
    path = run_dir / "input-declarations" / (key + ".json")
    if not path.exists():
        raise WorkflowError("WORKFLOW_UPGRADE_REQUIRED", "frozen input declaration is unavailable; preserve this execution")
    value = validate(read(path))
    if fingerprint(value) != key:
        raise WorkflowError("UNCOMMITTED_INPUT", "frozen input declaration digest changed")
    return value


def current_digest(run_dir: Path) -> str | None:
    path = run_dir / "input-dependencies.json"
    return archive(run_dir, read(path)) if path.exists() else None


def verify_basis(run_dir: Path, basis: dict) -> None:
    key = basis.get("input_dependencies_sha256")
    if key is not None:
        frozen(run_dir, key)


def declare(run_dir: Path, value: dict) -> None:
    """Called only by bound intake, before allocation or through fenced steering."""
    from workflow_objectives import validate_run, _writers_quiescent, TERMINAL
    import workflow_projects as projects
    import worktree_manager as manager
    run, _ = validate_run(run_dir)
    if run["status"] in TERMINAL:
        raise WorkflowError("UNCOMMITTED_INPUT", "completed runs cannot replace input declarations")
    row = projects.get(run_dir)
    outer = row.get("outer") if row else None
    base = run["source_head"] if outer else manager._git(Path(run["git_root"]), "rev-parse", "HEAD")
    validate(value, base)
    path = run_dir / "input-dependencies.json"
    if path.exists() and read(path) == value:
        archive(run_dir, value)
        return
    if outer:
        previous = read(path)["claims"] if path.exists() else []
        added = [claim for claim in value["claims"] if claim not in previous]
        if any(overlaps_dirty(p, claim) for p in dirty_inputs(Path(row["source_root"]), added, base=base) for claim in added):
            raise WorkflowError("UNCOMMITTED_INPUT", "new shared inputs overlap uncommitted original-source changes")
    if any((run_dir / "bases").glob("*/*.json")):
        if not run.get("fence"):
            raise WorkflowError("REPLAN_REQUIRED", "frozen input changes require accepted steering and replanning")
        _writers_quiescent(run_dir)
    archive(run_dir, value)
    write(path, value)


def matches(path: str, claim: dict) -> bool:
    target = claim["path"]
    return path == target or claim["kind"] == "prefix" and (target == "." or path.startswith(target + "/"))


def overlaps_dirty(path: str, claim: dict) -> bool:
    # Git reports dirty submodule contents at the gitlink, not each nested file.
    # Keep this overlap rule separate from directional ownership containment.
    return matches(path, claim) or claim["path"].startswith(path + "/")


def implicit_inputs(scope: str) -> list[str]:
    project = PurePosixPath(scope)
    paths = {str(project / "docs" / (name + ".md")) for name in ("requirements", "design")}
    for parent in (project, *project.parents):
        for name in ("AGENTS.md", "AGENTS.override.md", "CLAUDE.md", "CLAUDE.local.md",
                     ".claude/CLAUDE.md", ".gitignore", ".gitattributes", ".codex/project-specs.json"):
            paths.add(str(parent / name))
    return sorted(paths)


def dirty_inputs(repo: Path, explicit: list[dict], *, base: str | None = None) -> set[str]:
    """Include ignored untracked files only when they are explicit inputs."""
    import worktree_manager as manager
    if explicit:
        # The execution snapshot does not initialize submodules. Neither its
        # committed tree nor the current index may hide declared input content
        # behind a gitlink, including ignored files invisible to parent status.
        for args in (("ls-tree", "-r", "-z", base or "HEAD"), ("ls-files", "--stage", "-z")):
            for entry in manager._git_bytes(repo, *args).split(b"\0"):
                if entry.startswith(b"160000 "):
                    path = entry.split(b"\t", 1)[1].decode("utf-8", "surrogateescape")
                    if any(overlaps_dirty(path, claim) for claim in explicit):
                        raise WorkflowError("UNSUPPORTED_SUBMODULE_INPUT", "declared inputs cross a gitlink without supported committed submodule snapshots")
    dirty = set(manager.status_paths(repo, include_submodule_dirt=True))
    if explicit:
        paths = sorted({claim["path"] for claim in explicit})
        dirty.update(p for p in manager._git_bytes(
            repo, "ls-files", "--others", "-z", "--", *(":(literal)" + p for p in paths))
            .decode("utf-8", "surrogateescape").split("\0") if p)
    return dirty


def check_source(repo: Path, scope: str, declaration: dict | None, *,
                 base: str | None = None, writes: list[dict] = (), protect_project: bool = True) -> None:
    implicit = implicit_inputs(scope) if protect_project else []
    if declaration is not None:
        validate(declaration, base)
    claims = (declaration["claims"] if declaration else []) + list(writes) + [{"kind": "exact", "path": p} for p in implicit]
    dirty = dirty_inputs(repo, claims, base=base)
    if not dirty:
        return
    if declaration is None:
        raise WorkflowError("UNCOMMITTED_INPUT", "declare complete committed input dependencies before excluding unrelated dirty content")
    if protect_project:
        claims.append({"kind": "prefix", "path": scope})
    if any(overlaps_dirty(path, claim) for path in dirty for claim in claims):
        raise WorkflowError("UNCOMMITTED_INPUT", "selected project, effective instructions, shared inputs or write claims contain uncommitted changes")


def admit(run_dir: Path, primary: Path, scope: str, branch: str, base: str) -> str | None:
    """Validate the private automatic capability inside the Worktree owner lock."""
    from agent_runtime import native_session_id, agent_home
    from workflow_objectives import authorize_coordinator
    import workflow_projects as projects
    run, _ = authorize_coordinator(run_dir, native_session_id())
    row = projects.get(run_dir)
    if (not run.get("automatic_outer") or row is None or row["state"] != "admitted"
            or Path(row["source_root"]) != primary or row["scope"] != scope
            or Path(row["home"]) != agent_home()
            or row["source_branch"] != branch):
        raise WorkflowError("OWNERSHIP_INVALID", "automatic admission does not match its original project")
    path = run_dir / "input-dependencies.json"
    declaration = read(path) if path.exists() else None
    check_source(primary, scope, declaration, base=base)
    return fingerprint(declaration) if declaration is not None else None


def check_expansion(run_dir: Path, writes: list[dict]) -> None:
    import workflow_projects as projects
    row = projects.get(run_dir)
    if row is None or row.get("outer") is None:
        return
    # Admission already protected the project. Only newly acquired external
    # claims need another observation; ordinary original-checkout work may continue.
    extra = [claim for claim in writes
             if not any(matches(claim["path"], {"kind": "prefix", "path": owned})
                        for owned in row["claims"])]
    if extra:
        base = read(run_dir / "run.json")["source_head"]
        if any(overlaps_dirty(path, claim) for path in dirty_inputs(Path(row["source_root"]), extra, base=base) for claim in extra):
            raise WorkflowError("UNCOMMITTED_INPUT", "expanded shared write claims overlap uncommitted original-source changes")
