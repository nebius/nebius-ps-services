"""Coordinator-issued, convergent SDLC steering readiness; never a phase runner."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

from workflow_objectives import authorize_coordinator, validate_run
from workflow_state import WorkflowError, digest, directory, lock, read, write

SCHEMA = "workflow/steering-readiness-v1"
MAX_INPUT_FILES = 256
MAX_TRAVERSAL_ENTRIES = 4096
_CLOCK_FIELDS = {"created_at", "updated_at", "observed_at", "completed_at", "started_at", "timestamp", "started_ns", "completed_ns", "deadline_ns", "received_at"}
_METADATA_SCHEMAS = {
    "agentic-sdlc/phase-result-v6", "agentic-sdlc/failure-event-v2",
    "agentic-sdlc/diagnosis-v3", "agentic-sdlc/failure-classification-v2",
    "agentic-sdlc/repair-control-v3", "agentic-sdlc/repair-journal-event-v2",
    "agentic-sdlc/design-approval-v2", "agentic-sdlc/revalidation-evidence-v2",
}


def _record_metadata(value, *, phase=False):
    """Only an owner's immediate scalar metadata fields are volatile."""
    fields = _CLOCK_FIELDS | {"current_phase"} if phase else _CLOCK_FIELDS
    return {key: item for key, item in value.items()
            if key not in fields or (item is not None and type(item) not in {str, int})}


def _semantic(value):
    # Generic JSON can contain schemas, examples and business data named
    # updated_at/timestamp. Never erase that decision content by recursive name.
    if isinstance(value, dict) and isinstance(value.get("schema"), str) and value["schema"] in _METADATA_SCHEMAS:
        return _record_metadata(value)
    return value


def _bytes(path: Path) -> bytes:
    from project_specs_lib.contracts import _read_file, ProjectSpecError
    try:
        return _read_file(path, "readiness input")[0]
    except ProjectSpecError as error:
        raise WorkflowError("READINESS_INVALID", "readiness input cannot be safely read") from error


def _markdown_semantic(text: str, spec_kind: str | None = None) -> str:
    """Ignore known document metadata clocks, preserving dates in content."""
    offset = 0
    if spec_kind is not None:
        from project_specs_lib.contracts import spec_markers
        marker, _ = spec_markers(spec_kind)
        found = re.search(r"(?m)^" + re.escape(marker) + r"\r?\n", text)
        if found is not None:
            offset = found.end()
    # Canonical specs place metadata after the managed marker. Other Markdown
    # can have leading frontmatter, with optional one-line lint comments.
    leading = re.match(r"(?:\s*<!--[^\n]*-->\r?\n|\s*\r?\n)*", text[offset:])
    offset += leading.end()
    header = re.match(r"---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)", text[offset:])
    if header is None:
        return text
    start, end = offset + header.start(1), offset + header.end(1)
    metadata = "\n".join(line for line in text[start:end].splitlines()
                         if not re.match(r"(?:created_at|updated_at):", line))
    return text[:start] + metadata + text[end:]


def _file_digest(path: Path, *, spec_kind: str | None = None) -> str:
    data = _bytes(path)
    if path.suffix == ".json":
        try:
            return digest(_semantic(json.loads(data)))
        except (ValueError, UnicodeError) as error:
            raise WorkflowError("READINESS_INVALID", "decision evidence is not valid JSON") from error
    if path.suffix == ".md":
        try:
            data = _markdown_semantic(data.decode("utf-8"), spec_kind).encode("utf-8")
        except UnicodeError as error:
            raise WorkflowError("READINESS_INVALID", "decision evidence is not UTF-8") from error
    return hashlib.sha256(data).hexdigest()


def _receipt(root: Path) -> dict:
    receipt = read(root / "current.json")
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    identity = receipt.get("receipt_sha256")
    if not isinstance(identity, str) or re.fullmatch(r"[0-9a-f]{64}", identity) is None:
        raise WorkflowError("READINESS_INVALID", "readiness receipt identity is invalid")
    try:
        archived = read(root / (identity + ".json"))
    except FileNotFoundError as error:
        raise WorkflowError("READINESS_INVALID", "readiness receipt was not issued by its owner") from error
    if digest(unsigned) != identity or archived != receipt:
        raise WorkflowError("READINESS_INVALID", "readiness receipt changed")
    return receipt


