"""Agentic SDLC adapter for owner-validated objective impact evidence."""

from __future__ import annotations

from contextlib import contextmanager
import json
import hashlib
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Agentic SDLC currently targets POSIX hosts.
    fcntl = None  # type: ignore[assignment]


SHARED_PROJECT_SPEC_SCRIPTS = (
    Path(__file__).resolve().parents[2] / "maintain-project-specs" / "scripts"
)
if str(SHARED_PROJECT_SPEC_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SHARED_PROJECT_SPEC_SCRIPTS))

from project_specs_lib.contracts import (  # noqa: E402
    ProjectSpecError,
    _read_file as owner_read_file,
    canonical_digest,
)
from project_specs_lib.impact import (  # noqa: E402
    CLAIM_SCHEMA as SHARED_CLAIM_SCHEMA,
    public_impact_status,
    validate_objective_impact as validate_shared_objective_impact,
)

EXECUTION_SCRIPTS = Path(__file__).resolve().parents[2] / "worktree" / "scripts"
if str(EXECUTION_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(EXECUTION_SCRIPTS))
from workflow_spec_checkout import SpecCheckoutError, resolve_spec_checkout  # noqa: E402


CLAIM_SCHEMA = "workflow/objective-impact-claim-v1"
RECEIPT_SCHEMA = "workflow/objective-impact-receipt-v1"
LEDGER_SCHEMA = "workflow/objective-impact-ledger-v1"
EXECUTION_BASIS_SCHEMA = "workflow/objective-impact-execution-basis-v1"
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
REVISION_RE = re.compile(r"r[0-9]{4}\Z")
FEATURE_RE = re.compile(r"FEAT-[0-9]{3,}\Z")
ATTEMPT_RE = re.compile(r"attempt-([0-9]{4,12})\.json\Z")
MAX_GENERATION = 999_999_999_999


class ObjectiveImpactError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def claim_path(run_dir: Path) -> Path:
    return run_dir / "objective-impact-claim.json"


def _derive_objective_impact(
    project_root: Path, *, claim: dict[str, object], **arguments: object
) -> dict[str, object]:
    """Use the pure shared validator without inheriting lifecycle-owned schemas."""

    shared_claim = {**claim, "schema": SHARED_CLAIM_SCHEMA}
    receipt = validate_shared_objective_impact(
        project_root,
        workflow=str(arguments.pop("workflow")),
        claim=shared_claim,
        **arguments,
    )
    return {**receipt, "schema": RECEIPT_SCHEMA}


def _root(run_dir: Path) -> Path:
    return run_dir / "objective-impact"


def _require_regular(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise ObjectiveImpactError("OBJECTIVE_IMPACT_REQUIRED", f"{label} is missing") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size > 4 * 1024 * 1024
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
    ):
        raise ObjectiveImpactError("RUN_STATE_INVALID", f"{label} is unsafe")


def _read(path: Path, label: str) -> dict[str, Any]:
    _require_regular(path, label)
    metadata = path.lstat()
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ObjectiveImpactError("RUN_STATE_INVALID", f"{label} is unsafe") from error
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise ObjectiveImpactError(
                "CONCURRENT_MODIFICATION", f"{label} changed while opening"
            )
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > 4 * 1024 * 1024:
                raise ObjectiveImpactError("RUN_STATE_INVALID", f"{label} is too large")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) != (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        ):
            raise ObjectiveImpactError(
                "CONCURRENT_MODIFICATION", f"{label} changed while reading"
            )
    finally:
        os.close(descriptor)
    try:
        value = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ObjectiveImpactError("RUN_STATE_INVALID", f"{label} is invalid") from error
    if not isinstance(value, dict):
        raise ObjectiveImpactError("RUN_STATE_INVALID", f"{label} is invalid")
    return value


