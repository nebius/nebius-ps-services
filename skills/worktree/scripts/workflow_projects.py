"""Durable logical-project admission and selected-home resource reservations.

Locks in this module cover metadata only. Callers journal before Git/native
effects and retain reservations after uncertain failures; elapsed time never
proves that a writer or checkout disappeared.
"""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

from workflow_state import WorkflowError, lock, read, write

SCHEMA = "workflow/project-admission-v1"
CAPACITY_SCHEMA = "workflow/shared-capacity-v1"
TERMINAL = {"cleaned", "cancelled"}
LIMITS = {"objectives": 4, "workers": 4, "worktrees": 16}


def scope(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or ".." in path.parts or "\\" in value
            or path.as_posix() != value):
        raise WorkflowError("SCOPE_INVALID", "project scope must be a canonical repository-relative path")
    return value


def overlaps(left: str, right: str) -> bool:
    left, right = scope(left), scope(right)
    return (left == "." or right == "." or left == right
            or left.startswith(right + "/") or right.startswith(left + "/"))


def project_id(common: Path, relative: str) -> str:
    return hashlib.sha256((str(common.resolve()) + "\0" + scope(relative)).encode()).hexdigest()[:24]


def registry(common: Path) -> Path:
    return common / "workflow-projects"


def _records(root: Path) -> list[dict]:
    result = []
    for path in sorted(root.glob("*.json")):
        value = read(path)
        if (value.get("schema") != SCHEMA or path.name != value.get("run_id", "") + ".json"
                or value.get("state") not in {"requested", "admitted", "allocated", "aggregation-ready", "delivered", *TERMINAL}
                or value.get("owner") not in {"implement", "agentic-sdlc"}
                or not Path(value.get("run_dir", "")).is_absolute()):
            raise WorkflowError("PROJECT_REGISTRY_INVALID", "project admission state cannot be reconciled")
        scope(value["scope"])
        result.append(value)
    return result


def records(common: Path) -> list[dict]:
    root = registry(common)
    if not root.exists():
        return []
    with lock(root):
        return _records(root)


def reserve(run_dir: Path, context: dict, home: Path, session_digest: str) -> dict:
    common = Path(context["git_common_dir"])
    relative = Path(context["project_root"]).relative_to(Path(context["git_root"])).as_posix()
    root = registry(common)
    identity = {"run_id": run_dir.name, "run_dir": str(run_dir), "owner": context["owner"],
                "home": str(home), "scope": relative,
                "logical_project_root": context["project_root"], "source_root": context["git_root"],
                "source_branch": context.get("source_branch"), "creation_head": context.get("source_head"),
                "git_common_dir": str(common)}
    with lock(root):
        entries = _records(root)
        existing = next((row for row in entries if row["run_id"] == run_dir.name), None)
        if existing:
            if any(existing.get(key) != value for key, value in identity.items()):
                raise WorkflowError("OWNERSHIP_INVALID", "project admission identity changed")
            return existing
        active = [row for row in entries if row["state"] not in TERMINAL]
        for row in active:
            if row["home"] != str(home):
                raise WorkflowError("HOME_CONFLICT", "active repository objectives use another native home")
            if any(overlaps(relative, claim) for claim in row["claims"]):
                raise WorkflowError("PROJECT_CONFLICT", f"continue the active {row['owner']} objective for {row['scope']}")
        value = {"schema": SCHEMA, **identity, "state": "requested", "claims": [relative],
                 "session_sha256": session_digest, "sequence": max((row["sequence"] for row in entries), default=0) + 1,
                 "outer": None, "delivery": None, "drift_rebuilds": 0, "rebuild_tokens": []}
        write(root / (run_dir.name + ".json"), value, exclusive=True)
        return value


def get(run_dir: Path) -> dict | None:
    run = read(run_dir / "run.json")
    common = Path(run["git_common_dir"])
    return next((row for row in records(common) if row["run_dir"] == str(run_dir)), None)


