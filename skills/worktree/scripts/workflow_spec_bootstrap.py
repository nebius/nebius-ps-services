"""Journaled initial specification commit in Implement's integration checkout."""

from pathlib import Path
import hashlib
import subprocess


def verify_source_specs(repo: Path, scope: str, base: str) -> None:
    import workflow_execution as engine

    paths = [str(Path(scope) / "docs" / f"{kind}.md") for kind in ("requirements", "design")]
    changed = engine.git(repo, ["diff", "--name-only", "--no-renames", base, "--", *paths],
                         "inspect implicit canonical spec inputs")
    untracked = engine.git(repo, ["ls-files", "--others", "--", *paths],
                           "inspect untracked canonical spec inputs")
    if changed or untracked:
        raise engine.ExecutionError("UNCOMMITTED_INPUT", "canonical spec inputs differ from the accepted committed source")


def _publication_receipt(run_dir, project, repo, parent, hashes, objective):
    """Read the bounded paired-publisher journal in this run's native home."""
    import workflow_execution as engine
    from project_specs_lib.contracts import _read_file
    import json

    home = run_dir.parents[2]
    workspace = f"{repo.name}-{hashlib.sha256(str(repo).encode()).hexdigest()[:12]}"
    scope = hashlib.sha256(str(project).encode()).hexdigest()[:16]
    root = home / "project-specs" / workspace / "transactions" / scope
    engine._reject_symlink_components(root, "paired publication journal")
    paths = sorted(root.glob("*.json"))
    if len(paths) > 256:
        raise engine.ExecutionError("SPEC_CONFLICT", "too many specification publication receipts")
    total = 0
    for path in paths:
        raw, text = _read_file(path, "specification publication receipt", max_bytes=16 * 1024)
        total += len(raw)
        if total > 1024 * 1024:
            raise engine.ExecutionError("SPEC_CONFLICT", "specification receipt inventory exceeds its bound")
        receipt = json.loads(text)
        if (isinstance(receipt, dict)
                and receipt.get("schema") == "maintain-project-specs.transaction.v1"
                and receipt.get("git_head") == parent
                and receipt.get("project_scope") == project.relative_to(repo).as_posix()
                and receipt.get("after") == hashes
                and receipt.get("workflow_identity") == {
                    "objective_sha256": objective, "basis_sha256": None}):
            return hashlib.sha256(raw).hexdigest()
    raise engine.ExecutionError("SPEC_CONFLICT", "initial specs require their bound paired-publication receipt")


def _verify_committed_pair(repo, project):
    import workflow_execution as engine
    from project_specs_lib.contracts import _read_file

    for kind in ("requirements", "design"):
        path = project / "docs" / f"{kind}.md"
        if not path.exists() and not path.is_symlink():
            continue
        raw, _text = _read_file(path, kind)
        relative = path.relative_to(repo).as_posix()
        result = engine._run(["git", "rev-parse", "--verify", f"HEAD:{relative}"], repo,
                             "verify committed canonical spec", check=False)
        fingerprint = subprocess.run(["git", "hash-object", "--no-filters", "--stdin"],
                                     cwd=repo, input=raw, capture_output=True, timeout=30, check=False)
        if (result.returncode or fingerprint.returncode
                or result.stdout.strip().encode("ascii") != fingerprint.stdout.strip()):
            raise engine.ExecutionError("UNCOMMITTED_INPUT", "canonical specs must match committed integration blobs")