def _stable(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise ObjectiveImpactError("RUN_STATE_INVALID", "objective impact directory is unsafe")
    if hasattr(os, "getuid") and path.stat().st_uid != os.getuid():
        raise ObjectiveImpactError("RUN_STATE_INVALID", "objective impact directory is unsafe")
    path.chmod(0o700)


def _atomic(path: Path, value: object) -> None:
    _ensure_dir(path.parent)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_stable(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _exclusive(path: Path, value: object) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_stable(value))
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    path.chmod(0o600)


@contextmanager
def _publication_lock(run_dir: Path):
    if fcntl is None:
        raise ObjectiveImpactError(
            "ENVIRONMENT_BLOCKER", "objective impact publication requires a POSIX host"
        )
    root = _root(run_dir)
    _ensure_dir(root)
    path = root / "publication.lock"
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise ObjectiveImpactError(
            "RUN_STATE_INVALID", "objective impact publication lock is unsafe"
        ) from error
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (hasattr(os, "getuid") and opened.st_uid != os.getuid())
        ):
            raise ObjectiveImpactError(
                "RUN_STATE_INVALID", "objective impact publication lock is unsafe"
            )
        os.fchmod(descriptor, 0o600)
        deadline = time.monotonic() + 10
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ObjectiveImpactError(
                        "WORKSPACE_BUSY", "another objective impact publication is active"
                    )
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _ledger_fingerprint(run_dir: Path) -> str | None:
    path = _root(run_dir) / "ledger.json"
    if not path.exists():
        return None
    return canonical_digest(_read(path, "objective impact ledger"))


def _max_attempt_generation(root: Path) -> int:
    maximum = 0
    for path in root.iterdir():
        if not path.name.startswith("attempt-"):
            continue
        match = ATTEMPT_RE.fullmatch(path.name)
        if match is None:
            raise ObjectiveImpactError("RUN_STATE_INVALID", "impact attempt name is invalid")
        _require_regular(path, "objective impact attempt")
        maximum = max(maximum, int(match.group(1)))
    if maximum >= MAX_GENERATION:
        raise ObjectiveImpactError("RUN_STATE_INVALID", "impact generation is exhausted")
    return maximum


def _publish_ledger_cas(
    run_dir: Path, expected_fingerprint: str | None, ledger: dict[str, object]
) -> None:
    if _ledger_fingerprint(run_dir) != expected_fingerprint:
        raise ObjectiveImpactError(
            "CONCURRENT_MODIFICATION", "objective impact ledger changed during publication"
        )
    _atomic(_root(run_dir) / "ledger.json", ledger)


def load_current(
    run_dir: Path, *, required: bool = False
) -> tuple[dict[str, Any], str] | None:
    root = _root(run_dir)
    ledger_path = root / "ledger.json"
    if not ledger_path.exists():
        if required:
            raise ObjectiveImpactError(
                "OBJECTIVE_IMPACT_REQUIRED",
                "the active run predates objective impact receipts and requires a fresh accepted objective",
            )
        return None
    if root.is_symlink() or not root.is_dir() or stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise ObjectiveImpactError("RUN_STATE_INVALID", "objective impact directory is unsafe")
    ledger = _read(ledger_path, "objective impact ledger")
    if (
        set(ledger) != {"schema", "workflow", "current"}
        or ledger.get("schema") != LEDGER_SCHEMA
        or ledger.get("workflow") != _read(run_dir / "run.json", "run").get("owner")
        or not isinstance(ledger.get("current"), dict)
    ):
        raise ObjectiveImpactError("RUN_STATE_INVALID", "objective impact ledger is invalid")
    current = dict(ledger["current"])
    generation = current.get("generation")
    name = f"attempt-{generation:04d}.json" if isinstance(generation, int) else None
    if (
        set(current) != {"generation", "revision", "path", "sha256"}
        or not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 1
        or generation > MAX_GENERATION
        or current.get("path") != name
        or REVISION_RE.fullmatch(str(current.get("revision") or "")) is None
        or SHA256_RE.fullmatch(str(current.get("sha256") or "")) is None
    ):
        raise ObjectiveImpactError("RUN_STATE_INVALID", "objective impact pointer is invalid")
    receipt = _read(root / str(name), "objective impact attempt")
    receipt_sha256 = canonical_digest(receipt)
    if (
        receipt.get("schema") != RECEIPT_SCHEMA
        or receipt.get("workflow") != ledger["workflow"]
        or receipt.get("generation") != generation
        or receipt.get("revision") != current.get("revision")
        or receipt_sha256 != current.get("sha256")
    ):
        raise ObjectiveImpactError("RUN_STATE_INVALID", "objective impact attempt is invalid")
    return receipt, receipt_sha256