def update(run_dir: Path, *, expected: str, state: str, **changes) -> dict:
    run = read(run_dir / "run.json")
    root = registry(Path(run["git_common_dir"]))
    allowed = {"requested": {"admitted", "cancelled"}, "admitted": {"allocated", "cancelled"},
               "allocated": {"aggregation-ready", "cancelled"},
               "aggregation-ready": {"allocated", "delivered"}, "delivered": {"cleaned"}}
    if state not in allowed.get(expected, set()) or set(changes) - {"outer", "delivery", "drift_rebuilds"}:
        raise WorkflowError("PROJECT_TRANSITION_INVALID", "invalid project admission transition")
    with lock(root):
        row = next((item for item in _records(root) if item["run_dir"] == str(run_dir)), None)
        if row is None:
            raise WorkflowError("PROJECT_REGISTRY_INVALID", "project admission is missing")
        if row["state"] == state and all(row.get(k) == v for k, v in changes.items()):
            return row
        if row["state"] != expected:
            raise WorkflowError("PROJECT_CONFLICT", "project admission changed; reconcile before retry")
        row.update(state=state, **changes)
        write(root / (run_dir.name + ".json"), row)
        return row


def claim(run_dir: Path, paths: list[str]) -> dict:
    """Claim additional shared paths before they enter an immutable assignment."""
    paths = sorted({scope(item) for item in paths})
    run = read(run_dir / "run.json")
    root = registry(Path(run["git_common_dir"]))
    with lock(root):
        entries = _records(root)
        row = next((item for item in entries if item["run_dir"] == str(run_dir)), None)
        if row is None or row["state"] not in {"requested", "admitted", "allocated"}:
            raise WorkflowError("PROJECT_CONFLICT", "project is not accepting write claims")
        for other in entries:
            if other["run_dir"] == str(run_dir) or other["state"] in TERMINAL:
                continue
            if any(overlaps(left, right) for left in paths for right in other["claims"]):
                raise WorkflowError("PROJECT_CONFLICT", "another objective owns a requested shared path")
        row["claims"] = sorted(set(row["claims"]) | set(paths))
        write(root / (run_dir.name + ".json"), row)
        return row


def charge_rebuild(run_dir: Path, reservation: str) -> dict:
    run = read(run_dir / "run.json")
    root = registry(Path(run["git_common_dir"]))
    with lock(root):
        row = next((item for item in _records(root) if item["run_dir"] == str(run_dir)), None)
        if row is None or row["state"] != "aggregation-ready":
            raise WorkflowError("AGGREGATION_BLOCKED", "objective has no aggregation attempt")
        if reservation not in row["rebuild_tokens"]:
            if row["drift_rebuilds"] >= 2:
                raise WorkflowError("AGGREGATION_RETRY_LIMIT", "two source-drift rebuilds were used; retain work and request an explicit retry decision")
            row["rebuild_tokens"].append(reservation)
            row["drift_rebuilds"] += 1
            write(root / (run_dir.name + ".json"), row)
        return row


def _capacity(home: Path):
    path = home / "workflow-capacity" / "reservations.json"
    value = read(path) if path.exists() else {"schema": CAPACITY_SCHEMA, "next_sequence": 1, "runs": {}}
    if value.get("schema") != CAPACITY_SCHEMA or not isinstance(value.get("runs"), dict):
        raise WorkflowError("CAPACITY_UNAVAILABLE", "shared capacity state is unsupported")
    return path, value


def admit(run_dir: Path) -> bool:
    home = run_dir.parent.parent.parent
    with lock(home / "workflow-capacity"):
        path, value = _capacity(home)
        rows = value["runs"]
        key = str(run_dir)
        if key not in rows:
            rows[key] = {"sequence": value["next_sequence"], "admitted": False,
                         "workers": [], "worktrees": [], "last_wave": 0,
                         "waiting": False, "active_wave": None, "native_ceiling": 1}
            value["next_sequence"] += 1
        row = rows[key]
        queue = sorted((r["sequence"], k) for k, r in rows.items() if not r["admitted"])
        if not row["admitted"] and sum(r["admitted"] for r in rows.values()) < LIMITS["objectives"] and queue[0][1] == key:
            row["admitted"] = True
        write(path, value)
        return row["admitted"]


