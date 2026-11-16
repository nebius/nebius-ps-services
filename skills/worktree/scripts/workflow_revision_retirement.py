"""Archive exact superseded revision candidates before admitting a fresh wave.

Recovery bytes are private, immutable diagnostics, never implementation results.
No caller chooses a cleanup path and no physical claim is released on inference.
"""

import base64
import hashlib
import json
import os
from pathlib import Path
import selectors
import stat
import subprocess
import time
import uuid

import workflow_execution as engine
from workflow_state import digest, directory, read, write

MAX_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE = 192 * 1024 * 1024
MAX_FILES = 10000


def _fail(message):
    raise engine.ExecutionError("RETIREMENT_REQUIRED", message)


def _bytes(path, limit=MAX_BYTES):
    engine._reject_symlink_components(path, "retained candidate")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != os.getuid() or before.st_size > limit):
            _fail("candidate archive requires bounded, owned, single-link regular files")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            value = handle.read(limit + 1)
        after = os.fstat(descriptor)
        def identity(value):
            return (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        if (len(value) > limit or identity(before) != identity(after)
                or identity(after) != identity(path.lstat())):
            _fail("candidate changed during archive observation")
        return value
    finally:
        os.close(descriptor)


def _encoded(value):
    return base64.b64encode(value).decode("ascii")


def _git_bytes(repo, *arguments, input_data=None):
    """Bound Git output and time while keeping its exact process group owned."""
    from workflow_process import OwnedProcessGroup, ProcessCleanupError
    group = OwnedProcessGroup()
    process = None
    try:
        process = subprocess.Popen(["git", *arguments], cwd=repo, env=engine.git_environment(repo),
                                   stdin=subprocess.PIPE if input_data else subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, process_group=group.id)
        deadline = time.monotonic() + 120
        output = bytearray()
        offset = 0
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if input_data:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _fail("candidate archive Git observation timed out")
                for key, _ in selector.select(remaining):
                    if key.fileobj is process.stdin:
                        try:
                            offset += os.write(process.stdin.fileno(), input_data[offset:offset + 65536])
                        except BlockingIOError:
                            continue
                        except BrokenPipeError:
                            # Some read-only Git commands consume only a header.
                            offset = len(input_data)
                        if offset == len(input_data):
                            selector.unregister(process.stdin)
                            process.stdin.close()
                        continue
                    chunk = os.read(process.stdout.fileno(), min(65536, MAX_BYTES + 1 - len(output)))
                    if not chunk:
                        selector.unregister(process.stdout)
                        continue
                    output.extend(chunk)
                    if len(output) > MAX_BYTES:
                        _fail("candidate Git archive exceeds its bound")
        if process.wait(timeout=max(.001, deadline - time.monotonic())) != 0:
            _fail("candidate archive Git observation failed")
        return bytes(output)
    finally:
        failures = []
        for action in ([lambda: group.close(grace=0)] +
                       ([lambda: process.wait(timeout=5), process.stdout.close] if process else []) +
                       ([process.stdin.close] if process and process.stdin else []) +
                       [group.verify_absent]):
            try:
                action()
            except BaseException as error:
                failures.append(error)
        if failures:
            raise ProcessCleanupError("candidate archive Git cleanup is unproven") from failures[-1]


def _index_objects(worktree):
    """Preserve index and resolve-undo blobs that a branch bundle cannot contain."""
    from workflow_security import contains_sensitive
    if _git_bytes(worktree, "rev-parse", "--shared-index-path").strip():
        _fail("split index requires its existing owner; candidate remains retained")
    rows = _git_bytes(worktree, "ls-files", "--stage", "--resolve-undo", "-z").split(b"\0")
    if len(rows) > MAX_FILES * 4 + 1:
        _fail("candidate index exceeds its entry bound")
    identities = set()
    for row in filter(None, rows):
        metadata = row.split(b"\t", 1)[0].split()
        if (len(metadata) != 3 or metadata[0] not in {b"100644", b"100755", b"120000"}
                or metadata[2] not in {b"0", b"1", b"2", b"3"}):
            _fail("unsupported candidate index entry remains retained")
        oid = metadata[1].decode("ascii")
        if engine.SHA_RE.fullmatch(oid) is None:
            _fail("candidate index object identity is invalid")
        identities.add(oid)
    ordered = sorted(identities)
    raw = _git_bytes(worktree, "cat-file", "--batch", input_data="".join(oid + "\n" for oid in ordered).encode())
    objects = {}
    offset = 0
    for oid in ordered:
        end = raw.find(b"\n", offset)
        fields = raw[offset:end].split()
        if end < 0 or len(fields) != 3 or fields[:2] != [oid.encode(), b"blob"] or not fields[2].isdigit():
            _fail("candidate index object cannot be archived")
        size = int(fields[2])
        start = end + 1
        content = raw[start:start + size]
        algorithm = "sha1" if len(oid) == 40 else "sha256"
        if (size > MAX_BYTES or len(content) != size or raw[start + size:start + size + 1] != b"\n"
                or hashlib.new(algorithm, b"blob " + str(size).encode() + b"\0" + content).hexdigest() != oid):
            _fail("candidate index object content changed")
        if contains_sensitive(content.decode("utf-8", errors="replace")):
            _fail("sensitive index material must remain in its existing protected location")
        objects[oid] = _encoded(content)
        offset = start + size + 1
    if offset != len(raw):
        _fail("candidate index object stream has unexpected trailing content")
    return objects


def _snapshot(worktree):
    entries = {}
    total = 0
    def visit(parent):
        nonlocal total
        # Stream enumeration so the entry bound also bounds discovery memory.
        with os.scandir(parent) as children:
            for entry in children:
                path = Path(entry.path)
                name = path.relative_to(worktree).as_posix()
                if name == ".git":
                    continue
                if path.name == ".git" or len(entries) >= MAX_FILES:
                    _fail("nested Git or excessive entries prevent candidate retirement")
                metadata = path.lstat()
                mode = stat.S_IMODE(metadata.st_mode)
                if stat.S_ISLNK(metadata.st_mode):
                    entries[name] = {"type": "symlink", "target": os.readlink(path), "mode": mode}
                elif stat.S_ISDIR(metadata.st_mode):
                    engine._reject_symlink_components(path, "retained candidate directory")
                    entries[name] = {"type": "directory", "mode": mode}
                    visit(path)
                elif stat.S_ISREG(metadata.st_mode):
                    content = _bytes(path, MAX_BYTES - total)
                    from workflow_security import contains_sensitive
                    if contains_sensitive(content.decode("utf-8", errors="replace")):
                        _fail("sensitive candidate material must remain in its existing protected location")
                    total += len(content)
                    entries[name] = {"type": "file", "content": _encoded(content), "mode": mode}
                else:
                    _fail("special file prevents candidate retirement")
    visit(worktree)
    if _git_bytes(worktree, "ls-files", "--unmerged"):
        _fail("unmerged index prevents candidate retirement")
    index = Path(engine.git(worktree, ["rev-parse", "--git-path", "index"], "locate candidate index"))
    if not index.is_absolute():
        index = worktree / index
    patch = _git_bytes(worktree, "diff", "--cached", "--binary", "--full-index",
                       "--no-ext-diff", "--no-textconv", "--no-renames", "HEAD")
    from workflow_security import contains_sensitive
    if contains_sensitive(patch.decode("utf-8", errors="replace")):
        _fail("sensitive staging material must remain in its existing protected location")
    objects = _index_objects(worktree)
    return {"files": entries, "index": _encoded(_bytes(index)), "index_objects": objects,
            "staged_patch": _encoded(patch),
            "head": engine.head(worktree)}


def _save_snapshot(path, value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    if len(raw) > MAX_ARCHIVE:
        _fail("candidate archive exceeds its storage bound")
    with directory(path.parent, create=True) as parent:
        temporary = ".snapshot-" + uuid.uuid4().hex
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
            os.fsync(parent)
        finally:
            os.unlink(temporary, dir_fd=parent)
    return hashlib.sha256(raw).hexdigest()


def _verify_archive(path, binding, repo):
    raw = _bytes(path, MAX_ARCHIVE)
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        _fail("candidate archive permissions changed")
    try:
        value = json.loads(raw)
        if (set(value) != {"schema", "binding", "candidate", "history"}
                or value["schema"] != "workflow/revision-candidate-archive-v1"
                or value["binding"] != binding):
            _fail("candidate archive identity changed")
        history = base64.b64decode(value["history"], validate=True)
        header = history.split(b"\n\n", 1)[0].splitlines()
        refs = [line for line in header[1:] if not line.startswith(b"@")]
        expected = (value["candidate"]["head"] + " refs/heads/" + binding["branch"]).encode()
        if (len(history) > MAX_BYTES or refs != [expected]
                or value["candidate"]["head"] != binding["head"]):
            _fail("candidate history is not a complete exact branch bundle")
        # Header inspection bounds the verifier's output to this one known ref.
        _git_bytes(repo, "bundle", "verify", "-", input_data=history)
    except (ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        raise engine.ExecutionError("RETIREMENT_REQUIRED", "candidate archive is invalid") from error
    return value, hashlib.sha256(raw).hexdigest()


def _identity(repo, worktree, branch, expected_head):
    engine._reject_symlink_components(worktree, "retained worker")
    registrations = engine.worktrees(repo)
    registration = registrations.get(worktree.resolve())
    if (not worktree.is_dir() or registration is None or registration.get("branch") != "refs/heads/" + branch
            or sum(item.get("branch") == "refs/heads/" + branch for item in registrations.values()) != 1
            or engine.git_common_dir(worktree) != engine.git_common_dir(repo)
            or engine.head(worktree) != expected_head):
        _fail("retained worker identity changed")


def _retire(run_dir, execution, coordinator, wave, row, prepared, sealed):
    from workflow_reconcile import observe_termination
    assignment = read(engine.assignment_path(run_dir, execution, wave["wave_id"], row["task_id"]))
    engine._validate_assignment_record(assignment)
    writer = next(item for item in prepared["writers"] if item["task_id"] == row["task_id"])
    _, state, observation = observe_termination(run_dir, execution, wave["wave_id"], row["task_id"],
                                               Path(writer["termination_path"]))
    if (state["status"] != "superseded" or digest(observation) != writer["proof"]["termination_sha256"]
            or writer["proof_path"] != row["proof_path"] or digest(writer["proof"]) != row["proof_sha256"]
            or read(run_dir / row["proof_path"]) != writer["proof"]):
        _fail("retained writer is not the exact stopped assignment")
    worktree = run_dir / "worktrees" / execution / "waves" / wave["wave_id"] / row["task_id"]
    branch = (f"codex/{coordinator['owner']}/{engine._safe_id(run_dir.name)}/"
              f"{engine._safe_id(execution)}/{engine._safe_id(wave['wave_id'])}/{engine._safe_id(row['task_id'])}")
    repo = Path(coordinator["git_root"])
    if (assignment["worktree"] != str(worktree) or assignment["branch"] != branch
            or Path(assignment["git_common_dir"]) != engine.git_common_dir(repo)):
        _fail("retained assignment escapes its exact execution owner")
    archive = engine.execution_dir(run_dir, execution) / "retired-workers" / assignment["assignment_digest"]
    snapshot = archive / "snapshot.json"
    ready = archive / "archived.json"
    terminal = archive / "retired.json"
    if snapshot.exists():
        try:
            saved = json.loads(_bytes(snapshot, MAX_ARCHIVE))
            expected_head = saved["binding"]["head"]
        except (ValueError, KeyError, TypeError) as error:
            raise engine.ExecutionError("RETIREMENT_REQUIRED", "candidate archive is invalid") from error
    else:
        if ready.exists() or terminal.exists():
            _fail("published candidate archive is missing")
        expected_head = engine.head(worktree)
    if not isinstance(expected_head, str) or engine.SHA_RE.fullmatch(expected_head) is None:
        _fail("retained candidate HEAD is invalid")
    binding = {"assignment_digest": assignment["assignment_digest"], "retention_sha256": digest(row),
               "termination_sha256": digest(observation), "sealed_revision_sha256": sealed["sha256"],
               "worktree": str(worktree), "branch": branch, "head": expected_head,
               "git_common_dir": assignment["git_common_dir"]}
    if not snapshot.exists():
        _identity(repo, worktree, branch, expected_head)
        candidate = _snapshot(worktree)
        history = _git_bytes(worktree, "bundle", "create", "-", "refs/heads/" + branch)
        _save_snapshot(snapshot, {"schema": "workflow/revision-candidate-archive-v1", "binding": binding,
                                  "candidate": candidate, "history": _encoded(history)})
    saved, snapshot_sha256 = _verify_archive(snapshot, binding, repo)
    receipt = {"schema": "workflow/revision-candidate-archived-v1", "binding": binding,
               "snapshot_sha256": snapshot_sha256}
    if ready.exists():
        if read(ready) != receipt:
            _fail("immutable candidate archive receipt changed")
    elif os.path.lexists(worktree):
        _identity(repo, worktree, branch, expected_head)
        if _snapshot(worktree) != saved["candidate"]:
            _fail("candidate drifted before archive publication")
        write(ready, receipt, exclusive=True)
    else:
        _fail("candidate disappeared before archive publication")
    final = {"schema": "workflow/revision-candidate-retired-v1", "archive_sha256": digest(receipt)}
    if terminal.exists():
        if (read(terminal) != final or os.path.lexists(worktree)
                or worktree.resolve() in engine.worktrees(repo) or engine.local_branch_exists(repo, branch)):
            _fail("retired candidate evidence or absence changed")
        return
    if os.path.lexists(worktree):
        if terminal.exists():
            _fail("retired worker path reappeared")
        _identity(repo, worktree, branch, expected_head)
        if _snapshot(worktree) != saved["candidate"]:
            _fail("candidate drifted before exact retirement")
        engine.git(repo, ["worktree", "unlock", str(worktree)], "unlock archived worker", check=False)
        engine.git(repo, ["worktree", "remove", "--force", str(worktree)], "retire archived worker")
    if worktree.resolve() in engine.worktrees(repo):
        _fail("retained worker registration remains unresolved")
    if engine.local_branch_exists(repo, branch):
        if terminal.exists():
            _fail("retired worker branch reappeared")
        if any(item.get("branch") == "refs/heads/" + branch for item in engine.worktrees(repo).values()):
            _fail("retained branch acquired another checkout")
        engine.git(repo, ["update-ref", "-d", "refs/heads/" + branch, expected_head], "retire exact archived branch")
    if os.path.lexists(worktree) or engine.local_branch_exists(repo, branch):
        _fail("retained worker absence is unproven")
    engine._interop("archived worker release", engine.record_outer_resource, run_dir,
                    Path(coordinator["selected_project_root"]), kind="worker", path=worktree,
                    branch=branch, state="absent")
    from workflow_objectives import validate_run
    if validate_run(run_dir)[0].get("shared_admission"):
        from workflow_projects import release_resources
        release_resources(run_dir, "worktrees", [f"{execution}/{wave['wave_id']}/{row['task_id']}"])
    write(terminal, final, exclusive=True)


def retire_sealed_revision_workers(run_dir, execution, *, verify_only=False):
    from agent_runtime import native_session_id
    from workflow_objectives import authorize_coordinator, _writers_quiescent
    from workflow_resources import require_released
    from workflow_validation import require_quiescent
    from workflow_reconcile import validate_retained_wave
    from workflow_revision import _read, _directory
    from workflow_publication import _basis
    with engine._execution_transition_lock(run_dir, execution):
        coordinator = engine._load_coordinator(run_dir, execution)
        for wave_id in coordinator["wave_ids"]:
            wave = read(engine.wave_path(run_dir, execution, wave_id))
            if wave["status"] != "superseded":
                continue
            for kind in ("test", "design"):
                revision = wave.get(kind + "_revision_id")
                if revision is None:
                    continue
                authorize_coordinator(run_dir, native_session_id())
                location = _directory(run_dir, execution, revision, kind)
                prepared = _read(location / "prepare.json", f"workflow/{kind}-revision-prepare-v1")
                sealed = _read(location / "sealed.json", f"workflow/{kind}-revision-sealed-v1")
                if (prepared["execution_id"] != execution or prepared["wave_id"] != wave_id
                        or prepared["revision_id"] != revision or sealed["revision_id"] != revision
                        or sealed["prepare_sha256"] != prepared["sha256"]
                        or sealed["sha256"] not in coordinator.get(kind + "_revisions", [])
                        or prepared["basis_sha256"] == _basis(run_dir, execution)
                        or not engine._is_ancestor(Path(coordinator["git_root"]), sealed["head"],
                                                   coordinator["integration_head"])):
                    _fail("candidate retirement requires a sealed revision and fresh reviewed basis")
                retention = validate_retained_wave(run_dir, execution, wave)
                if (len(prepared["writers"]) != len(retention["assignments"])
                        or {item["task_id"] for item in prepared["writers"]}
                        != {item["task_id"] for item in retention["assignments"]}):
                    _fail("prepared revision writer set changed")
                pending = any(not (engine.execution_dir(run_dir, execution) / "retired-workers" /
                                   row["assignment_digest"] / "retired.json").exists()
                              for row in retention["assignments"])
                if pending:
                    if verify_only:
                        _fail("sealed revision candidates have not completed retirement")
                    _writers_quiescent(run_dir)
                    require_released(run_dir)
                    require_quiescent(run_dir, execution)
                for row in retention["assignments"]:
                    _retire(run_dir, execution, coordinator, wave, row, prepared, sealed)