def _input_files(run_dir: Path, execution_id: str, context, plan):
    """Bound enumeration before sorting; never follow links or drop evidence."""
    files = set()
    visited = 0

    def add(path, mode):
        if not stat.S_ISREG(mode):
            raise WorkflowError("READINESS_INVALID", "readiness input is not a regular file")
        files.add(path)
        if len(files) > MAX_INPUT_FILES:
            raise WorkflowError("READINESS_INVALID", "readiness input file inventory exceeds its bound")

    for path in context + ([plan] if plan is not None else []):
        add(path, path.lstat().st_mode)
    pending = [(run_dir / "evidence" / execution_id, False),
               (run_dir / "repairs" / execution_id, True)]
    if plan is not None:
        pending.append((plan.parent, None))
    while pending:
        parent, json_only = pending.pop()
        if not os.path.lexists(parent):
            continue
        with directory(parent) as descriptor, os.scandir(descriptor) as entries:
            for entry in entries:
                visited += 1
                if visited > MAX_TRAVERSAL_ENTRIES:
                    raise WorkflowError("READINESS_INVALID", "readiness traversal exceeds its bound")
                path = parent / entry.name
                mode = entry.stat(follow_symlinks=False).st_mode
                if json_only is None and not entry.name.startswith(plan.name + "."):
                    continue
                if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                    raise WorkflowError("READINESS_INVALID", "readiness input is linked or special")
                if stat.S_ISDIR(mode):
                    if json_only is not None:
                        pending.append((path, json_only))
                elif json_only is not True or path.suffix == ".json":
                    add(path, mode)
    return sorted(files)


def inputs(run_dir: Path, execution_id: str, plan: Path | None = None) -> dict:
    from workflow_impact import spec_checkout
    run, objective = validate_run(run_dir)
    if re.fullmatch(r"(?:FEAT|SLICE)-[0-9]{3,}", execution_id) is None:
        raise WorkflowError("READINESS_INVALID", "execution identity is invalid")
    project = spec_checkout(run_dir, Path(run["project_root"]), execution_id=execution_id,
                            strict_head=False)
    files = {"specs/" + name: _file_digest(project / "docs" / name, spec_kind=Path(name).stem)
             for name in ("requirements.md", "design.md")}
    features = [execution_id]
    if execution_id.startswith("SLICE-"):
        from workflow_program import verify_inputs
        verified = verify_inputs(run_dir, project)
        program = verified["definition"] if verified else None
        selected = next((row for row in program["slices"] if row["id"] == execution_id), None) if program else None
        if selected is None:
            raise WorkflowError("CONTEXT_PENDING", "delivery slice context mapping is missing")
        features = selected["feature_ids"]
    context = [run_dir / "context" / (feature + ".context.md") for feature in features]
    if not context or any(not path.is_file() for path in context):
        raise WorkflowError("CONTEXT_PENDING", "sdlc-gather-context must produce the current context pack")
    coordinator = run_dir / "execution" / execution_id / "coordinator.json"
    readiness = run_dir / "steering/readiness" / execution_id
    if plan is None and (readiness / "current.json").exists():
        selected = _receipt(readiness).get("plan_path")
        if selected:
            plan = run_dir / selected
    if plan is None and coordinator.exists():
        selected = read(coordinator).get("plan_path")
        if selected:
            plan = Path(selected)
    if plan is not None:
        plan = plan.absolute()
        if not plan.is_relative_to(run_dir / "plans") or ".." in plan.parts or plan.is_symlink():
            raise WorkflowError("READINESS_INVALID", "plan must be owned by this private run")
    # Only this execution and its admitted context packs participate.
    for path in _input_files(run_dir, execution_id, context, plan):
        relative = path.relative_to(run_dir).as_posix()
        files[relative] = _file_digest(path)
    projection = {"schema": SCHEMA, "run_id": run_dir.name, "execution_id": execution_id,
                  "objective_sha256": objective["revisions"][-1]["sha256"],
                  "objective_revision": objective["revisions"][-1]["revision"],
                  "plan_path": plan.relative_to(run_dir).as_posix() if plan is not None else None,
                  "coordinator_session_sha256": run["coordinator_session_sha256"],
                  "inputs": files}
    return {**projection, "inputs_sha256": digest(projection)}