def reserve_resources(run_dir: Path, kind: str, identities: list[str], *, ceiling: int | None = None) -> list[str]:
    """Atomic full-wave physical claims or partial worker-slot admission.

Replays retain existing claims. Partial allocation is allowed only for workers;
an empty result means wait, not a failed task or permission to oversubscribe.
"""
    diagnostic = kind == "diagnostics"
    if diagnostic:
        if any(not item.startswith("diagnostic/") for item in identities):
            raise WorkflowError("RESOURCE_LIMIT", "diagnostic reservation identity is invalid")
        kind = "workers"
    if kind not in {"workers", "worktrees"} or len(identities) != len(set(identities)):
        raise WorkflowError("RESOURCE_LIMIT", "invalid shared resource reservation")
    home = run_dir.parent.parent.parent
    with lock(home / "workflow-capacity"):
        path, value = _capacity(home)
        rows = value["runs"]
        row = rows.get(str(run_dir))
        if row is None or not row["admitted"]:
            raise WorkflowError("CAPACITY_WAIT", "objective is waiting for shared capacity")
        held = set(row[kind])
        pending = [item for item in identities if item not in held]
        if kind == "workers" and pending and not diagnostic:
            row["waiting"] = True
            waiting = [(item["last_wave"], item["sequence"], key) for key, item in rows.items()
                       if item["admitted"] and item["waiting"] and item["active_wave"] is None]
            if row["active_wave"] is None and min(waiting)[2] != str(run_dir):
                write(path, value)
                return [item for item in identities if item in held]
        limit = LIMITS[kind] if ceiling is None else min(LIMITS[kind], ceiling)
        if kind == "workers":
            row["native_ceiling"] = limit
            limit = min([limit, *[item["native_ceiling"] for item in rows.values()
                                  if item["workers"]]])
        available = max(0, limit - sum(len(r[kind]) for r in rows.values()))
        if kind == "worktrees" and len(pending) > available:
            raise WorkflowError("CAPACITY_WAIT", "whole wave is waiting for shared worktree capacity")
        admitted = pending[:available]
        row[kind] = sorted(held | set(admitted))
        if kind == "workers" and admitted and not diagnostic:
            row["active_wave"] = admitted[0].rsplit("/", 1)[0]
        write(path, value)
        return [item for item in identities if item in held or item in admitted]


def complete_wave(run_dir: Path) -> None:
    """Rotate fairness only after the owner's full-wave cleanup barrier."""
    home = run_dir.parent.parent.parent
    with lock(home / "workflow-capacity"):
        path, value = _capacity(home)
        row = value["runs"].get(str(run_dir))
        if row is None or row["workers"]:
            raise WorkflowError("CLEANUP_REQUIRED", "wave still holds shared worker slots")
        if row["active_wave"] is None and not row["waiting"]:
            return
        row.update(waiting=False, active_wave=None,
                   last_wave=max((item["last_wave"] for item in value["runs"].values()), default=0) + 1)
        write(path, value)


def release_resources(run_dir: Path, kind: str, identities: list[str]) -> None:
    """Called only after the execution owner proves termination or removal."""
    if kind == "diagnostics":
        if any(not item.startswith("diagnostic/") for item in identities):
            raise WorkflowError("RESOURCE_LIMIT", "diagnostic reservation identity is invalid")
        kind = "workers"
    if kind not in {"workers", "worktrees"}:
        raise WorkflowError("RESOURCE_LIMIT", "invalid resource kind")
    home = run_dir.parent.parent.parent
    with lock(home / "workflow-capacity"):
        path, value = _capacity(home)
        row = value["runs"].get(str(run_dir))
        if row is None:
            raise WorkflowError("CAPACITY_UNAVAILABLE", "shared resource owner is missing")
        row[kind] = sorted(set(row[kind]) - set(identities))
        write(path, value)


def release(run_dir: Path) -> None:
    home = run_dir.parent.parent.parent
    with lock(home / "workflow-capacity"):
        path, value = _capacity(home)
        row = value["runs"].get(str(run_dir))
        if row is None:
            return
        if row["workers"] or row["worktrees"]:
            raise WorkflowError("CLEANUP_REQUIRED", "shared resource reservations remain")
        del value["runs"][str(run_dir)]
        write(path, value)


def require_resource(run_dir: Path, kind: str, identity: str) -> None:
    if kind not in {"workers", "worktrees"}:
        raise WorkflowError("RESOURCE_LIMIT", "invalid shared resource kind")
    home = run_dir.parent.parent.parent
    with lock(home / "workflow-capacity"):
        _path, value = _capacity(home)
        row = value["runs"].get(str(run_dir))
        if row is None or not row["admitted"] or identity not in row[kind]:
            raise WorkflowError("CAPACITY_WAIT", "task has no shared resource reservation")


def require_released(run_dir: Path, identities: list[str]) -> None:
    """Verify a prelaunch fallback retains no native worker claims."""
    home = run_dir.parent.parent.parent
    with lock(home / "workflow-capacity"):
        _path, value = _capacity(home)
        if any(set(identities) & set(row["workers"]) for row in value["runs"].values()):
            raise WorkflowError("CLEANUP_REQUIRED", "diagnostic worker claims remain")
