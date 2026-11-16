"""Private observational statistics owner. Never a scheduler or acceptance gate."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

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
_load_skill_support('runtime', __file__, 'worktree/scripts/workflow_statistics.py')

from workflow_state import WorkflowError, digest, directory, lock, read, write, _regular  # noqa: E402 — verified bootstrap precedes runtime imports
from workflow_statistics_inventory import validate as validate_inventory  # noqa: E402 — verified bootstrap precedes runtime imports
from workflow_statistics_report import chat, markdown, project  # noqa: E402 — verified bootstrap precedes runtime imports

SCHEMA = "workflow/statistics-observation-v1"
TERMINAL = {"done", "cancelled", "failed", "blocked", "interrupted"}
OWNERS = {"agentic-sdlc", "implement"}


@lru_cache(maxsize=1)
def _clock_identity():
    try:
        if sys.platform == "darwin":
            boot = subprocess.check_output(["/usr/sbin/sysctl", "-n", "kern.bootsessionuuid"], timeout=2).decode().strip()
        elif sys.platform.startswith("linux"):
            boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        else:
            boot = ""
        if not boot:
            raise ValueError("boot identity unavailable")
        origin = os.uname().nodename + boot + time.get_clock_info("monotonic").implementation
        return hashlib.sha256(origin.encode()).hexdigest(), "machine-boot"
    except (OSError, ValueError, subprocess.SubprocessError):
        # A process-local identity is safe but explicitly cannot bridge restarts.
        return uuid.uuid4().hex, "process-only"


def clock():
    identity, scope = _clock_identity()
    return {"utc": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            "monotonic_ns": time.monotonic_ns(), "clock_id": identity, "scope": scope}


def safe_clock():
    try:
        return clock()
    except Exception:
        return None


def _label(value, limit=500):
    from workflow_security import contains_sensitive
    if not isinstance(value, str) or contains_sensitive(value):
        return "unavailable"
    value = re.sub(r"https?://\S+|(?:/Users/|/home/|/private/|/tmp/)\S+", "[private reference]", value)
    return " ".join(value.split())[:limit]


def _bytes(path):
    with directory(path.parent) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd)
            _regular(before)
            if before.st_size > 4 * 1024 * 1024:
                raise WorkflowError("STATISTICS_UNAVAILABLE", "observation input exceeds bound")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                data = stream.read(4 * 1024 * 1024 + 1)
            after = os.fstat(fd)
            bound = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            def signature(s):
                return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            if signature(before) != signature(after) or signature(after) != signature(bound):
                raise WorkflowError("STATISTICS_UNAVAILABLE", "observation changed during read")
            return data
        finally:
            os.close(fd)


def _files(run_dir):
    names = ["run.json", "objective.json", "current-state.json", "checkpoints/latest.json",
             "execution-policy.json", "host-capacity.json", "workflow-timing.json", "statistics/inventory.json",
             "aggregation/delivery.json", "aggregation/candidate.json"]
    paths = {run_dir / name for name in names if (run_dir / name).exists()}
    for pattern in ("objectives/*.json", "program/*.json", "plans/*.md", "plans/*.md.lock",
                    "objective-impact/execution/*.json",
                    "execution/*/coordinator.json", "execution/*/waves/*.json", "execution/*/tasks/*/*.json",
                    "execution/*/assignments/*/*.json", "execution/*/worker-results/*/*.json",
                    "execution/*/terminations/*.json", "repairs/*/repair-control.json", "repairs/*/dispatches/*.json",
                    "aggregation/operations/*.json", "aggregation/gates/*.json", "aggregation/corrections/*.json"):
        paths.update(run_dir.glob(pattern))
    pointer = run_dir / "checkpoints/latest.json"
    if pointer.exists():
        selected = read(pointer).get("path", "")
        if not re.fullmatch(r"checkpoints/checkpoint-[0-9]{4,8}\.json", selected):
            raise WorkflowError("STATISTICS_UNAVAILABLE", "checkpoint pointer is invalid")
        paths.add(run_dir / selected)
    return sorted(paths)


def _task(execution, definition, *, wave=None, record=None, plan_digest=None):
    from workflow_execution import sha256_json
    record = record or {}
    return {"id": execution + "/" + definition["task_id"], "task_id": definition["task_id"],
            "execution": execution, "wave": wave, "name": _label(definition.get("name") or definition["goal"]),
            "component": definition.get("primary_component"), "touched_components": definition.get("touched_components", []),
            "features": definition.get("feature_ids", []), "dependencies": definition["dependencies"],
            "definition_sha256": record.get("task_definition_digest") or sha256_json(definition),
            "plan_sha256": plan_digest, "status": record.get("status", "planned"),
            "owner_status": record.get("status", "planned"), "worker_result": None,
            "attempt": record.get("attempt", 0), "dispatched_at": record.get("dispatched_at"),
            "worker_started_at": record.get("worker_started_at"), "accepted": False, "integrated": False}


def snapshot(run_dir):
    """Caller holds project lock. Double-check direct coordinator checkpoint writes."""
    paths = _files(run_dir)
    from workflow_objectives import validate_run
    validate_run(run_dir)
    raw = {str(p.relative_to(run_dir)): _bytes(p) for p in paths}
    values = {name: json.loads(data) for name, data in raw.items() if name.endswith(".json") and name != "workflow-timing.json"}
    try:
        from workflow_timing import _load, COVERAGE
        counters = _load(run_dir / "workflow-timing.json")
        values["workflow-timing.json"] = {"schema": counters["schema"], "stages": counters["stages"],
                                           "inclusive": True, "coverage": COVERAGE}
    except (OSError, KeyError, ValueError, WorkflowError):
        pass  # Inclusive counters are supplemental, never required for intervals.
    run, objective = values["run.json"], values["objective.json"]
    if run["owner"] not in OWNERS:
        raise WorkflowError("STATISTICS_UNAVAILABLE", "unsupported statistics owner")
    latest = objective["revisions"][-1]
    accepted = values[latest["snapshot"]]
    current = values.get("current-state.json", {}) if run["owner"] == "agentic-sdlc" else {}
    status = run["status"] if run["status"] in TERMINAL else current.get("status", run["status"])
    dispatch_blocked = bool(run.get("fence")) or status in TERMINAL | {"paused"}
    if current:
        pointer = values.get("checkpoints/latest.json", {})
        checkpoint = values.get(pointer.get("path"), {})
        if any(checkpoint.get(key) != current.get(key) for key in ("checkpoint_id", "current_phase", "status", "current_execution")):
            raise WorkflowError("STATISTICS_UNAVAILABLE", "phase checkpoint publication is incomplete")
    tasks, retired_tasks, executions, plans, planned_waves = {}, [], [], {}, {}
    for name, data in raw.items():
        match = re.fullmatch(r"plans/((?:FEAT|SLICE)-[0-9]{3,})\.plan\.v([0-9]+)\.md", name)
        if match and name + ".lock" in raw:
            identity, version = match[1], int(match[2])
            if identity not in plans or plans[identity][0] < version:
                plans[identity] = (version, name, data)
    from workflow_execution import parse_locked_plan, _task_json, build_dependency_waves
    for identity, (_, name, data) in plans.items():
        if values.get(f"execution/{identity}/coordinator.json", {}).get("plan_digest"):
            continue  # Only admitted task definitions count after preparation.
        plan_digest = hashlib.sha256(data).hexdigest()
        definitions = parse_locked_plan(data.decode())
        planned_waves[identity] = [[task.task_id for task in wave] for wave in build_dependency_waves(
            definitions, wave_limit=values.get("execution-policy.json", {}).get("wave_task_limit", 8))]
        for definition in definitions:
            row = _task(identity, _task_json(definition), plan_digest=plan_digest)
            tasks[row["id"]] = row
    for name, value in values.items():
        match = re.fullmatch(r"execution/([^/]+)/coordinator.json", name)
        if not match:
            continue
        identity = match[1]
        waves = [values[f"execution/{identity}/waves/{w}.json"] for w in value["wave_ids"]]
        active = sum(len(w["active_task_ids"]) for w in waves)
        from workflow_capacity import effective
        capacity = effective(run_dir, value["capacity"])
        reasons = []
        if value.get("cleanup_retained"):
            reasons.append("cleanup_pending")
        if capacity < value["capacity"]:
            reasons.append("shared_or_native_capacity")
        if dispatch_blocked:
            reasons.append("workflow_dispatch_fenced" if run.get("fence") else "workflow_" + status)
        elif active < capacity:
            if value.get("active_wave") is None:
                reasons.append("serial_wave_barrier")
            elif any(w.get("ready_task_ids") for w in waves if w["wave_id"] == value["active_wave"]):
                reasons.append("ready_tasks_waiting_for_dispatch")
            else:
                reasons.append("wave_completion_pending")
        executions.append({"id": identity, "status": value["status"], "active_wave": value.get("active_wave"),
                           "plan_sha256": value.get("plan_digest"),
                           "basis_sha256": values.get(f"objective-impact/execution/{identity}.json", {}).get("sha256"),
                           "effective_capacity": capacity, "active_slots": active,
                           "available_slots": 0 if dispatch_blocked else max(0, capacity - active),
                           "dispatch_blocked": dispatch_blocked, "idle_reasons": reasons,
                           "promoted": bool(value.get("promoted_head")),
                           "cleanup": "complete" if value["status"] == "done" and not value.get("cleanup_retained") else "pending",
                           "waves": [{k: w.get(k) for k in ("wave_id", "status", "task_ids", "active_task_ids", "admitted_task_ids", "integrated_task_ids")} for w in waves]})
        for wave in waves:
            for task_id in wave["task_ids"]:
                record = values[f"execution/{identity}/tasks/{wave['wave_id']}/{task_id}.json"]
                row = _task(identity, record["task"], wave=wave["wave_id"], record=record, plan_digest=value.get("plan_digest"))
                row["accepted"] = task_id in wave.get("admitted_task_ids", [])
                row["integrated"] = task_id in wave.get("integrated_task_ids", [])
                assignment = values.get(f"execution/{identity}/assignments/{wave['wave_id']}/{task_id}.json")
                if assignment and record["attempt"]:
                    from workflow_worker_results import receipt_path, read_receipt
                    receipt = receipt_path(assignment, record["attempt"])
                    if str(receipt.relative_to(run_dir)) in values:
                        _, submitted = read_receipt(assignment, record)
                        result_status = submitted["worker_result"]["status"]
                        row["worker_result"] = {"status": result_status,
                                                "receipt_sha256": submitted["receipt_sha256"]}
                        if row["status"] in {"running", "assigned"}:
                            row["status"] = result_status if result_status in {"blocked", "replan_required"} else "submitted"
                if assignment and row["status"] in {"running", "assigned"}:
                    terminated = any(v.get("schema") == "workflow/worker-termination-v1"
                                     and v.get("status") == "terminated" and v.get("native_handle") and v.get("observation")
                                     and v.get("assignment_digest") == assignment["assignment_digest"]
                                     and v.get("worker_session_hash") == record.get("worker_session_hash")
                                     and v.get("observer_session_hash") == run.get("coordinator_session_sha256")
                                     for p, v in values.items() if p.startswith(f"execution/{identity}/terminations/"))
                    if terminated:
                        row["status"] = "terminated"
                if wave["status"] == "superseded":
                    row["disposition"] = "superseded"
                    retired_tasks.append(row)
                else:
                    tasks[row["id"]] = row
    repair_controls, repair_dispatches = [], []
    for name, control in values.items():
        match = re.fullmatch(r"repairs/((?:FEAT|SLICE)-[0-9]{3,})/repair-control.json", name)
        if not match:
            continue
        identity = match[1]
        dispatches = [(path, row) for path, row in values.items()
                      if re.fullmatch(rf"repairs/{re.escape(identity)}/dispatches/[0-9a-f]{{64}}\.json", path)]
        if (control.get("schema") != "agentic-sdlc/repair-control-v3"
                or control.get("execution_id") != identity
                or type(control.get("feature_dispatches")) is not int
                or control["feature_dispatches"] != len(dispatches)
                or type(control.get("feature_dispatch_limit")) is not int
                or control["feature_dispatch_limit"] < len(dispatches)):
            raise WorkflowError("STATISTICS_UNAVAILABLE", "repair dispatch publication is incomplete")
        repair_controls.append({"execution": identity, "status": _label(control["status"]),
                                "feature_dispatches": len(dispatches),
                                "feature_dispatch_limit": control["feature_dispatch_limit"]})
        for path, dispatch in dispatches:
            bound = {key: dispatch[key] for key in ("blocker_key", "ordinal", "classification_id",
                     "remedy_scale", "hypothesis", "evidence_reference")}
            # Repair receipts use compact ASCII JSON, unlike Worktree's file digest.
            identity_digest = hashlib.sha256(json.dumps(bound, sort_keys=True, separators=(",", ":"),
                                                       ensure_ascii=True).encode()).hexdigest()
            if (dispatch["dispatch_id"] != identity_digest or Path(path).stem != dispatch["dispatch_id"]
                    or re.fullmatch(r"[0-9a-f]{64}", str(dispatch["classification_id"])) is None
                    or type(dispatch["ordinal"]) is not int or dispatch["ordinal"] < 1):
                raise WorkflowError("STATISTICS_UNAVAILABLE", "repair dispatch identity changed")
            repair_dispatches.append({"execution": identity, "dispatch_id": dispatch["dispatch_id"],
                                      "classification_id": dispatch["classification_id"],
                                      "ordinal": dispatch["ordinal"], "source_sha256": digest(dispatch)})
    program_digest = (accepted.get("inputs") or {}).get("program_sha256")
    program = values.get(f"program/{program_digest}.json", {})
    undecomposed = [r["id"] for r in program.get("slices", []) if not any(t["execution"] == r["id"] for t in tasks.values())]
    if not program:
        architecture = values.get("statistics/inventory.json") or {}
        features = {f for r in architecture.get("components", []) for f in r["features"]}
        planned = {f for r in tasks.values() for f in r["features"]} | {r["execution"] for r in tasks.values()}
        undecomposed = sorted(features - planned)
    result = {"run_id": run_dir.name, "owner": run["owner"], "objective": {"revision": latest["revision"], "sha256": latest["sha256"],
              "outcome": _label(accepted["outcome"]), "scope": [_label(s) for s in accepted["scope"]]},
              "phase": current.get("current_phase", "accepted"), "status": status,
              "architecture": values.get("statistics/inventory.json"),
              "tasks": sorted(tasks.values(), key=lambda r: r["id"]), "executions": executions,
              "retired_tasks": sorted(retired_tasks, key=lambda r: r["id"]),
              "repair_controls": sorted(repair_controls, key=lambda r: r["execution"]),
              "repair_dispatches": sorted(repair_dispatches, key=lambda r: (r["execution"], r["dispatch_id"])),
              "planned_waves": planned_waves,
              "undecomposed_slices": undecomposed,
              "limits": values.get("execution-policy.json", {"worker_limit": 4, "wave_task_limit": 8,
                         "worker_worktree_limit": 16, "source": "unconfigured defaults"}),
              "gaps": [] if program else ["Complete application decomposition is not recorded; known task totals are partial."],
              "stage_counters": values.get("workflow-timing.json")}
    from workflow_statistics_delivery import delivery, implement_phase
    result["delivery"] = delivery(run, values, executions)
    result["scope_basis"] = "application" if run["owner"] == "agentic-sdlc" else "accepted_objective"
    if run["owner"] == "implement":
        result["phase"] = implement_phase(values, executions, result["delivery"], bool(plans))
        active_execution = run.get("corrective_execution", "FEAT-001")
        planned = bool(values.get(f"execution/{active_execution}/coordinator.json", {}).get("plan_digest"))
        result["undecomposed_slices"] = []
        result["gaps"] = [] if planned else ["Accepted Implement objective has unplanned scope; plan acceptance is not recorded."]
    inventory = result["architecture"]
    result["architecture_currentness"] = "unavailable"
    if inventory:
        try:
            archived = read(run_dir / "statistics/inventories" / (digest(inventory) + ".json"))
            if archived != inventory:
                raise WorkflowError("STATISTICS_UNAVAILABLE", "inventory archive differs")
            from workflow_impact import spec_checkout
            import workflow_program  # noqa: F401 -- canonical source reader setup
            from workflow_statistics_inventory import _SourceObservation
            selected = spec_checkout(run_dir, Path(run["project_root"]), strict_head=False)
            observation = _SourceObservation(selected, run_dir)
            stale = sorted({s["path"] for r in inventory["layers"] + inventory["components"] for s in r["sources"]
                            if observation.digest(s) != s["sha256"]
                            or s["kind"] == "objective" and s["path"] != latest["snapshot"]})
            observation.finish()
            if read(run_dir / "statistics/inventories" / (digest(inventory) + ".json")) != archived:
                raise WorkflowError("STATISTICS_UNAVAILABLE", "inventory archive changed during observation")
            result["architecture_currentness"] = "stale" if stale else "current"
            if stale:
                result["gaps"].append("Architecture sources changed; refresh inventory: " + ", ".join(stale))
        except (OSError, ValueError, RuntimeError, KeyError):
            result["gaps"].append("Architecture source currentness is unavailable; retained facts remain revision-bound.")
    if _files(run_dir) != paths or any(_bytes(run_dir / name) != data for name, data in raw.items()):
        raise WorkflowError("STATISTICS_UNAVAILABLE", "owner snapshot changed during observation")
    return result


def _events(root):
    paths = sorted((root / "events").glob("*.json"))
    if len(paths) > 10000:
        raise WorkflowError("STATISTICS_UNAVAILABLE", "observation history exceeds bounded projection")
    events = []
    prior = None
    for number, path in enumerate(paths, 1):
        event = read(path)
        unsigned = {k: v for k, v in event.items() if k != "sha256"}
        if (path.name != f"{number:06d}.json" or event.get("schema") != SCHEMA
                or event.get("sequence") != number or event.get("previous") != prior
                or event.get("sha256") != digest(unsigned)):
            raise WorkflowError("STATISTICS_UNAVAILABLE", "observation history is incomplete or changed")
        events.append(event)
        prior = event["sha256"]
    return events


def _markdown_write(path, content, *, exclusive=False):
    # Use the same pinned no-follow, owner-only publication as JSON state.
    with directory(path.parent, create=True) as parent:
        if path.exists() or path.is_symlink():
            _regular(os.stat(path.name, dir_fd=parent, follow_symlinks=False))
        name = path.name if exclusive else "." + uuid.uuid4().hex + ".tmp"
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if not exclusive:
                os.replace(name, path.name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            if not exclusive:
                try:
                    os.unlink(name, dir_fd=parent)
                except FileNotFoundError:
                    pass


def _report(root, name, report, *, immutable=False):
    path = root / (name + ".json")
    if path.exists() and immutable:
        if read(path) != report:
            raise WorkflowError("STATISTICS_UNAVAILABLE", "immutable report cannot be rewritten")
    else:
        write(path, report, exclusive=immutable)
    md = root / (name + ".md")
    if not md.exists() or not immutable:
        _markdown_write(md, markdown(report), exclusive=immutable)


def _initial(root, events):
    path = root / "initial-state.json"
    if not path.exists():
        return None, None
    binding = read(path)
    if set(binding) != {"schema", "observation_sha256"} or binding["schema"] != "workflow/statistics-initial-v1":
        raise WorkflowError("STATISTICS_UNAVAILABLE", "initial observation binding is invalid")
    selected = next((r for r in events if r["sha256"] == binding["observation_sha256"]), None)
    if selected is None:
        raise WorkflowError("STATISTICS_UNAVAILABLE", "initial observation is missing")
    return selected["state"], selected


def record(run_dir, event, *, started=None, initial=False):
    run_dir = Path(run_dir)
    with lock(run_dir.parent):
        state = snapshot(run_dir)
        if state.get("owner") == "implement":
            initial = initial and not state["executions"] and state["status"] not in TERMINAL
        root = run_dir / "statistics"
        events = _events(root)
        semantic = {k: v for k, v in state.items() if k != "stage_counters"}
        same = bool(events and events[-1]["state_sha256"] == digest(semantic)
                    and (event not in {"pause", "resume"} or event == events[-1]["event"])
                    and (not initial or any(r.get("initial") for r in events)))
        if same and all((root / name).exists() for name in ("current.json", "current.md",
                "notifications/" + events[-1]["sha256"] + ".json")) and (not initial or all(
                    (root / name).exists() for name in ("initial-state.json", "initial.json", "initial.md"))):
            return {"status": "UNCHANGED"}
        if same:
            observation = events[-1]
            observed, state = observation["clock"], observation["state"]
        else:
            observed = clock()
            from workflow_statistics_report import interval
            observation = {"schema": SCHEMA, "sequence": len(events) + 1, "event": event,
                           "initial": initial,
                           "clock": observed, "state": state, "state_sha256": digest(semantic),
                           "operation": interval(started, observed, action=event) if started else None,
                           "previous": events[-1]["sha256"] if events else None}
            observation["sha256"] = digest(observation)
            write(root / "events" / f"{len(events) + 1:06d}.json", observation, exclusive=True)
            events.append(observation)
        initial_path = root / "initial-state.json"
        intended = next((r for r in events if r.get("initial")), None)
        if intended and not initial_path.exists():
            write(initial_path, {"schema": "workflow/statistics-initial-v1",
                                 "observation_sha256": intended["sha256"]}, exclusive=True)
        baseline, initial_event = _initial(root, events)
        report = project(events, state, observed, initial=baseline)
        report["observation_sha256"] = observation["sha256"]
        if initial_event:
            initial_report = (dict(report) if initial_event == observation else
                              project(events[:initial_event["sequence"]], baseline, initial_event["clock"], initial=baseline))
            initial_report.update(report_kind="initial", observation_sha256=initial_event["sha256"])
            _report(root, "initial", initial_report, immutable=True)
        _report(root, "current", report)
        if report["report_kind"] == "terminal" or event == "pause":
            _report(root, "terminal/" + observation["sha256"], report, immutable=True)
        first_report = bool(initial_event and initial_event["sha256"] == observation["sha256"])
        notification = {"id": observation["sha256"], "summary": chat(initial_report if first_report else report),
                        "report": "statistics/initial.md" if first_report else "statistics/current.md"}
        notification_path = root / "notifications" / (observation["sha256"] + ".json")
        if notification_path.exists():
            return {"status": "UNCHANGED"}
        write(notification_path, notification, exclusive=True)
        return {"status": "RECORDED", **notification}


def safe_record(run_dir, event, **kwargs):
    """Only called after a successful owner mutation; failure cannot reverse it."""
    try:
        owner = read(Path(run_dir) / "run.json").get("owner")
        if owner not in OWNERS:
            return None
        result = record(run_dir, event, **kwargs)
        if result.get("status") == "RECORDED":
            prefix = "IMPLEMENT_STATISTICS " if owner == "implement" else "SDLC_STATISTICS "
            print(prefix + json.dumps(result, sort_keys=True), file=sys.stderr)
        return result
    except Exception:
        return {"status": "UNAVAILABLE", "message": "Statistics could not be recorded; product outcome is unchanged."}


@contextmanager
def _read_lock(run_dir):
    # Never create a lock, directory, cache or report during public status.
    with _shared_read_lock(run_dir.parent):
        aggregation = run_dir / "aggregation/validation-lock"
        if aggregation.exists():
            with _shared_read_lock(aggregation):
                yield
        else:
            yield


@contextmanager
def _shared_read_lock(root):
    with directory(root) as parent:
        fd = os.open("transition.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            _regular(os.fstat(fd))
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)


def status(run_dir):
    try:
        with _read_lock(run_dir):
            state = snapshot(run_dir)
            root = run_dir / "statistics"
            events = _events(root)
            observed = clock()
            current_digest = digest({k: v for k, v in state.items() if k != "stage_counters"})
            current = bool(events and events[-1]["state_sha256"] == current_digest)
            if current and state["status"] in TERMINAL:
                observed = events[-1]["clock"]
            baseline, _ = _initial(root, events)
            report = project(events, state, observed, initial=baseline)
            if not current:
                report["measurement_gaps"].append("Current owner state has not yet been captured in statistics history.")
                report["completeness"] = "partial"
            return report
    except Exception:
        return {"status": "unavailable", "reason": "Owner snapshot is busy, incomplete or unsafe; retry status after the transition."}


def inventory(run_dir, candidate, session):
    from workflow_objectives import authorize_coordinator
    from workflow_impact import spec_checkout
    with lock(run_dir.parent):
        run, _ = authorize_coordinator(run_dir, session)
        if run["owner"] not in OWNERS:
            raise WorkflowError("STATISTICS_UNAVAILABLE", "unsupported statistics owner")
        selected = spec_checkout(run_dir, Path(run["project_root"]), strict_head=False)
        # Load the canonical source reader through its existing owner module.
        import workflow_program  # noqa: F401
        value = validate_inventory(candidate, selected, run_dir)
        root = run_dir / "statistics"
        path = root / "inventory.json"
        if not path.exists() or read(path) != value:
            write(root / "inventories" / (digest(value) + ".json"), value)  # content addressed
            write(path, value)
        return record(run_dir, "inventory", initial=not (run_dir / "current-state.json").exists())


def admit_initial_storage(run_dir):
    """Narrow replay-safe initializer allowance; never allow execution artifacts."""
    root = run_dir / "statistics"
    if not root.exists():
        return
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise WorkflowError("RECOVERY_REQUIRED", "unsafe statistics storage")
        if path.is_dir():
            if relative not in {"events", "notifications", "inventories"}:
                raise WorkflowError("RECOVERY_REQUIRED", "unknown statistics directory")
        elif not (relative in {"inventory.json", "initial-state.json", "initial.json", "initial.md", "current.json", "current.md"}
                  or re.fullmatch(r"events/[0-9]{6}\.json|(?:notifications|inventories)/[0-9a-f]{64}\.json", relative)):
            raise WorkflowError("RECOVERY_REQUIRED", "unknown statistics artifact")
        else:
            _bytes(path)
    for event in _events(root):
        if event["state"]["tasks"] or event["state"]["phase"] not in {"accepted", "sdlc-start"}:
            raise WorkflowError("RECOVERY_REQUIRED", "statistics record downstream execution")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("action", choices=("checkpoint", "inventory"))
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--candidate", type=Path)
    args = parser.parse_args(argv)
    from agent_runtime import native_session_id
    from workflow_objectives import authorize_coordinator
    try:
        run, _ = authorize_coordinator(args.run_dir, native_session_id())
        if run["owner"] not in OWNERS:
            raise WorkflowError("STATISTICS_UNAVAILABLE", "unsupported statistics owner")
        if args.action == "inventory":
            if args.candidate is None:
                parser.error("inventory requires --candidate")
            result = inventory(args.run_dir, read(args.candidate), native_session_id())
        else:
            result = safe_record(args.run_dir, "phase")
    except Exception:
        result = {"status": "UNAVAILABLE", "message": "Statistics input is unavailable or invalid; product state is unchanged."}
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
