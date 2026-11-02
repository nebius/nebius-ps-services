"""Journaled initial contract commits at each workflow's authoritative checkout."""

from pathlib import Path
import hashlib
import subprocess

from workflow_state import git_environment


def verify_source_specs(repo: Path, scope: str, base: str) -> None:
    import workflow_execution as engine

    paths = [str(Path(scope) / "docs" / f"{kind}.md") for kind in ("requirements", "design")]
    changed = engine.git(repo, ["diff", "--name-only", "--no-renames", base, "--", *paths],
                         "inspect implicit canonical spec inputs")
    untracked = engine.git(repo, ["ls-files", "--others", "--", *paths],
                           "inspect untracked canonical spec inputs")
    if changed or untracked:
        raise engine.ExecutionError("UNCOMMITTED_INPUT", "canonical spec inputs differ from the accepted committed source")


def _publication_receipt(run_dir, project, repo, parent, hashes, objective, *, pre_execution=False):
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
                and (receipt.get("workflow_identity") == {
                    "objective_sha256": objective, "basis_sha256": None}
                     or pre_execution and "workflow_identity" not in receipt)):
            # Before SDLC registers an execution owner, paired publication is
            # project-scoped. Preparation independently verifies current impact
            # and binds these exact published bytes to its accepted objective.
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
                                     cwd=repo, input=raw, capture_output=True, timeout=30, check=False, env=git_environment(repo))
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
    project = resolve_spec_checkout(run_dir, selected, execution_id=feature, strict_head=False)
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


