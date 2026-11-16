"""Operation-local containment and observation for the private Worktree owner."""
from contextlib import contextmanager
from contextvars import ContextVar
import fcntl
import json
import subprocess
import sys
import time

from workflow_process import Capture, OwnedProcessGroup, run_owned


class OperationError(RuntimeError):
    pass


_current = ContextVar("private_worktree_operation", default=None)


class Operation:
    def __init__(self, action):
        self.action = action
        self.group = OwnedProcessGroup()
        self.started = time.monotonic()
        self.next_observation = self.started + 30

    def observe(self, sample):
        now = time.monotonic()
        if now >= self.next_observation:
            print(json.dumps({"operation": self.action, "health": "observation_required",
                              "waiting_kind": sample.get("waiting_kind", "owned_git_process"),
                              "elapsed_seconds": round(now - self.started, 1)}), file=sys.stderr, flush=True)
            self.next_observation = now + 30


@contextmanager
def operation(action):
    # Nested owner calls share the exact pinned group and operation lifetime.
    if _current.get() is not None:
        yield
        return
    value = Operation(action)
    token = _current.set(value)
    try:
        yield
    finally:
        _current.reset(token)
        try:
            # Every direct Git child has already exited and been reaped. There
            # is no healthy command to grace here; contain residual descendants
            # while the unreaped anchor still pins the group identity.
            value.group.close(grace=0)
        finally:
            value.group.verify_absent()


def run(arguments, *, cwd=None, env=None, text=False, **options):
    from workflow_timing import observed
    with observed("tools") as sample:
        value = _current.get()
        if value is None:
            result = subprocess.run(arguments, cwd=cwd, env=env, text=text, **options)
            sample["failed"] = result.returncode != 0
            return result
        result = run_owned(arguments, cwd=cwd, env=env, group=value.group,
                           stdout_capture=Capture(4 * 1024 * 1024), stderr_capture=Capture(4 * 1024 * 1024),
                           on_observation=value.observe)
        sample["failed"] = result["exit_code"] != 0
        outputs = [result[key]["data"] for key in ("stdout", "stderr")]
        if any(output is None for output in outputs):
            raise OperationError("Worktree observation is incomplete or exceeds its protocol bound")
        if text:
            outputs = [output.decode() for output in outputs]
        return subprocess.CompletedProcess(arguments, result["exit_code"], *outputs)


def acquire_lock(descriptor):
    from workflow_timing import observed
    with observed("coordination_wait"):
        value = _current.get()
        if value is None:
            return fcntl.flock(descriptor, fcntl.LOCK_EX)
        while True:
            try:
                return fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                value.observe({"waiting_kind": "ownership_lock"})
                time.sleep(.05)