def publish(
    project_root: Path,
    run_dir: Path,
    binding: dict[str, object],
    refinement: dict[str, object],
) -> tuple[dict[str, Any], str]:
    with _publication_lock(run_dir):
        return _publish_locked(spec_checkout(run_dir, project_root), run_dir, binding, refinement)


def spec_checkout(
    run_dir: Path, project_root: Path, *, feature_id: str | None = None,
    strict_head: bool = True,
) -> Path:
    try:
        return resolve_spec_checkout(
            run_dir, project_root, feature_id=feature_id, strict_head=strict_head,
        )
    except SpecCheckoutError as error:
        raise ObjectiveImpactError(error.code, str(error)) from error


def _publish_locked(
    project_root: Path,
    run_dir: Path,
    binding: dict[str, object],
    refinement: dict[str, object],
) -> tuple[dict[str, Any], str]:
    revisions = binding.get("revisions")
    latest = revisions[-1] if isinstance(revisions, list) and revisions else None
    if not isinstance(latest, dict):
        raise ObjectiveImpactError("RUN_STATE_INVALID", "objective binding has no latest revision")
    claim = _read(claim_path(run_dir), "objective impact claim")
    if claim.get("schema") != CLAIM_SCHEMA:
        raise ObjectiveImpactError("RUN_STATE_INVALID", "objective impact claim schema is invalid")
    expected_ledger = _ledger_fingerprint(run_dir)
    current = load_current(run_dir, required=False)
    prior = current[0] if current is not None else None
    prior_sha256 = current[1] if current is not None else None
    if prior is not None:
        prior_transition = prior.get("spec_transition")
        retry_prior_spec = (
            prior_transition.get("prior_spec_receipt_sha256")
            if isinstance(prior_transition, dict)
            else prior.get("spec_receipt_sha256")
        )
        try:
            retry = _derive_objective_impact(
                project_root,
                workflow=str(binding["owner"]),
                objective_id=str(binding["objective_id"]),
                revision=str(latest["revision"]),
                objective_sha256=str(latest["sha256"]),
                intent_sha256=str(latest.get("intent_sha256") or latest["sha256"]),
                refinement=refinement,
                claim=claim,
                prior_impact_sha256=prior.get("prior_impact_sha256"),
                prior_spec_receipt_sha256=retry_prior_spec,
                generation=int(prior["generation"]),
            )
        except ProjectSpecError as error:
            raise ObjectiveImpactError("OBJECTIVE_IMPACT_REQUIRED", error.message) from error
        if canonical_digest(retry) == prior_sha256:
            if _ledger_fingerprint(run_dir) != expected_ledger:
                raise ObjectiveImpactError(
                    "CONCURRENT_MODIFICATION",
                    "objective impact ledger changed during publication",
                )
            return prior, str(prior_sha256)
    root = _root(run_dir)
    _ensure_dir(root)
    generation = 1 if prior is None else int(prior["generation"]) + 1
    maximum = _max_attempt_generation(root)
    while True:
        try:
            receipt = _derive_objective_impact(
                project_root,
                workflow=str(binding["owner"]),
                objective_id=str(binding["objective_id"]),
                revision=str(latest["revision"]),
                objective_sha256=str(latest["sha256"]),
                intent_sha256=str(latest.get("intent_sha256") or latest["sha256"]),
                refinement=refinement,
                claim=claim,
                prior_impact_sha256=prior_sha256,
                prior_spec_receipt_sha256=(
                    prior.get("spec_receipt_sha256") if prior is not None else None
                ),
                generation=generation,
            )
        except ProjectSpecError as error:
            raise ObjectiveImpactError("OBJECTIVE_IMPACT_REQUIRED", error.message) from error
        name = f"attempt-{generation:04d}.json"
        attempt_path = root / name
        receipt_sha256 = canonical_digest(receipt)
        if not attempt_path.exists():
            try:
                _exclusive(attempt_path, receipt)
            except FileExistsError:
                maximum = _max_attempt_generation(root)
                generation = maximum + 1
                continue
            else:
                break
        existing = _read(attempt_path, "objective impact attempt")
        if canonical_digest(existing) == receipt_sha256:
            receipt = existing
            break
        generation = maximum + 1
    receipt_sha256 = canonical_digest(receipt)
    _publish_ledger_cas(
        run_dir,
        expected_ledger,
        {
            "schema": LEDGER_SCHEMA,
            "workflow": binding["owner"],
            "current": {
                "generation": generation,
                "revision": receipt["revision"],
                "path": name,
                "sha256": receipt_sha256,
            },
        },
    )
    return receipt, receipt_sha256