def _seal_sdlc_contract(run_dir, project, execution_id, plan_digest, anchor):
    """Journal one admitted source contract before freezing SDLC's clean base."""
    import workflow_execution as engine
    from git_promotion import promotion_lock
    from workflow_state import read, write, digest
    from workflow_program import accepted, validate_bundle
    from workflow_interop import validate_contract_lease, record_promotion
    from project_specs_lib.contracts import _read_file, inspect_pair_bytes
    from project_specs_lib.source_bundle import read_source

    run, latest, snapshot = accepted(run_dir)
    repo = engine.git_root(project)
    scope = project.relative_to(repo).as_posix()
    if (run["owner"] != "agentic-sdlc" or str(project) != run["project_root"]
            or str(repo) != run["git_root"] or str(engine.git_common_dir(repo)) != run["git_common_dir"]
            or anchor.get("status") not in {"managed", "unmanaged"}):
        raise engine.ExecutionError("OWNERSHIP_INVALID", "contract source differs from accepted SDLC scope")
    intent_path = run_dir / "contract-commits" / (execution_id + ".json")
    result_path = run_dir / "contract-commits" / (execution_id + ".result.json")
    allowed = {str(Path(scope) / "docs" / f"{kind}.md") for kind in ("requirements", "design")}
    if snapshot["inputs"] is not None:
        bundle = validate_bundle(project, snapshot["inputs"]["manifest"], snapshot["inputs"]["manifest_sha256"])
        allowed.update(str(Path(scope) / path) for path in bundle["paths"])

    def changed_paths():
        paths = set()
        for args in (["diff", "--name-only", "--no-renames", "-z"],
                     ["diff", "--cached", "--name-only", "--no-renames", "-z"],
                     ["ls-files", "--others", "--exclude-standard", "-z"]):
            paths.update(filter(None, engine._run(["git", *args], repo, "inspect contract paths").stdout.split("\0")))
        return paths

    def files():
        values = {}
        for path in sorted(allowed):
            ignored = engine._run(["git", "check-ignore", "--no-index", "-q", "--", path], repo,
                                  "inspect contract ignore rules", check=False)
            if ignored.returncode != 1:
                raise engine.ExecutionError("WORKTREE_CONFLICT", "contract paths must not be ignored")
            values[path] = hashlib.sha256(read_source(repo, path)).hexdigest()
        return values

    def verify_index(expected):
        if changed_paths() - allowed or files() != expected:
            raise engine.ExecutionError("WORKTREE_CONFLICT", "contract paths or bytes changed during preparation")
        engine._verify_staged_integration(repo, scope)
        for path, fingerprint in expected.items():
            blob = subprocess.run(["git", "show", ":" + path], cwd=repo, capture_output=True,
                                  timeout=30, check=False, env=git_environment(repo))
            mode = engine.git(repo, ["ls-files", "--stage", "--", path], "inspect contract file mode").partition(" ")[0]
            if blob.returncode or mode != "100644" or hashlib.sha256(blob.stdout).hexdigest() != fingerprint:
                raise engine.ExecutionError("WORKTREE_CONFLICT", "contract index differs from admitted regular files")

    with promotion_lock(repo):
        current = engine.head(repo)
        changed = changed_paths()
        if changed - allowed:
            raise engine.ExecutionError("WORKTREE_CONFLICT", "contract preparation contains unrelated repository changes")
        existing_intent = read(intent_path) if intent_path.exists() else None
        branch = engine.branch(repo)
        predecessor = existing_intent.get("parent") if existing_intent is not None else current
        sources = {(run["source_branch"], run["source_head"])}
        # Later deliveries have their own verified source transaction. Ordinary
        # features may build only on an exact completed promotion from this run.
        source_path = run_dir / "delivery" / execution_id / "source.json"
        if snapshot["inputs"] is not None and source_path.exists():
            source = read(source_path)  # prepare_source already verified delivery ancestry.
            sources.add((source["branch"], source["head"]))
        for path in (run_dir / "execution").glob("*/coordinator.json"):
            prior = engine._load_coordinator(run_dir, path.parent.name)
            if (prior.get("status") == "done" and prior.get("run_id") == run_dir.name
                    and prior.get("selected_project_root") == str(project)
                    and prior.get("git_common_dir") == str(engine.git_common_dir(repo))
                    and prior.get("promoted_head") is not None):
                sources.add((prior["base_branch"], prior["promoted_head"]))
        if (branch, predecessor) not in sources:
            raise engine.ExecutionError("SOURCE_DRIFT", "contract source is not an accepted or completed workflow checkpoint")
        if existing_intent is None and not changed:
            _verify_committed_pair(repo, project)
            return
        parent = predecessor
        admitted_files = files()
        if snapshot["inputs"] is not None:
            validated = validate_bundle(project, snapshot["inputs"]["manifest"], snapshot["inputs"]["manifest_sha256"])
            if {str(Path(scope) / path) for path in validated["paths"]} - allowed:
                raise engine.ExecutionError("WORKTREE_CONFLICT", "contract bundle scope changed")
        documents = {kind: _read_file(project / "docs" / f"{kind}.md", kind)[0]
                     for kind in ("requirements", "design")}
        inspect_pair_bytes(documents["requirements"], documents["design"])
        hashes = {kind: hashlib.sha256(raw).hexdigest() for kind, raw in documents.items()}
        receipt = _publication_receipt(run_dir, project, repo, parent, hashes, latest["sha256"], pre_execution=True)
        if (any(hashes[kind] != admitted_files[str(Path(scope) / "docs" / f"{kind}.md")]
                for kind in hashes) or files() != admitted_files):
            raise engine.ExecutionError("WORKTREE_CONFLICT", "published contract bytes changed during validation")
        identity = {"schema": "workflow/sdlc-contract-commit-v1", "run_id": run_dir.name,
                    "execution_id": execution_id, "parent": parent, "branch": branch,
                    "git_common_dir": str(engine.git_common_dir(repo)), "project_scope": scope,
                    "objective_sha256": latest["sha256"], "plan_digest": plan_digest,
                    "files": admitted_files, "publication_receipt_sha256": receipt,
                    "message": "chore: establish SDLC contract"}
        if intent_path.exists():
            intent = read(intent_path)
            if (set(intent) != set(identity) | {"tree", "sha256"}
                    or any(intent.get(key) != value for key, value in identity.items())
                    or intent["sha256"] != digest({key: value for key, value in intent.items() if key != "sha256"})):
                raise engine.ExecutionError("WORKTREE_CONFLICT", "contract commit intent changed")
        else:
            if current != parent:
                raise engine.ExecutionError("SOURCE_DRIFT", "unjournaled contract source HEAD")
            if anchor["status"] == "managed":
                engine._interop("contract ownership", validate_contract_lease, run_dir, project, parent)
            engine.git(repo, ["add", "-A"], "stage admitted SDLC contract")
            verify_index(identity["files"])
            intent = {**identity, "tree": engine.git(repo, ["write-tree"], "freeze contract tree")}
            intent["sha256"] = digest(intent)
            write(intent_path, intent, exclusive=True)
        if current == parent:
            verify_index(identity["files"])
            if (engine.head(repo) != parent
                    or engine.git(repo, ["write-tree"], "verify contract tree") != intent["tree"]):
                raise engine.ExecutionError("WORKTREE_CONFLICT", "contract tree or parent differs from journal")
            if anchor["status"] == "managed":
                engine._interop("contract ownership", validate_contract_lease, run_dir, project, parent)
            engine.git(repo, ["commit", "-m", identity["message"]], "commit admitted SDLC contract")
            current = engine.head(repo)
        if (engine.git(repo, ["rev-list", "--parents", "-n", "1", current], "verify contract parent").split() != [current, parent]
                or engine.git(repo, ["show", "-s", "--format=%B", current], "verify contract message") != identity["message"]
                or engine.git(repo, ["rev-parse", f"{current}^{{tree}}"], "verify contract tree") != intent["tree"]
                or not engine.clean(repo)):
            raise engine.ExecutionError("WORKTREE_CONFLICT", "contract commit differs from journaled clean direct child")
        committed = set(filter(None, engine._run(
            ["git", "diff", "--name-only", "--no-renames", "-z", parent, current], repo,
            "inspect committed contract paths").stdout.split("\0")))
        if not committed or committed - allowed:
            raise engine.ExecutionError("WORKTREE_CONFLICT", "contract commit escaped its admitted paths")
        engine._verify_integration_commit(repo, parent, current, scope)
        _verify_committed_pair(repo, project)
        if anchor["status"] == "managed":
            engine._interop("contract promotion recording", record_promotion, run_dir, project, current)
        result = {"schema": "workflow/sdlc-contract-result-v1", "intent_sha256": intent["sha256"], "head": current}
        if result_path.exists():
            if read(result_path) != result:
                raise engine.ExecutionError("WORKTREE_CONFLICT", "contract commit result changed")
        else:
            write(result_path, result, exclusive=True)


def seal_sdlc_contract(run_dir, project, execution_id, plan_digest, anchor):
    import workflow_execution as engine
    from git_promotion import GitPromotionError
    from project_specs_lib.contracts import ProjectSpecError
    try:
        return _seal_sdlc_contract(run_dir, project, execution_id, plan_digest, anchor)
    except ProjectSpecError as error:
        raise engine.ExecutionError(error.code, str(error)) from error
    except (OSError, ValueError, subprocess.TimeoutExpired, GitPromotionError) as error:
        raise engine.ExecutionError("WORKTREE_CONFLICT", "contract files or Git identity are unavailable or unsafe") from error
