"""Private PR execution, subordinate to the canonical transaction owner.

The source receipt is never copied. Execution manifests are usable only while
both directions of their original-owner binding validate. Candidate programs
must still run in a separate restricted environment, never in this control repo.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
import shutil
import stat
import sys


def require(tx, condition, reason, code="scope_changed"):
    if not condition:
        raise tx.TransactionError(reason, code)


def workspace_root(tx, task, ref):
    key = tx._digest_text(str(task))[:32]
    return tx._codex_home() / "review-workspaces" / key / tx._ref_key(ref)


def secure_tree(tx, path, *, create=False):
    """Check every private component before creating or using any descendant."""
    home = tx._codex_home()
    require(tx, path.is_absolute() and path.is_relative_to(home), "review path is outside the private home")
    current = home
    for part in path.relative_to(home).parts:
        current /= part
        if not current.exists() and not current.is_symlink() and create:
            current.mkdir(mode=0o700)
        try:
            info = current.lstat()
        except OSError as error:
            raise tx.TransactionError("review workspace is unavailable", "effect_unknown") from error
        require(tx, stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                and info.st_uid == os.getuid() and not info.st_mode & 0o077,
                "review workspace is linked, unowned or not private")


def directory_identity(tx, root):
    secure_tree(tx, root)
    values = []
    for path in (root, root / ".git"):
        info = path.lstat()
        require(tx, stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                and info.st_uid == os.getuid(), "review repository was substituted")
        values.extend((info.st_dev, info.st_ino))
    return values


def read_private(tx, path, root, label):
    require(tx, tx._safe_private_file(path, root), label + " is unavailable or unsafe")
    return tx._load_json(path, label)


def source_allowed(tx, root, session):
    require(tx, tx._managed_worktree_name(tx._primary_worktree(root), root) is None,
            "managed workflow checkout cannot delegate ordinary review repairs")
    tx._load_skill_support("sdlc-hook-state", tx.__file__, "commit/scripts/commit_transaction.py")
    active = sys.modules["lib.sdlc_state"].load_active_run(root, tx._codex_home(), session)
    require(tx, active is None, "active Agentic SDLC review is findings-only")


def trusted_hooks(tx, source):
    """Pin an inactive hook policy; executable hooks need stronger isolation."""
    path = Path(tx._git_text(source, "rev-parse", "--path-format=absolute", "--git-path", "hooks"))
    require(tx, path.is_absolute() and not path.is_symlink(), "trusted hook path is linked or relative")
    path = path.resolve()
    if not path.exists():
        return {"path": str(path), "fingerprint": None}
    entries = []
    total = 0
    pending = [path]
    while pending:
        current = pending.pop()
        info = current.lstat()
        require(tx, not stat.S_ISLNK(info.st_mode) and info.st_uid in {0, os.getuid()}
                and not info.st_mode & 0o022, "trusted hook policy is linked, unowned or writable by others")
        entry = [str(current.relative_to(path)), info.st_dev, info.st_ino, info.st_mode, info.st_uid]
        if stat.S_ISDIR(info.st_mode):
            pending.extend(sorted(current.iterdir(), reverse=True))
        else:
            require(tx, stat.S_ISREG(info.st_mode), "trusted hook policy contains a special file")
            require(tx, current.parent != path or not info.st_mode & 0o111 or current.name.endswith(".sample"),
                    "executable trusted hooks require restricted execution; private review cannot bypass them")
            total += info.st_size
            require(tx, total <= 8 * 1024 * 1024, "trusted hook policy is too large to pin")
            with os.fdopen(os.open(current, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
                def stable(value):
                    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid,
                            value.st_size, value.st_mtime_ns, value.st_ctime_ns)
                require(tx, stable(os.fstat(stream.fileno())) == stable(info), "trusted hook changed while being pinned")
                content = stream.read(8 * 1024 * 1024 + 1)
                require(tx, len(content) == info.st_size and stable(os.fstat(stream.fileno())) == stable(info),
                        "trusted hook changed while being pinned")
            entry.append(tx._digest_bytes(content))
        entries.append(entry)
        require(tx, len(entries) + len(pending) <= 1024, "trusted hook policy has too many entries to pin")
    return {"path": str(path), "fingerprint": tx._digest_bytes(tx._stable_json(entries))}


LFS_OVERRIDES = tuple(value for key, setting in (
    ("clean", ""), ("smudge", ""), ("process", ""), ("required", "true")
) for value in ("-c", f"filter.lfs.{key}={setting}"))


def private_arguments(tx, arguments):
    """Owner overrides follow all caller global options, before the subcommand."""
    offset = 0
    while offset < len(arguments) and arguments[offset] == "-c":
        require(tx, offset + 1 < len(arguments), "missing private Git config value")
        offset += 2
    require(tx, offset < len(arguments) and not arguments[offset].startswith("-"),
            "unsupported private Git global option")
    return (*arguments[:offset], *LFS_OVERRIDES, *arguments[offset:]), arguments[offset:]


def inert_git_config(tx, root, env=None):
    """Reject candidate-triggered programs, including private-path includeIf rules."""
    result = tx._run_git(root, ("config", "--null", "--get-regexp",
        r"^(filter\..*\.(clean|smudge|process)|core\.fsmonitor|diff\.external|diff\..*\.(command|textconv)|merge\..*\.driver)$"),
        check=False, env=env, _review_policy=False)
    require(tx, result.returncode in {0, 1}, "private Git execution policy is unavailable")
    for field in result.stdout.split(b"\0"):
        if not field:
            continue
        key, separator, value = field.partition(b"\n")
        if separator and key in {b"filter.lfs.clean", b"filter.lfs.smudge", b"filter.lfs.process"}:
            # Every private invocation overrides these, including this probe.
            continue
        require(tx, separator and key == b"core.fsmonitor" and value.lower() in {b"false", b"no", b"off", b"0", b""},
                "executable Git filters, fsmonitor, diff or merge drivers require restricted execution")


def no_lfs(tx, root, *, tree=None, env=None):
    """Check attributes without checkout, conversion, or invoking git-lfs."""
    def probe(args, **kwargs):
        return tx._run_git(root, args, env=env, _review_policy=False, **kwargs)
    if tree is not None:
        paths = probe(("ls-tree", "-r", "--name-only", "-z", tree)).stdout
        modes = [("--source=" + tree,)]
    else:
        paths = probe(("ls-files", "--cached", "--others", "--exclude-standard", "-z")).stdout
        modes = [(), ("--cached",)]
    if not paths:
        return
    for mode in modes:
        result = probe(("check-attr", *mode, "-z", "--stdin", "filter"), input=paths)
        fields = result.stdout.split(b"\0")
        require(tx, fields[-1] == b"" and (len(fields) - 1) % 3 == 0,
                "private filter attributes are unavailable")
        require(tx, all(value != b"lfs" for value in fields[2:-1:3]),
                "LFS content is not supported by private reviews yet", "unsupported_lfs")


def check_command(tx, root, arguments, env):
    """Recheck conversion boundaries; required=true also fences missed paths."""
    command = arguments[0]
    if command in {"status", "diff", "add", "commit", "merge", "checkout", "switch",
                   "reset", "read-tree", "write-tree", "push"}:
        no_lfs(tx, root, env=env)
    if command in {"merge", "checkout", "switch", "reset", "read-tree"}:
        for value in arguments[1:]:
            if tx._valid_object(value):
                no_lfs(tx, root, tree=value, env=env)


def lock_directories(tx, common):
    locator = tx._transaction_root(common) / "review-owner.json"
    if not locator.exists() and not locator.is_symlink():
        # Missing registration must not turn a private execution into a root task.
        require(tx, not common.is_relative_to(tx._codex_home() / "review-workspaces"),
                "private review execution registration is missing")
        return [common]
    value = read_private(tx, locator, tx._transaction_root(common), "review lock binding")
    require(tx, set(value) == {"parent_common", "execution_common", "parent_task"}
            and value["execution_common"] == str(common), "invalid review lock binding")
    parent = Path(value["parent_common"])
    require(tx, parent.is_absolute() and parent != common
            and not parent.is_relative_to(tx._codex_home() / "review-workspaces"),
            "invalid original review lock")
    return [parent, common]


def validate_records(tx, records, grant):
    require(tx, isinstance(records, dict) and set(records) <= set(grant["targets"]),
            "invalid review execution registry")
    for ref, value in records.items():
        require(tx, isinstance(value, dict)
                and set(value) == {"root", "task", "task_sha256", "phase", "identity", "allocation_identity", "source_checkout"}
                and value["phase"] in {"allocating", "active", "removing", "removed"}
                and isinstance(value["root"], str) and isinstance(value["task"], str)
                and tx._valid_ref(Path(grant["repo_root"]), value["source_checkout"], "refs/heads/")
                and (value["allocation_identity"] is None or isinstance(value["allocation_identity"], list)
                     and len(value["allocation_identity"]) == 2
                     and all(type(n) is int for n in value["allocation_identity"]))
                and (value["identity"] is None or isinstance(value["identity"], list)
                     and len(value["identity"]) == 4 and all(type(n) is int for n in value["identity"]))
                and (value["task_sha256"] is None or tx._valid_digest(value["task_sha256"])),
                "invalid review execution record")
        require(tx, grant["review_parent"] is None and grant["requested_action"] == "create-pr",
                "review delegation cannot recurse or widen action")


def validate_parent(tx, path, grant, root, session, *, allow_closed=False):
    parent = grant["review_parent"]
    if parent is None:
        require(tx, not root.is_relative_to(tx._codex_home() / "review-workspaces"),
                "private execution lacks original authority")
        return
    require(tx, isinstance(parent, dict)
            and set(parent) == {"task", "sha256", "ref", "hooks"}
            and tx._valid_digest(parent["sha256"]), "invalid original review authority")
    parent_path = Path(parent["task"])
    original = read_private(tx, parent_path, tx._codex_home() / "commit-transactions", "original review task")
    require(tx, original.get("review_parent") is None and original.get("schema") == tx.TASK_SCHEMA,
            "review authority cannot recurse")
    source = tx._canonical_repo(original["repo_root"])
    tx._validate_task({"owner_evidence_path": str(parent_path), "owner_evidence_sha256": parent["sha256"]},
                      source, session, allow_closed=allow_closed, validate_scope=False)
    source_allowed(tx, source, session)
    inert_git_config(tx, root)
    empty_hooks = root.parent / "empty-template"
    secure_tree(tx, empty_hooks)
    require(tx, trusted_hooks(tx, source) == parent["hooks"] and not any(empty_hooks.iterdir())
            and tx._git_text(root, "config", "--path", "--get", "core.hooksPath") == str(empty_hooks),
            "trusted review hook policy changed")
    state = tx._task_state(parent_path, original)
    record = state["reviews"].get(parent["ref"])
    expected_root = workspace_root(tx, parent_path, parent["ref"]) / "repo"
    secure_tree(tx, expected_root)
    require(tx, record is not None and record["phase"] == "active"
            and record["root"] == str(root) == str(expected_root)
            and record["task"] == str(path)
            and record["identity"] == directory_identity(tx, root)
            and record["task_sha256"] == tx._digest_bytes(tx._stable_json(grant)),
            "review execution binding changed")
    require(tx, grant["requested_action"] == "create-pr"
            and set(grant["targets"]) == {parent["ref"]}
            and grant["receipt_sha256"] == original["receipt_sha256"]
            and grant["turn_sha256"] == original["turn_sha256"]
            and grant["prompt_sha256"] == original["prompt_sha256"]
            and grant["origin_sha256"] == original["origin_sha256"]
            and grant["base_ref"] == original["base_ref"]
            and grant["default_ref"] == original["default_ref"]
            and grant["completion"]["policy"] == "preserve",
            "review execution exceeds original authority")
    locator = read_private(tx, tx._transaction_root(tx._common_dir(root)) / "review-owner.json",
                           tx._transaction_root(tx._common_dir(root)), "review lock binding")
    require(tx, locator == {"parent_common": original["common_dir"],
                           "execution_common": str(tx._common_dir(root)), "parent_task": str(parent_path)},
            "review lock identity changed")


def initial_state(tx, grant):
    return {
        "schema": tx.TASK_STATE_SCHEMA, "grant_sha256": tx._digest_bytes(tx._stable_json(grant)),
        "base_head": grant["base_head"], "reviews": {},
        "completion": {"phase": "active", "pending": None, "effects": [], "updates": {},
                       "fetch": "not-requested", "destination_head": None, "checkout": None},
        "targets": {ref: {"head": value["head"], "commits": 0, "last_claim": None, "sync": None,
                          "materialized": value["kind"] == "local", "merged_result": None,
                          "publication": {"remote_head": value["head"], "published_head": None,
                                          "pending": None, "last_rejected": None, "advanced_head": None}}
                    for ref, value in grant["targets"].items()},
    }


def allocate(tx, source, task, ref, selector, base, default):
    directory = workspace_root(tx, task, ref)
    secure_tree(tx, directory, create=True)
    marker = directory / "allocation.json"
    hooks = trusted_hooks(tx, source)
    expected = {"source_task": str(task), "ref": ref, "selector": selector, "base": base, "default": default, "hooks": hooks}
    if marker.exists() or marker.is_symlink():
        require(tx, read_private(tx, marker, directory, "review allocation") == expected,
                "review allocation changed")
    else:
        require(tx, not any(directory.iterdir()), "unowned review allocation already exists")
        tx._atomic_json(marker, expected)
    root = directory / "repo"
    secure_tree(tx, root, create=True)
    fresh = not (root / ".git").exists() and not (root / ".git").is_symlink()
    if fresh:
        require(tx, not any(root.iterdir()), "incomplete allocation contains unowned files")
    # No template hooks, alternates, reference clones, local hardlinks or caller dirt.
    templates = directory / "empty-template"
    secure_tree(tx, templates, create=True)
    require(tx, not any(templates.iterdir()), "review init template was modified")
    if fresh:
        tx._run_git(root, ("init", "--template=" + str(templates)))
        (root / ".git").chmod(0o700)
    inert_git_config(tx, root)
    # Source policy has no executable hooks. Use an owned empty directory to
    # prevent later source hook additions from executing inside this repository.
    tx._run_git(root, ("config", "core.hooksPath", str(templates)))
    if tx._run_git(root, ("remote", "get-url", "origin"), check=False).returncode:
        tx._run_git(root, ("remote", "add", "origin", tx._git_text(source, "remote", "get-url", "origin")))
        push = tx._git_text(source, "remote", "get-url", "--push", "origin")
        tx._run_git(root, ("remote", "set-url", "--push", "origin", push))
        # Only author identity is copied; repository configuration/hooks are not.
        for key in ("user.name", "user.email"):
            value = tx._run_git(source, ("config", "--get", key), check=False)
            if value.returncode == 0:
                tx._run_git(root, ("config", key, value.stdout.decode().strip()))
    require(tx, tx._common_dir(root) == root / ".git" and (root / ".git").is_dir()
            and not (root / ".git/objects/info/alternates").exists(), "review repository is not independent")
    require(tx, tx._pr_origin_digest(root) == tx._pr_origin_digest(source), "review origin differs from source")
    binding, head = tx._pr.intake(root, tx._run_git, selector, base)
    fork = binding["head_repository_id"] != binding["base_repository_id"]
    if fork:
        require(tx, tx.re.fullmatch(rf"refs/heads/pr/{binding['base_repository_id']}/{binding['number']}/[A-Za-z0-9_-]{{8,64}}", ref),
                "fork requires its namespaced private target")
    else:
        require(tx, ref == "refs/heads/" + binding["head_ref"], "review target differs from PR head branch")
    require(tx, tx._valid_ref(root, ref, "refs/heads/") and ref not in {"refs/heads/" + base, "refs/heads/" + default},
            "review target must be a non-default PR branch")
    base_head = tx._pr.remote_head(root, tx._run_git, "origin", "refs/heads/" + base)
    require(tx, tx._valid_object(base_head), "review base is unavailable")
    tx._run_git(root, ("fetch", "--no-tags", "--no-write-fetch-head", "--no-recurse-submodules", "origin", base_head))
    no_lfs(tx, root, tree=head)
    no_lfs(tx, root, tree=base_head)
    tx._run_git(root, ("update-ref", "refs/remotes/origin/" + base, base_head))
    tx._run_git(root, ("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/" + default))
    existing = tx._local_head(root, ref)
    if existing is None:
        # An interrupted initial allocation has no authority to create repairs.
        tx._run_git(root, ("switch", "--no-track", "-c", ref.removeprefix("refs/heads/"), head))
    else:
        require(tx, existing == head and tx._identity(root)["ref"] == ref and not tx._status(root),
                "incomplete allocation contains work; retain it for recovery", "effect_unknown")
    require(tx, trusted_hooks(tx, source) == hooks, "trusted hooks changed during allocation")
    return root, binding, head, base_head


def child_manifest(tx, task, grant, state, ref, root, binding, head, base_head, session):
    original = tx._digest_bytes(tx._stable_json(grant))
    identity = tx._identity(root)
    child = {**copy.deepcopy(grant), **{k: identity[k] for k in ("repo_root", "worktree", "common_dir")},
             "initial_ref": ref, "initial_head": head, "targets": {ref: {"head": head, "kind": "local"}},
             "pr_targets": {ref: binding}, "dependencies": {ref: []}, "base_head": base_head,
             "validation_ref": None, "recovery_claim_sha256": None,
             "review_parent": {"task": str(task), "sha256": original, "ref": ref,
                               "hooks": read_private(tx, root.parent / "allocation.json", root.parent,
                                                     "review allocation")["hooks"]},
             "completion": {"policy": "preserve", "final_ref": ref,
                            "refs": {ref: head, grant["default_ref"].replace("refs/remotes/origin/", "refs/heads/", 1): None}}}
    child_path = tx._task_path(root, session, grant["receipt_sha256"])
    directory_info = root.parent.lstat()
    record = {"root": str(root), "task": str(child_path), "task_sha256": tx._digest_bytes(tx._stable_json(child)),
              "phase": "active", "identity": directory_identity(tx, root),
              "allocation_identity": [directory_info.st_dev, directory_info.st_ino],
              "source_checkout": tx._identity(Path(grant["repo_root"]))["ref"]}
    # Persist a lock link before publishing any execution authority.
    tx._atomic_json(tx._transaction_root(tx._common_dir(root)) / "review-owner.json",
                    {"parent_common": grant["common_dir"], "execution_common": str(tx._common_dir(root)), "parent_task": str(task)})
    if child_path.exists():
        require(tx, read_private(tx, child_path, tx._transaction_root(tx._common_dir(root)), "execution manifest") == child,
                "existing review execution manifest differs")
        tx._task_state(child_path, child)
    else:
        tx._atomic_json(tx._task_state_path(child_path), initial_state(tx, child))
        tx._atomic_json(child_path, child)
    state["reviews"][ref] = record
    tx._atomic_json(tx._task_state_path(task), state)
    return record


def execution(tx, grant, record, session, *, allow_closed=False):
    require(tx, record["phase"] == "active", "review allocation is incomplete or removed", "effect_unknown")
    root = Path(record["root"])
    secure_tree(tx, root)
    root = tx._canonical_repo(str(root))
    task = Path(record["task"])
    _, child = tx._validate_task({"owner_evidence_path": str(task), "owner_evidence_sha256": record["task_sha256"]},
                               root, session, allow_closed=allow_closed, validate_scope=False)
    return root, task, child, tx._task_state(task, child)


def result(tx, grant, record, session):
    root, task, child, state = execution(tx, grant, record, session)
    ref = child["initial_ref"]
    return {**tx._root_task_context(root, task, child, session), "repo_root": str(root),
            "source_task": child["review_parent"]["task"], "branch": ref,
            "head": state["targets"][ref]["head"], "base": state["base_head"]}


def begin_review(tx, args):
    source = tx._canonical_repo(args.repo_root)
    require(tx, args.requested_action == "create-pr" and args.pr_base and args.pr_bindings
            and not args.allow_default_branch and not args.dependency and not args.validation_branch
            and args.checkout_policy is None, "review intake requires exact PR selectors and no extra mutation scope")
    with tx._repository_lock(tx._common_dir(source)):
        source_allowed(tx, source, args.session_id)
        task = tx._task_path(source, args.session_id, args.intent_sha256)
        selectors = tx._load_json(Path(args.pr_bindings), "PR selectors")
        require(tx, selectors and set(selectors) == {"refs/heads/" + ref for ref in args.target or []},
                "review selectors must match the complete target set")
        if task.exists():
            _, grant, _ = tx._task_from_arguments(args, source)
            require(tx, grant["completion"]["policy"] == "review" and selectors == {
                ref: {k: b[k] for k in ("repository", "number")} for ref, b in grant["pr_targets"].items()},
                "review scope changed")
            state = tx._task_state(task, grant)
            return {"status": "active", "task": str(task), "executions": {
                ref: result(tx, grant, state["reviews"][ref], args.session_id) for ref in selectors}}
        identity = tx._identity(source)
        receipt_path = tx.expected_authorization_path(source, args.session_id).with_name("intent.json")
        receipt = read_private(tx, receipt_path, tx._transaction_root(tx._common_dir(source)), "root receipt")
        expected = {"schema": "commit-transaction.intent.v1", **{k: identity[k] for k in ("repo_root", "worktree", "common_dir", "ref")},
                    "base_head": identity["head"], "session_sha256": tx._digest_text(args.session_id)}
        require(tx, set(receipt) == {*expected, "turn_sha256", "prompt_sha256"}
                and all(receipt.get(k) == v for k, v in expected.items())
                and all(tx._valid_digest(receipt[k]) for k in ("turn_sha256", "prompt_sha256"))
                and tx._digest_bytes(tx._stable_json(receipt)) == args.intent_sha256,
                "root receipt does not match review intake")
        allocations = {}
        default = None
        for ref, selector in selectors.items():
            info = tx._pr.api("repos/" + selector["repository"])
            observed = info.get("default_branch")
            require(tx, isinstance(observed, str) and (default is None or default == observed), "ambiguous default branch")
            default = observed
            allocations[ref] = allocate(tx, source, task, ref, selector, args.pr_base, default)
        grant = {"schema": tx.TASK_SCHEMA, **{k: identity[k] for k in ("repo_root", "worktree", "common_dir")},
                 "session_sha256": receipt["session_sha256"], "requested_action": "create-pr",
                 "receipt_sha256": args.intent_sha256, "turn_sha256": receipt["turn_sha256"], "prompt_sha256": receipt["prompt_sha256"],
                 "initial_ref": identity["ref"], "initial_head": identity["head"],
                 "targets": {ref: {"head": a[2], "kind": "remote"} for ref, a in allocations.items()},
                 "pr_targets": {ref: a[1] for ref, a in allocations.items()}, "review_parent": None,
                 "dependencies": {ref: [] for ref in allocations}, "base_ref": "refs/remotes/origin/" + args.pr_base,
                 "base_head": next(iter(allocations.values()))[3], "origin_sha256": tx._pr_origin_digest(source),
                 "default_ref": "refs/remotes/origin/" + default, "allow_default_branch": False,
                 "validation_ref": None, "recovery_claim_sha256": None,
                 "completion": {"policy": "review", "final_ref": identity["ref"],
                                "refs": {ref: tx._local_head(source, ref) for ref in {identity["ref"], "refs/heads/" + default}}}}
        require(tx, all(a[3] == grant["base_head"] for a in allocations.values()), "review base moved during intake")
        state = initial_state(tx, grant)
        for ref, (root, binding, head, base_head) in allocations.items():
            child_manifest(tx, task, grant, state, ref, root, binding, head, base_head, args.session_id)
        tx._atomic_json(task, grant)  # Original authority is published last.
        return {"status": "active", "task": str(task), "executions": {
            ref: result(tx, grant, state["reviews"][ref], args.session_id) for ref in allocations}}


def open_review(tx, args):
    source = tx._canonical_repo(args.repo_root)
    with tx._repository_lock(tx._common_dir(source)):
        task, grant, _ = tx._task_from_arguments(args, source)
        require(tx, grant["review_parent"] is None and grant["requested_action"] == "create-pr", "review needs the original PR owner")
        source_allowed(tx, source, args.session_id)
        state = tx._task_state(task, grant)
        ref = "refs/heads/" + args.branch
        require(tx, ref in grant["targets"], "review target is outside the original grant")
        if ref in state["reviews"] and state["reviews"][ref]["phase"] == "active":
            return result(tx, grant, state["reviews"][ref], args.session_id)
        require(tx, not state["completion"]["pending"] and state["completion"]["phase"] in {"active", "paused"},
                "parent completion has started")
        target = state["targets"][ref]
        require(tx, target["sync"] is None and target["merged_result"] is None
                and target["publication"]["pending"] is None and target["publication"]["advanced_head"] is None
                and not tx._unresolved_claim(source, task, ref), "parent target has unfinished effects", "effect_unknown")
        selectors = tx._load_json(Path(args.pr_bindings), "PR selectors") if args.pr_bindings else {}
        binding = grant["pr_targets"].get(ref)
        selector = {k: binding[k] for k in ("repository", "number")} if binding else selectors.get(ref)
        require(tx, not selectors or set(selectors) == {ref}, "unexpected review selector scope")
        require(tx, selector is not None and (not selectors or selectors[ref] == selector), "review requires the selected published PR")
        directory = workspace_root(tx, task, ref) / "repo"
        state["reviews"][ref] = {"root": str(directory), "task": "", "task_sha256": None, "phase": "allocating", "identity": None,
                             "allocation_identity": None, "source_checkout": tx._identity(source)["ref"]}
        tx._atomic_json(tx._task_state_path(task), state)  # Fence parent before allocation.
        root, new_binding, head, base = allocate(tx, source, task, ref, selector,
                                               grant["base_ref"].removeprefix("refs/remotes/origin/"),
                                               grant["default_ref"].removeprefix("refs/remotes/origin/"))
        require(tx, head == target["head"] and (binding is None or binding == new_binding), "published PR changed before private delegation")
        record = child_manifest(tx, task, grant, state, ref, root, new_binding, head, base, args.session_id)
        return result(tx, grant, record, args.session_id)


def ready_head(tx, root, child, state, *, published=True):
    ref = child["initial_ref"]
    target = state["targets"][ref]
    task = (tx._transaction_root(Path(child["common_dir"])) / "sessions"
            / child["session_sha256"][:24] / "tasks" / (child["receipt_sha256"] + ".json"))
    require(tx, tx._identity(root)["ref"] == ref and tx._local_head(root, ref) == target["head"]
            and not tx._status(root) and target["sync"] is None and not state["completion"]["pending"]
            and target["publication"]["pending"] is None and target["publication"]["advanced_head"] is None,
            "review execution has unfinished or changed work", "effect_unknown")
    require(tx, not tx._unresolved_claim(root, task, ref), "review execution has an unresolved claim", "effect_unknown")
    require(tx, not published or target["publication"]["published_head"] == target["head"]
            or target["head"] == target["publication"]["remote_head"] == child["initial_head"],
            "review repair has not been published", "effect_unknown")
    return target["head"]


def context_command(tx, args):
    source = tx._canonical_repo(args.repo_root)
    with tx._repository_lock(tx._common_dir(source)):
        task, grant, _ = tx._task_from_arguments(args, source)
        state = tx._task_state(task, grant)
        ref = "refs/heads/" + args.branch
        require(tx, ref in state["reviews"], "review execution is not registered")
        record = state["reviews"][ref]
        root, _, child, checkpoint = execution(tx, grant, record, args.session_id)
        ready_head(tx, root, child, checkpoint)
        return result(tx, grant, record, args.session_id)


def preserves_source(grant, state):
    return grant["completion"]["policy"] == "review" or (
        grant["review_parent"] is None and state["reviews"]
        and set(state["reviews"]) == set(state["targets"])
        and all(record["source_checkout"] == grant["completion"]["final_ref"] for record in state["reviews"].values())
        and (grant["completion"]["policy"] == "preserve"
             or not any(t["merged_result"] for t in state["targets"].values())))


def complete_source(tx, task, grant, state, mode):
    require(tx, not state["completion"]["pending"], "source completion effect is pending", "effect_unknown")
    state["completion"].update(phase="paused" if mode == "pause" else "done", checkout=None,
                               fetch="not-applicable", destination_head=None,
                               updates={ref: {"before": head, "after": head, "status": "preserved"}
                                        for ref, head in grant["completion"]["refs"].items()})
    tx._atomic_json(tx._task_state_path(task), state)
    return state["completion"]


def verify_preserved_completion(tx, grant, state):
    value = state["completion"]
    require(tx, value["phase"] == "done" and not value["pending"]
            and value["checkout"] is None
            and value["updates"] == {ref: {"before": head, "after": head, "status": "preserved"}
                                     for ref, head in grant["completion"]["refs"].items()},
            "source preservation completion is unfinished", "effect_unknown")


def completion_heads(tx, task, grant, state, args):
    heads = {}
    for ref, record in state["reviews"].items():
        if record["phase"] == "allocating" and args.outcome == "cancelled":
            # No active two-way binding exists, so no subordinate effect was authorized.
            heads[ref] = state["targets"][ref]["head"]
            continue
        root, _, child, checkpoint = execution(tx, grant, record, args.session_id)
        if args.outcome == "completed":
            heads[ref] = ready_head(tx, root, child, checkpoint)
        else:
            target = checkpoint["targets"][ref]
            require(tx, target["publication"]["pending"] is None and target["sync"] is None,
                    "uncertain review effects prevent cancellation", "effect_unknown")
            heads[ref] = target["head"]
    return heads


def record_merge(tx, grant, record, sha):
    require(tx, record["phase"] == "active", "review allocation is incomplete")
    path = Path(record["task"])
    child = read_private(tx, path, tx._codex_home() / "commit-transactions", "review manifest")
    require(tx, tx._digest_bytes(tx._stable_json(child)) == record["task_sha256"], "review manifest changed")
    state = tx._task_state(path, child)
    target = state["targets"][child["initial_ref"]]
    require(tx, target["sync"] is None and target["publication"]["pending"] is None,
            "review effects are unsettled", "effect_unknown")
    target["merged_result"] = sha
    tx._atomic_json(tx._task_state_path(path), state)


def cleanup(tx, args):
    source = tx._canonical_repo(args.repo_root)
    with tx._repository_lock(tx._common_dir(source)):
        task, grant, _ = tx._task_from_arguments(args, source, allow_closed=True, validate_scope=False)
        require(tx, tx._task_closed_path(task).exists(), "review cleanup requires terminal original closure")
        state = tx._task_state(task, grant)
        ref = "refs/heads/" + args.branch
        require(tx, ref in state["reviews"], "review workspace is not owned by this task")
        record = state["reviews"][ref]
        if record["phase"] == "removed":
            return {"status": "removed", "branch": ref}
        directory = workspace_root(tx, task, ref)
        require(tx, record["root"] == str(directory / "repo") and shutil.rmtree.avoids_symlink_attacks,
                "safe scoped review cleanup is unavailable")
        if record["phase"] != "removing":
            root, _, child, checkpoint = execution(tx, grant, record, args.session_id, allow_closed=True)
            ready_head(tx, root, child, checkpoint)
            record["phase"] = "removing"
            tx._atomic_json(tx._task_state_path(task), state)
        # Source lock fences every canonical child process; no arbitrary path is accepted.
        if directory.exists() or directory.is_symlink():
            secure_tree(tx, directory)
            info = directory.lstat()
            require(tx, record["allocation_identity"] == [info.st_dev, info.st_ino],
                    "cleanup allocation directory was substituted")
            shutil.rmtree(directory)
        record["phase"] = "removed"
        tx._atomic_json(tx._task_state_path(task), state)
        return {"status": "removed", "branch": ref}


def verify_execution(tx, path, source, session, *, branch, head, repository, pr, ancestor=None):
    """Independently verify the two-way binding and exact published checkpoint."""
    child = read_private(tx, path, tx._codex_home() / "commit-transactions", "review execution evidence")
    require(tx, child.get("schema") == tx.TASK_SCHEMA and isinstance(child.get("review_parent"), dict)
            and isinstance(child.get("repo_root"), str),
            "publication requires subordinate review evidence")
    root = Path(child["repo_root"])
    secure_tree(tx, root)
    with tx._repository_lock(tx._common_dir(root)):
        _, child = tx._validate_task({"owner_evidence_path": str(path), "owner_evidence_sha256": tx._digest_bytes(tx._stable_json(child))},
                                    root, session)
        parent = read_private(tx, Path(child["review_parent"]["task"]), tx._codex_home() / "commit-transactions", "original review task")
        ref = "refs/heads/" + branch
        binding = child["pr_targets"].get(ref)
        require(tx, source.resolve(strict=True) == Path(parent["repo_root"])
                and ref == child["initial_ref"] and binding is not None
                and binding["repository"] == repository and binding["number"] == pr,
                "review execution does not belong to this publication")
        checkpoint = tx._task_state(path, child)
        observed = ready_head(tx, root, child, checkpoint)
        no_lfs(tx, root, tree=observed)
        no_lfs(tx, root, tree=checkpoint["base_head"])
        if ancestor is not None:
            require(tx, tx._valid_object(ancestor)
                    and tx._run_git(root, ("merge-base", "--is-ancestor", ancestor, observed), check=False).returncode == 0,
                    "previous reviewed head is not an ancestor", "history_changed")
        require(tx, head is None or head == observed, "reviewed publication head changed", "history_changed")
        require(tx, tx._pr.snapshot(binding, root, tx._run_git) == observed,
                "remote PR moved after private review", "history_changed")
        return {"repo_root": str(root), "head": observed, "base": checkpoint["base_head"],
                "task": str(path), "task_sha256": tx._digest_bytes(tx._stable_json(child))}