def verify_current(
    run_dir: Path, project_root: Path, *, feature_id: str | None = None
) -> tuple[dict[str, Any], str]:
    current = load_current(run_dir, required=True)
    assert current is not None
    receipt, receipt_sha256 = current
    binding = _read(run_dir / "objective.json", "objective binding")
    revisions = binding.get("revisions")
    latest = revisions[-1] if isinstance(revisions, list) and revisions else None
    if (
        not isinstance(latest, dict)
        or binding.get("objective_id") != receipt.get("objective_id")
        or latest.get("revision") != receipt.get("revision")
        or (latest.get("intent_sha256") or latest.get("sha256"))
        != receipt.get("intent_sha256")
    ):
        raise ObjectiveImpactError(
            "OBJECTIVE_IMPACT_REQUIRED", "the latest accepted prompt has no impact settlement"
        )
    # Execution callers retain exact HEAD/journal recovery checks at each
    # transition; this read only selects the registered spec ownership boundary.
    project_root = spec_checkout(run_dir, project_root, feature_id=feature_id, strict_head=False)
    for kind in ("requirements", "design"):
        path = project_root / "docs" / f"{kind}.md"
        if receipt.get(f"{kind}_sha256") == "absent" and not path.exists() and not path.is_symlink():
            continue
        try:
            raw, _text = owner_read_file(path, f"{kind} specification")
        except ProjectSpecError as error:
            raise ObjectiveImpactError("SPEC_CONFLICT", error.message) from error
        if hashlib.sha256(raw).hexdigest() != receipt.get(f"{kind}_sha256"):
            raise ObjectiveImpactError(
                "REPLAN_REQUIRED", "canonical project specs drifted after impact settlement"
            )
    return receipt, receipt_sha256


def _basis_path(run_dir: Path, feature_id: str) -> Path:
    if FEATURE_RE.fullmatch(feature_id) is None:
        raise ObjectiveImpactError("RUN_STATE_INVALID", "feature ID is invalid")
    return _root(run_dir) / "execution" / f"{feature_id}.json"