def seal_specifications(run_dir, feature, coordinator):
    """Commit only the published canonical pair; retry only the journaled tree."""
    import workflow_execution as engine
    from workflow_state import read, write
    from workflow_spec_checkout import resolve_spec_checkout
    from project_specs_lib.contracts import _read_file, inspect_pair_bytes

    run, objective = engine.check_dispatch(run_dir)
    accepted = objective["revisions"][-1]["sha256"]
    if coordinator.get("bootstrap_objective_sha256") != accepted:
        raise engine.ExecutionError("REPLAN_REQUIRED", "bootstrap objective changed")
    repo = Path(coordinator["integration_worktree"])
    selected = Path(coordinator["selected_project_root"])
    project = resolve_spec_checkout(run_dir, selected, feature_id=feature, strict_head=False)
    verify_source_specs(Path(coordinator["project_root"]), coordinator["project_scope"], run["source_head"])
    parent = coordinator["integration_head"]
    current = engine.head(repo)
    intent_path = run_dir / "execution" / feature / "spec-bootstrap.json"
    allowed = {str(Path(coordinator["project_scope"]) / "docs" / f"{kind}.md")
               for kind in ("requirements", "design")}
    changed = set()
    for command in (["diff", "--name-only", "--no-renames", "-z", parent],
                    ["ls-files", "--others", "--exclude-standard", "-z"]):
        changed.update(engine._run(["git", *command], repo, "inspect bootstrap changes").stdout.split("\0"))
    changed.discard("")
    if changed - allowed:
        raise engine.ExecutionError("WORKER_SCOPE_VIOLATION", "specification bootstrap contains unrelated integration changes")
    if not intent_path.exists() and current == parent and not changed:
        _verify_committed_pair(repo, project)
        coordinator["status"] = "prepared"
        engine._save_coordinator(run_dir, feature, coordinator)
        return coordinator
    documents = {kind: _read_file(project / "docs" / f"{kind}.md", kind)[0]
                 for kind in ("requirements", "design")}
    inspect_pair_bytes(documents["requirements"], documents["design"])
    hashes = {kind: hashlib.sha256(raw).hexdigest() for kind, raw in documents.items()}
    message = "chore: establish implementation specifications"
    identity = {"schema": "workflow/spec-bootstrap-commit-v1", "parent": parent,
                "objective_sha256": accepted, "plan_digest": coordinator["plan_digest"],
                "specifications": hashes, "message": message}
    receipt = _publication_receipt(run_dir, project, repo, parent, hashes, accepted)
    if intent_path.exists():
        intent = read(intent_path)
        if (any(intent.get(key) != value for key, value in identity.items())
                or intent.get("publication_receipt_sha256") != receipt):
            raise engine.ExecutionError("WORKTREE_CONFLICT", "specification bootstrap intent changed")
    else:
        if current != parent:
            raise engine.ExecutionError("WORKTREE_CONFLICT", "unjournaled specification commit")
        engine.git(repo, ["add", "-A"], "stage initial canonical specification pair")
        engine._verify_staged_integration(repo, coordinator["project_scope"])
        intent = {**identity, "publication_receipt_sha256": receipt,
                  "tree": engine.git(repo, ["write-tree"], "freeze specification tree")}
        write(intent_path, intent, exclusive=True)
    if current == parent:
        if engine.git(repo, ["write-tree"], "verify staged specification tree") != intent["tree"]:
            raise engine.ExecutionError("WORKTREE_CONFLICT", "staged specification tree differs from the journal")
        engine._verify_staged_integration(repo, coordinator["project_scope"])
        engine.git(repo, ["commit", "-m", message], "commit initial canonical specifications")
        current = engine.head(repo)
    if (engine.git(repo, ["rev-list", "--parents", "-n", "1", current], "verify specification parent").split()
            != [current, parent]
            or engine.git(repo, ["show", "-s", "--format=%s", current], "verify specification message") != message
            or engine.git(repo, ["rev-parse", f"{current}^{{tree}}"], "verify specification tree") != intent["tree"]
            or not engine.clean(repo)):
        raise engine.ExecutionError("WORKTREE_CONFLICT", "specification commit differs from the journaled direct child")
    engine._verify_integration_commit(repo, parent, current, coordinator["project_scope"])
    _verify_committed_pair(repo, project)
    coordinator.update(integration_head=current, status="prepared")
    engine._save_coordinator(run_dir, feature, coordinator)
    engine.append_journal(engine.journal_path(run_dir, feature, "coordinator"),
                          {"event": "specs_sealed", "head": current, "tree": intent["tree"]})
    return coordinator