def _outputs(run_dir: Path, projection: dict) -> dict:
    state = read(run_dir / "steering" / "auto-steering.json")
    if (state.get("state_version") != 2 or state.get("run_id") != run_dir.name
            or state.get("current_execution") != projection["execution_id"]
            or state.get("routing_request") is not None
            or not isinstance(state.get("entries"), list)
            or not isinstance(state.get("active_reminders"), list)):
        raise WorkflowError("STEERING_PENDING", "sdlc-auto-steering has unresolved routing")
    for entry in state["entries"]:
        if (not isinstance(entry, dict) or entry.get("status") not in {"resolved", "active"}
                or entry.get("status") == "active" and entry.get("disposition") not in {
                    "runtime-only", "resolved", "superseded", "rejected"}):
            raise WorkflowError("STEERING_PENDING", "sdlc-auto-steering must resolve its pending entries")
    if not any(entry.get("objective_sha256") == projection["objective_sha256"]
               and entry.get("objective_revision") == projection["objective_revision"]
               for entry in state["entries"]):
        raise WorkflowError("STEERING_PENDING", "steering has not reviewed the accepted objective")
    ledger = _bytes(run_dir / "STEERING.md").decode()
    ledger = re.sub(r"(?m)^- (?:Updated at|Current phase|Received at):[^\n]*\n?", "", ledger)
    semantic = _record_metadata(state, phase=True)
    semantic["entries"] = [_record_metadata(entry) for entry in state["entries"]]
    return {"ledger_sha256": hashlib.sha256(ledger.encode()).hexdigest(),
            "state_sha256": digest(semantic)}


def issue(run_dir: Path, execution_id: str, reviewed_inputs: str, session: str,
          plan: Path | None = None) -> dict:
    from workflow_timing import measure
    with lock(run_dir.parent), measure(run_dir, "readiness"):
        authorize_coordinator(run_dir, session, owner="agentic-sdlc")
        projection = inputs(run_dir, execution_id, plan)
        if reviewed_inputs != projection["inputs_sha256"]:
            raise WorkflowError("STEERING_PENDING", "steering inputs changed; review the current inputs")
        receipt = {**projection, **_outputs(run_dir, projection)}
        receipt["receipt_sha256"] = digest(receipt)
        root = run_dir / "steering" / "readiness" / execution_id
        history = root / (receipt["receipt_sha256"] + ".json")
        if history.exists():
            if read(history) != receipt:
                raise WorkflowError("READINESS_INVALID", "immutable readiness receipt changed")
        else:
            write(history, receipt, exclusive=True)
        pointer = root / "current.json"
        if not pointer.exists() or read(pointer) != receipt:
            write(pointer, receipt)
        return {"status": "ready", "receipt_sha256": receipt["receipt_sha256"],
                "inputs_sha256": reviewed_inputs}


def require(run_dir: Path, execution_id: str, plan: Path | None = None) -> dict:
    run, _ = validate_run(run_dir)
    if run["owner"] != "agentic-sdlc":
        return {"status": "not_applicable"}
    root = run_dir / "steering" / "readiness" / execution_id
    if not root.exists():
        raise WorkflowError("STEERING_PENDING", "next owning phase: sdlc-auto-steering")
    try:
        receipt = _receipt(root)
    except FileNotFoundError as error:
        raise WorkflowError("STEERING_PENDING", "next owning phase: sdlc-auto-steering") from error
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    projection = inputs(run_dir, execution_id, plan)
    if (unsigned != {**projection, **_outputs(run_dir, projection)}):
        raise WorkflowError("STEERING_PENDING", "inputs changed; next owning phase: sdlc-auto-steering")
    return {"status": "ready", "receipt_sha256": receipt["receipt_sha256"]}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Read-only SDLC steering readiness guard", allow_abbrev=False)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--execution", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(require(args.run_dir, args.execution), sort_keys=True))
    except (WorkflowError, OSError, ValueError) as error:
        print(json.dumps({"status": "pending", "code": getattr(error, "code", "READINESS_INVALID"),
                          "next_recommended_skill": "sdlc-auto-steering"}, sort_keys=True))
        raise SystemExit(3)