def _identity_files(project_root: Path) -> dict[str, str]:
    """Freeze effective repository instructions and the execution helper closure."""
    sources = {path for path in Path(__file__).parent.glob("*.py") if not path.name.startswith("test")}
    catalog = Path(__file__).resolve().parents[2]
    for dependency in ("maintain-project-specs", "global-context-management"):
        sources.update(path for path in (catalog / dependency / "scripts").rglob("*.py") if not path.name.startswith("test"))
    for directory, children, filenames in os.walk(project_root):
        children[:] = [name for name in children if name not in {".git", "node_modules", ".venv", "venv", "__pycache__"}]
        chosen = "AGENTS.override.md" if "AGENTS.override.md" in filenames else "AGENTS.md"
        if chosen in filenames:
            sources.add(Path(directory) / chosen)
    for owner in (
        "implement", "sdlc-start", "sdlc-prepare-execution", "sdlc-implement-plan",
        "global-context-management", "maintain-project-specs", "code-review", "align",
    ):
        sources.add(catalog / owner / "SKILL.md")
    # These mandatory execution inputs live outside the Python helper trees.
    # Enumerate them so deletion is invalid, rather than silently disappearing
    # from a glob, and avoid binding unrelated catalog documentation.
    for relative in (
        "implement/references/implementation-loop.md",
        "implement/references/objective-contract.md",
        "global-context-management/references/agent-hosts.md",
        "maintain-project-specs/references/lifecycle.md",
        "code-review/references/quality-rubric.md",
        "align/references/quality-gate.md",
        "worktree/references/workflow-contract.md",
        "worktree/assets/worker-result.schema.json",
        "sdlc-start/references/state-schema.md",
        "sdlc-prepare-execution/references/execution-plane.md",
    ):
        sources.add(catalog / relative)
    for parent in (project_root, *project_root.parents):
        override = parent / "AGENTS.override.md"
        normal = parent / "AGENTS.md"
        chosen = override if override.exists() else normal
        if chosen.exists():
            sources.add(chosen)
    identities = {}
    for path in sorted(sources):
        if path.is_symlink() or not path.is_file():
            raise ObjectiveImpactError("BASIS_INVALID", "an instruction/helper identity is unsafe")
        identities[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return identities


def _execution_basis(run_dir, feature_id, plan_digest, project_root, receipt, receipt_sha256):
    from workflow_objectives import validate_run
    run, objective = validate_run(run_dir)
    plans = [path for path in (run_dir / "plans").rglob("*.md")
             if path.is_file() and not path.is_symlink()
             and hashlib.sha256(path.read_bytes()).hexdigest() == plan_digest]
    if len(plans) != 1:
        raise ObjectiveImpactError("BASIS_INVALID", "one immutable locked plan must match the execution digest")
    latest = objective["revisions"][-1]
    from workflow_state import read
    prepared = read(run_dir / "prepared-bases" / (feature_id + ".json"))
    if prepared.get("schema") != "workflow/prepared-base-v1" or prepared.get("git_common_dir") != run["git_common_dir"]:
        raise ObjectiveImpactError("BASIS_INVALID", "prepared Git base identity is invalid")
    inputs = run_dir / "input-dependencies.json"
    return {
        "schema": EXECUTION_BASIS_SCHEMA, "owner": run["owner"],
        "feature_id": feature_id, "objective_id": objective["objective_id"],
        "plan_digest": plan_digest, "plan_path": str(plans[0]),
        "plan_basis_revision": latest["revision"], "latest_settled_revision": latest["revision"],
        "objective_sha256": latest["sha256"], "impact_sha256": receipt_sha256,
        "spec_receipt_sha256": receipt["spec_receipt_sha256"],
        "spec_transition_sha256": receipt.get("spec_transition_sha256"),
        "plan_action": receipt["plan_action"], "base_commit": prepared["base_head"],
        "input_dependencies_sha256": canonical_digest(read(inputs)) if inputs.exists() else None,
        "ownership": {key: run[key] for key in ("owner", "run_id", "project_root", "git_common_dir")},
        "identities": _identity_files(project_root),
        # Task ownership and validation requirements are part of these exact
        # locked plan bytes, and each immutable assignment binds this basis.
        "assignment_plan_sha256": plan_digest, "validation_plan_sha256": plan_digest,
    }


def settle_execution(run_dir: Path, feature_id: str, plan_digest: str, project_root: Path,
                     allow_basis_creation: bool = False) -> dict[str, object]:
    from workflow_objectives import _writers_quiescent, validate_run
    from workflow_state import write, read
    if SHA256_RE.fullmatch(plan_digest) is None:
        raise ObjectiveImpactError("RUN_STATE_INVALID", "execution plan digest is invalid")
    spec_checkout(run_dir, project_root, feature_id=feature_id)
    receipt, receipt_sha256 = verify_current(run_dir, project_root)
    path = _basis_path(run_dir, feature_id)
    existing = _read(path, "execution basis pointer") if path.exists() else None
    value = _execution_basis(run_dir, feature_id, plan_digest, project_root, receipt, receipt_sha256)
    fingerprint = canonical_digest(value)
    if existing is not None and existing.get("sha256") == fingerprint:
        return value
    _writers_quiescent(run_dir)
    run, objective = validate_run(run_dir)
    if existing is not None:
        prior = read(run_dir / existing["path"])
        if (prior["plan_digest"] == plan_digest and receipt["plan_action"] == "replan_required"
                and prior["latest_settled_revision"] != receipt["revision"]):
            raise ObjectiveImpactError("REPLAN_REQUIRED", "material steering requires a distinct immutable plan")
    elif not allow_basis_creation:
        raise ObjectiveImpactError("BASIS_REQUIRED", "prepared execution requires an explicit initial basis")
    relative = f"bases/{feature_id}/{fingerprint}.json"
    immutable = run_dir / relative
    if immutable.exists():
        if read(immutable) != value:
            raise ObjectiveImpactError("BASIS_INVALID", "immutable execution basis was changed")
    else:
        write(immutable, value, exclusive=True)
    _atomic(path, {"schema": "workflow/execution-basis-pointer-v1", "path": relative, "sha256": fingerprint})
    return value


def verify_execution(run_dir: Path, feature_id: str, plan_digest: str, project_root: Path, *, allow_fenced: bool = False) -> dict[str, object]:
    from workflow_objectives import check_dispatch
    from workflow_state import read
    if not allow_fenced:
        check_dispatch(run_dir, allow_complete=True)
    receipt, receipt_sha256 = verify_current(run_dir, project_root, feature_id=feature_id)
    pointer = _read(_basis_path(run_dir, feature_id), "execution basis pointer")
    fingerprint = pointer.get("sha256")
    if (pointer.get("schema") != "workflow/execution-basis-pointer-v1"
            or not isinstance(fingerprint, str) or SHA256_RE.fullmatch(fingerprint) is None
            or pointer.get("path") != f"bases/{feature_id}/{fingerprint}.json"):
        raise ObjectiveImpactError("BASIS_INVALID", "execution basis pointer is invalid")
    value = read(run_dir / pointer["path"])
    current = _execution_basis(run_dir, feature_id, plan_digest, project_root, receipt, receipt_sha256)
    if canonical_digest(value) != fingerprint or value != current:
        raise ObjectiveImpactError("REPLAN_REQUIRED", "immutable execution basis is stale")
    return value


def public_execution_bases(run_dir: Path) -> list[dict[str, str]]:
    directory = _root(run_dir) / "execution"
    if not directory.exists():
        return []
    if directory.is_symlink() or not directory.is_dir():
        raise ObjectiveImpactError("RUN_STATE_INVALID", "impact execution directory is unsafe")
    rows: list[dict[str, str]] = []
    for path in sorted(directory.glob("FEAT-*.json")):
        pointer = _read(path, "execution basis pointer")
        value = _read(run_dir / str(pointer["path"]), "execution basis")
        feature_id = str(value.get("feature_id") or "")
        revision = str(value.get("plan_basis_revision") or "")
        if FEATURE_RE.fullmatch(feature_id) is None or REVISION_RE.fullmatch(revision) is None:
            raise ObjectiveImpactError("RUN_STATE_INVALID", "impact execution basis is invalid")
        rows.append({"feature_id": feature_id, "plan_basis_revision": revision})
    return rows


__all__ = [
    "CLAIM_SCHEMA",
    "ObjectiveImpactError",
    "claim_path",
    "load_current",
    "publish",
    "public_impact_status",
    "public_execution_bases",
    "settle_execution",
    "verify_current",
    "verify_execution",
]
