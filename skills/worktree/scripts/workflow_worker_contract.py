"""Canonical assignment launch contract and strict native result validation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys

from workflow_security import contains_sensitive
from workflow_liveness import health_policy, RETIRED_FIELDS
from workflow_state import WorkflowError


class WorkerContractError(WorkflowError):
    """Fixed safe diagnostics for a rejected worker contract."""


SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
REQUIREMENT_ID_RE = re.compile(r"REQ-[0-9]{3,}\Z")
DESIGN_ID_RE = re.compile(r"FEAT-[0-9]{3,}\Z")
SPEC_GAP_KINDS = {"requirement", "design", "traceability"}


def _spec_gap_rejection(value: object) -> str | None:
    if not isinstance(value, list):
        return "spec_gaps_type"
    for gap in value:
        if not isinstance(gap, dict) or set(gap) != {
            "kind",
            "summary",
            "evidence",
            "requirement_ids",
            "design_ids",
        }:
            return "spec_gap_shape"
        if not isinstance(gap["kind"], str) or gap["kind"] not in SPEC_GAP_KINDS:
            return "spec_gap_kind"
        if not isinstance(gap["summary"], str):
            return "spec_gap_summary_type"
        if not gap["summary"].strip():
            return "spec_gap_summary_empty"
        if contains_sensitive(gap["summary"]):
            return "spec_gap_summary_sensitive"
        if not isinstance(gap["evidence"], list):
            return "spec_gap_evidence_type"
        if not gap["evidence"]:
            return "spec_gap_evidence_empty"
        for item in gap["evidence"]:
            if not isinstance(item, str):
                return "spec_gap_evidence_item_type"
            if not item.strip():
                return "spec_gap_evidence_item_empty"
            if contains_sensitive(item):
                return "spec_gap_evidence_item_sensitive"
        for field, pattern in (
            ("requirement_ids", REQUIREMENT_ID_RE),
            ("design_ids", DESIGN_ID_RE),
        ):
            items = gap[field]
            if not isinstance(items, list):
                return f"spec_gap_{field}_type"
            if any(
                not isinstance(item, str) or pattern.fullmatch(item) is None
                for item in items
            ):
                return f"spec_gap_{field}_item"
            if len(set(items)) != len(items):
                return f"spec_gap_{field}_duplicate"
    return None


def result_rejection(result: object, assignment: dict[str, object]) -> str | None:
    """Return only a fixed reason; never retain or reflect rejected values."""
    if not isinstance(result, dict) or set(result) != {
        "task_id",
        "assignment_digest",
        "status",
        "summary",
        "decisions",
        "open_risks",
        "spec_gaps",
        "validation",
        "review",
    }:
        return "shape"
    if result.get("task_id") != assignment["task_id"]:
        return "task_identity"
    if result.get("assignment_digest") != assignment["assignment_digest"]:
        return "assignment_identity"
    if not isinstance(result["status"], str) or result["status"] not in {
        "implemented",
        "verified_no_change",
        "replan_required",
        "blocked",
    }:
        return "status"
    for field in ("summary", "validation", "review"):
        value = result[field]
        if not isinstance(value, str):
            return f"{field}_type"
        if not value.strip():
            return f"{field}_empty"
        if contains_sensitive(value):
            return f"{field}_sensitive"
    for field in ("decisions", "open_risks"):
        values = result[field]
        if not isinstance(values, list):
            return f"{field}_type"
        for value in values:
            if not isinstance(value, str):
                return f"{field}_item_type"
            if not value.strip():
                return f"{field}_item_empty"
            if contains_sensitive(value):
                return f"{field}_item_sensitive"
    gap_rejection = _spec_gap_rejection(result["spec_gaps"])
    if gap_rejection is not None:
        return gap_rejection
    if result["status"] in {"implemented", "verified_no_change"} and result["spec_gaps"]:
        return "implemented_with_spec_gaps"
    if result["status"] == "replan_required" and not result["spec_gaps"]:
        return "replan_without_spec_gap"
    if result["status"] == "blocked" and result["spec_gaps"]:
        return "blocked_with_spec_gaps"
    return None

def result_feedback(result: object, assignment: dict[str, object]) -> dict:
    """Only a complete result inside a redundant envelope is structural input."""
    rejection = result_rejection(result, assignment)
    normalized = result.get("result") if isinstance(result, dict) and set(result) == {"result"} else None
    eligible = rejection is not None and result_rejection(normalized, assignment) is None
    semantic = result if rejection is None else normalized if eligible else None
    return {"schema": "workflow/result-feedback-v1", "owner": "worktree",
            "status": "valid" if rejection is None else "rejected", "effects": "none",
            "candidate_sha256": stable_digest(result), "code": rejection,
            "correction_kind": "redundant_result_envelope" if eligible else None,
            "semantic_sha256": stable_digest(semantic) if semantic is not None else None,
            "next_action": "Return the complete canonical result object without an outer result envelope."
            if eligible else "Use the canonical result schema; semantic failures require classification."}


def stable_digest(value: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def canonical_execution_helper() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "worktree"
        / "scripts"
        / "workflow_transition.py"
    ).resolve()


def load_assignment(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkerContractError(
            "EXECUTION_STATE_INVALID", "worker assignment is unreadable"
        ) from exc
    if isinstance(value, dict) and value.get("schema") in {
        "agentic-sdlc/worker-assignment-v1",
        "agentic-sdlc/worker-assignment-v2",
        "agentic-sdlc/worker-assignment-v3",
    }:
        raise WorkerContractError(
            "WORKFLOW_UPGRADE_REQUIRED", "legacy worker assignment is unsupported"
        )
    if not isinstance(value, dict) or value.get("schema") != (
        "workflow/worker-assignment-v4"
    ):
        raise WorkerContractError(
            "EXECUTION_STATE_INVALID", "worker assignment schema is invalid"
        )
    digest = value.get("assignment_digest")
    unsigned = dict(value)
    unsigned.pop("assignment_digest", None)
    if not isinstance(digest, str) or stable_digest(unsigned) != digest:
        raise WorkerContractError(
            "EXECUTION_STATE_INVALID", "worker assignment digest is invalid"
        )
    scope_cwd = Path(str(value.get("scope_cwd") or ""))
    if not scope_cwd.is_absolute() or scope_cwd.is_symlink() or not scope_cwd.is_dir():
        raise WorkerContractError("WORKTREE_CONFLICT", "worker scope cwd is invalid")
    helper = Path(str(value.get("execution_helper") or ""))
    expected_helper = canonical_execution_helper()
    run_dir = Path(str(value.get("run_dir") or ""))
    profile = value.get("worker_profile")
    spec_receipt = value.get("project_spec_receipt")
    if (
        bool(RETIRED_FIELDS.intersection(value))
        or not helper.is_absolute()
        or helper.is_symlink()
        or not helper.is_file()
        or helper.resolve() != expected_helper
        or not expected_helper.is_file()
        or not run_dir.is_absolute()
        or run_dir.is_symlink()
        or profile not in {"standard", "integration"}
        or value.get("health_policy") != health_policy(profile)
        or value.get("worker_phases")
        != ["preflight", "implementing", "validating", "reviewing", "reporting"]
        or SHA256_RE.fullmatch(str(value.get("root_intent_sha256") or "")) is None
        or not isinstance(spec_receipt, dict)
        or set(spec_receipt)
        != {"schema", "requirements_sha256", "design_sha256"}
        or spec_receipt.get("schema")
        != "maintain-project-specs.worker-receipt.v1"
        or any(
            spec_receipt.get(field) != "absent"
            and SHA256_RE.fullmatch(str(spec_receipt.get(field) or "")) is None
            for field in ("requirements_sha256", "design_sha256")
        )
    ):
        raise WorkerContractError(
            "EXECUTION_STATE_INVALID", "worker assignment liveness is invalid"
        )
    handoff_path = Path(str(value.get("incoming_handoff_path") or ""))
    if (
        not handoff_path.is_absolute()
        or handoff_path.is_symlink()
        or not handoff_path.is_file()
        or (handoff_path.stat().st_mode & 0o777) != 0o600
    ):
        raise WorkerContractError(
            "EXECUTION_STATE_INVALID", "incoming handoff path or mode is invalid"
        )
    try:
        handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkerContractError(
            "EXECUTION_STATE_INVALID", "incoming handoff is unreadable"
        ) from exc
    unsigned_handoff = dict(handoff) if isinstance(handoff, dict) else {}
    handoff_digest = unsigned_handoff.pop("handoff_digest", None)
    if (
        not isinstance(handoff, dict)
        or handoff.get("schema") != "agentic-sdlc/incoming-handoff-v3"
        or handoff.get("execution_id") != value.get("execution_id")
        or handoff.get("wave_id") != value.get("wave_id")
        or handoff.get("task_id") != value.get("task_id")
        or handoff_digest != value.get("incoming_handoff_digest")
        or handoff_digest != stable_digest(unsigned_handoff)
    ):
        raise WorkerContractError(
            "EXECUTION_STATE_INVALID", "incoming handoff context is invalid"
        )
    return value


def worker_prompt(
    assignment_path: Path, assignment: dict[str, object], *, recovery_attempt: int | None = None,
) -> str:
    from workflow_validation import task_validation_context

    try:
        validation_context = task_validation_context(assignment)
    except WorkflowError as error:
        raise WorkerContractError(error.code, error.message) from error
    from workflow_uat import normalize as normalize_uat
    if normalize_uat(assignment.get("uat_policy")) != assignment.get("uat_policy"):
        raise WorkerContractError("BASIS_INVALID", "frozen UAT policy is missing")
    run_dir = Path(str(assignment["run_dir"]))
    helper = Path(str(assignment["execution_helper"]))
    start = [
        sys.executable,
        str(helper),
        "task-start",
        "--run-dir",
        str(run_dir),
        "--execution",
        str(assignment["execution_id"]),
        "--wave",
        str(assignment["wave_id"]),
        "--task",
        str(assignment["task_id"]),
        "--assignment-digest",
        str(assignment["assignment_digest"]),
        "--scope-cwd",
        str(assignment["scope_cwd"]),
    ]
    if recovery_attempt is not None:
        start[2] = "task-recover"
        position = start.index("--assignment-digest")
        del start[position:position + 2]
        start.extend(["--expected-attempt", str(recovery_attempt), "--confirmed-stopped"])
    instructions = [
        "Implement exactly one immutable workflow worker assignment.",
        "You are not alone in the repository. Preserve others' work and stay inside your assigned write claims.",
        "Use fresh assignment-only context. Do not inherit another worker's or the coordinator's conversation.",
        "Read the incoming handoff; retrieve digest-bound predecessor results only when their bounded summaries are insufficient.",
        "Set the shell tool's working directory explicitly for task-start, task-recover and every heartbeat: "
        + str(assignment["scope_cwd"])
        + ". Do not rely on an inherited agent default directory. Use the same scope for other commands, "
        "except the explicit frozen validation cwd below. Use absolute paths within your claims for product file edits.",
        "Run the authorized task-recover command before editing:" if recovery_attempt is not None
        else "Run the task-start command represented by this JSON argv before editing:",
        json.dumps(start),
        "Call task-start promptly before editing. Long startup requests diagnosis; it does not expire ownership."
        if recovery_attempt is None else
        "Recovery requires the exact current owner authorization; do not recycle a healthy worker.",
        "Run direct task-heartbeat commands with a target cadence of "
        f"{assignment['health_policy']['heartbeat_interval_seconds']} seconds while working. Use one of "
        "the assignment's worker_phases and this JSON argv prefix:",
        json.dumps(
            [
                sys.executable,
                str(helper),
                "task-heartbeat",
                "--run-dir",
                str(run_dir),
                "--execution",
                str(assignment["execution_id"]),
                "--wave",
                str(assignment["wave_id"]),
                "--task",
                str(assignment["task_id"]),
                "--assignment-digest",
                str(assignment["assignment_digest"]),
                "--phase",
            ]
        ),
        "Invoke heartbeats directly; never create a background heartbeat loop.",
        "Heartbeat at available work and waiting boundaries; use short yielding tool waits where supported. "
        "Report cadence gaps honestly. Missing telemetry requires observation and diagnosis, not a failure claim.",
        "Allowed heartbeat phases: " + json.dumps(assignment["worker_phases"]),
        "There is no duration-only attempt deadline. Continue healthy long-running work. "
        "Report meaningful progress, owned command state and external waits separately. "
        "CPU, output, dirty paths and heartbeat messages alone do not prove useful progress. "
        "Cancellation, confirmed failure, authority/scope violations and resource safety still apply.",
        "Read the assignment JSON at:",
        str(assignment_path.resolve()),
        "Check a result before returning it using this read-only JSON argv (replace only <result-json>):",
        json.dumps([sys.executable, str(helper), "check-result", "--assignment",
                    str(assignment_path.resolve()), "--result", "<result-json>"]),
        "Use the canonical schema at " + str(Path(__file__).resolve().parents[1] / "assets/worker-result.schema.json")
        + "; never import private modules with runpy. This check does not ingest or accept a result.",
        "Work only from its scope_cwd and write claims.",
        "Read the effective project instructions for that assigned scope before editing; "
        "do not rely on instruction discovery from the parent agent's directory. "
        "Discover which instruction files exist before reading them. Optional absent "
        "AGENTS.md or AGENTS.override.md files are not required artifacts; do not "
        "include nonexistent paths in a read command. Read every applicable existing "
        "file, with AGENTS.override.md taking precedence in its directory.",
        "Your exact frozen task validation definitions follow. Use their explicit "
        "Git-checkout-root cwd, even for a nested project; do not search for or "
        "guess a validation sidecar. Preserve argv, timeout and allowed environment "
        f"keys; {{python}} means {sys.executable}. These definitions do not authorize "
        "early checks, retries or changes to the assignment's expected outcomes. "
        "The coordinator still performs independent acceptance after termination.",
        json.dumps(validation_context, ensure_ascii=False),
        "Frozen UAT policy: " + json.dumps(assignment["uat_policy"], sort_keys=True) +
        ". Run required development checks. UAT belongs to the coordinator at a ready scope; "
        "do not launch container or cloud campaigns or block this task on optional UAT.",
        "Inherit root_intent_sha256 and project_spec_receipt exactly. Do not "
        "reclassify user intent or edit canonical project specs.",
        "Report any requirement, design, or traceability discovery through the "
        "typed spec_gaps result field for root-coordinator reconciliation.",
        ("Before reporting success, run the owner-supervised SDLC task checks with this argv: "
         + json.dumps([sys.executable, str(helper), "task-accept", "--assignment", str(assignment_path.resolve())])
         + ". On failure report blocked through the native result; preserve evidence and use coordinator recovery. "
         "Deferred criteria remain open obligations and do not replace final UAT.")
        if assignment["owner"] == "agentic-sdlc" else "Follow the frozen task validation instructions.",
        "Before an action with known permission or network prerequisites, use "
        "supported tools and the host's native approval mechanism. Approval "
        "requests do not themselves grant access. A denied approval is a "
        "policy/authority stop, never permission to bypass the restriction. "
        "An unexpected executed failure still requires the blocked handoff below.",
        "Return implemented only when assigned work, validation and review are "
        "complete, with no spec gaps. Return verified_no_change when the exact clean "
        "base already satisfies the task; describe the acceptance evidence without "
        "creating cosmetic changes. The coordinator independently validates this "
        "proposal after termination; normal liveness budgets still apply. "
        "Return replan_required only for material "
        "specification gaps, with a nonempty typed spec_gaps list.",
        "If work or validation is incomplete because of a non-specification "
        "runtime, tool, environment or missing-evidence failure, return blocked "
        "with empty spec_gaps and honest summary, validation and review evidence. "
        "Never invent a specification gap or claim unperformed checks passed.",
        "Before repairing or rerunning an unexpected failed check or a blocking "
        "review finding, stop changes and return blocked to the coordinator. "
        "This includes lint, syntax, formatter, test, runtime and corrective-oracle "
        "failures. Preserve the exact failed command, outcome and available "
        "candidate evidence privately without secrets; disclose missing evidence "
        "rather than presenting a reconstruction as contemporaneous.",
        "Only a negative outcome declared before execution in the immutable assignment "
        "validation is expected development feedback. A different failure or "
        "unexpected pass requires the same blocked handoff. Never relabel an "
        "observed failure as expected. Normal in-scope implementation and prescribed "
        "formatting before checks remain allowed.",
        "After failure, perform only necessary evidence preservation, direct "
        "heartbeats and authorized safe runtime teardown and lease release, then "
        "terminate with the complete result. The coordinator owns classification "
        "and authorizes any retry through the existing recovery or replan path. "
        "A later successful check cannot erase an earlier unclassified failure.",
        "Do not commit, merge, replan, edit coordinator state, or start another task.",
    ]
    for resource in runtime_commands(assignment_path, assignment):
        instructions.extend([
            "For the assigned shared runtime, acquire before use and release after all runtime actions including teardown. "
            "Use these exact JSON argv values; the resource name already omits the conflict-domain prefix:",
            json.dumps(resource),
        ])
    if assignment.get("diagnosis_id") is not None:
        instructions.extend(
            [
                "This is a corrective task bound to the assignment's diagnosis_id.",
                "After the smallest bounded repair, run the assignment's original "
                "regression_oracle first, then its affected-boundary validation.",
                "Do not reinterpret or modify any completed task definition.",
            ]
        )
    instructions.extend(
        [
            "Validate the change and perform a focused code review.",
            "Return only the schema-conforming worker result.",
        ]
    )
    return "\n".join(instructions)


def runtime_commands(assignment_path: Path, assignment: dict[str, object]) -> list[dict]:
    helper = str(assignment["execution_helper"])
    return [
        {
            "resource": domain.removeprefix("runtime:"),
            **{action: [sys.executable, helper, "runtime-" + action,
                        "--assignment", str(assignment_path.resolve()),
                        "--resource", domain.removeprefix("runtime:")]
               for action in ("acquire", "verify", "release")},
        }
        for domain in assignment.get("conflict_domains", []) if domain.startswith("runtime:")
    ]


def _support_write_roots(assignment: dict[str, object], *, recovery: bool) -> list[str]:
    run_dir = Path(str(assignment["run_dir"]))
    roots = [str(run_dir.parent)]
    if recovery or any(domain.startswith("runtime:") for domain in assignment.get("conflict_domains", [])):
        roots.append(str(run_dir.parents[2] / "workflow-resources"))
    return roots


def validate_launch_identity(
    assignment_path: Path, home: Path, session: str, *, recovery: bool = False,
) -> None:
    """Validate native grant identity without creating directories or arming work."""
    from workflow_objectives import ROOTS, observe_binding, project_context, validate_run
    from workflow_resources import historical_assignment
    from workflow_state import WorkflowError, directory, read

    assignment = historical_assignment(assignment_path)
    run_dir = Path(assignment["run_dir"])
    home = home.expanduser().absolute()
    run, _ = validate_run(run_dir)
    owner = run["owner"]
    if (assignment.get("owner") != owner or run_dir.parent.parent != home / ROOTS[owner]
            or assignment.get("selected_project_root") != run.get("project_root")):
        raise WorkflowError("OWNERSHIP_INVALID", "native launch does not belong to the selected home and run")
    with directory(home):
        pass
    project = Path(run["project_root"])
    bound = observe_binding(home, owner, session, project)
    root, context, _, _ = project_context(project, home, owner)
    if (bound is None or bound[0] != run_dir or root != run_dir.parent
            or read(root / "project.json") != context
            or assignment.get("git_common_dir") != context["git_common_dir"]):
        raise WorkflowError("OWNERSHIP_INVALID", "native launch project or coordinator binding is invalid")
    for value in _support_write_roots(assignment, recovery=recovery):
        path = Path(value)
        if path.exists() or path.is_symlink():
            with directory(path):
                pass
        elif path != home / "workflow-resources":
            raise WorkflowError("OWNERSHIP_INVALID", "native launch support directory is unavailable")


def prepare_launch_support(launch: dict[str, object]) -> None:
    """Prepare admitted support storage while the coordinator holds its project lock."""
    from workflow_state import directory

    for index, value in enumerate(launch["support_write_roots"]):
        with directory(Path(value), create=index > 0):
            pass


def launch_contract(
    assignment_path: Path, assignment: dict[str, object], *, recovery_attempt: int | None = None,
) -> dict[str, object]:
    """Build the same envelope for every host adapter after assignment validation."""
    if recovery_attempt is not None and (type(recovery_attempt) is not int or recovery_attempt < 1):
        raise WorkerContractError("EXECUTION_STATE_INVALID", "recovery attempt is invalid")
    schema = Path(__file__).resolve().parents[1] / "assets/worker-result.schema.json"
    if schema.is_symlink() or not schema.is_file():
        raise WorkerContractError("EXECUTION_STATE_INVALID", "canonical worker output schema is unavailable")
    return {
        "schema": "workflow/worker-launch-v3",
        "assignment_path": str(assignment_path.resolve()),
        "assignment_digest": assignment["assignment_digest"],
        "basis_sha256": assignment.get("basis_sha256"),
        "scope_cwd": assignment["scope_cwd"],
        "support_write_roots": _support_write_roots(assignment, recovery=recovery_attempt is not None),
        "context_policy": "fresh-assignment-only",
        "recovery_attempt": recovery_attempt,
        "worker_phases": assignment["worker_phases"],
        "health_policy": assignment["health_policy"],
        "runtime_commands": runtime_commands(assignment_path, assignment),
        "result_check_command": [sys.executable, str(assignment["execution_helper"]),
                                 "check-result", "--assignment", str(assignment_path.resolve()),
                                 "--result", "<result-json>"],
        "output_schema_path": str(schema),
        "output_schema_sha256": hashlib.sha256(schema.read_bytes()).hexdigest(),
        "prompt": worker_prompt(assignment_path, assignment, recovery_attempt=recovery_attempt),
    }
