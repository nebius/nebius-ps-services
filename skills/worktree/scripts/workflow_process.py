"""Owned native process groups with a pinned identity through descendant cleanup.

POSIX groups contain ordinary descendants, not processes that deliberately escape
or daemon-owned resources. Those require their resource owner's stop receipt.
"""
from __future__ import annotations

import os
import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import selectors
import signal
import subprocess
import sys
import time


class ProcessCleanupError(RuntimeError):
    code = "WRITER_NOT_QUIESCENT"


def execution_policy(value):
    """Validate the canonical observation policy; callers admit deadline authority.

    A deadline must be bound by the admitting owner to a frozen timed criterion,
    negative control, or independently established backend constraint. A string
    supplied by an untrusted command is never that authority.
    """
    if (not isinstance(value, dict)
            or set(value) != {"observation_interval_seconds", "deadline"}
            or type(value["observation_interval_seconds"]) is not int
            or not 1 <= value["observation_interval_seconds"] <= 300):
        raise ValueError("invalid execution observation policy")
    deadline = value["deadline"]
    if deadline is not None and (
            not isinstance(deadline, dict) or set(deadline) != {"seconds", "basis", "reference"}
            or type(deadline["seconds"]) is not int or deadline["seconds"] <= 0
            or deadline["basis"] not in {"timed_criterion", "negative_control", "backend_constraint"}
            or not isinstance(deadline["reference"], str) or not deadline["reference"].strip()):
        raise ValueError("deadline requires a frozen authority binding")
    return value


def admit_execution_policy(value, *, deadline_authority=None):
    """The owner supplies a separately verified binding, never plan self-report."""
    value = execution_policy(value)
    if value["deadline"] is not None and value["deadline"] != deadline_authority:
        raise ValueError("execution deadline is not bound to independently admitted authority")
    return value


@dataclass(frozen=True)
class Capture:
    """Digest every byte; retain bytes only for a bounded complete protocol."""

    limit: int = 0

    def __post_init__(self):
        if type(self.limit) is not int or not 0 <= self.limit <= 16 * 1024 * 1024:
            raise ValueError("invalid stream capture bound")


class _Stream:
    def __init__(self, capture):
        self.limit = capture.limit
        self.hash = hashlib.sha256()
        self.count = 0
        self.data = bytearray()
        self.eof = False

    def accept(self, chunk):
        self.hash.update(chunk)
        self.count += len(chunk)
        if self.limit:
            self.data.extend(chunk[:max(0, self.limit - len(self.data))])

    def result(self):
        overflow = bool(self.limit and self.count > self.limit)
        return {"sha256": self.hash.hexdigest(), "bytes": self.count,
                "complete": self.eof, "overflow": overflow,
                "data": bytes(self.data) if self.limit and self.eof and not overflow else None}


class PipeCapture:
    """Incrementally hash external-owner pipes without retaining raw diagnostics.

    The caller drains while running and proves process cleanup before finish.
    Pipe EOF is evidence about output completeness, never process ownership.
    """

    def __init__(self, process):
        self.selector = selectors.DefaultSelector()
        self.pipes = (process.stdout, process.stderr)
        self.streams = {name: _Stream(Capture()) for name in ("stdout", "stderr")}
        try:
            for name, stream in self.streams.items():
                pipe = getattr(process, name)
                os.set_blocking(pipe.fileno(), False)
                self.selector.register(pipe, selectors.EVENT_READ, stream)
        except BaseException:
            self.close()
            raise

    def drain(self, timeout=.05):
        for key, _ in self.selector.select(timeout):
            chunk = os.read(key.fd, 65536)
            if chunk:
                key.data.accept(chunk)
            else:
                key.data.eof = True
                self.selector.unregister(key.fileobj)
                key.fileobj.close()

    def close(self):
        for key in list(self.selector.get_map().values()):
            self.selector.unregister(key.fileobj)
        for pipe in self.pipes:
            pipe.close()
        self.selector.close()

    def finish(self):
        # Only bound post-cleanup draining, never the healthy child lifetime.
        deadline = time.monotonic() + 1
        try:
            while self.selector.get_map() and time.monotonic() < deadline:
                self.drain(.02)
            return {name: {key: value for key, value in stream.result().items() if key != "data"}
                    for name, stream in self.streams.items()}
        finally:
            self.close()


class ObservationProbe:
    """One outstanding read-only probe, so observation cannot block pipe drains."""

    def __init__(self, callback):
        self.callback = callback
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="operation-observation")
        self.pending = None
        self.next_probe = 0
        self.value = "health_unknown"

    def poll(self, sample, interval):
        if self.pending is not None:
            if not self.pending.done():
                return "health_unknown"
            try:
                self.value = self.pending.result()
            except (TimeoutError, ConnectionError):
                self.value = "health_unknown"
            self.pending = None
        if self.value in {"continue", "health_unknown"} and time.monotonic() >= self.next_probe:
            self.pending = self.pool.submit(self.callback, sample)
            self.next_probe = time.monotonic() + interval
        return self.value

    def close(self):
        # Probes must use bounded read-only transport operations. They cannot
        # outlive the owner or supply execution/acceptance authority themselves.
        self.pool.shutdown(wait=True, cancel_futures=True)


def run_owned(argv, *, cwd=None, env=None, policy=None,
              stdout_capture=Capture(), stderr_capture=Capture(),
              control=None, on_observation=None, stdin=None, on_stdout=None, cancel_grace=5,
              deadline_authority=None, group=None):
    """Run with bounded streaming and explicit, exact local cleanup evidence.

    Silence and output volume never terminate the command. Control callbacks
    are local nonblocking authority checks, never child output interpreters.
    Unavailable observations report unknown health and retain ownership. External
    effects require their backend's independent reconciliation even after local
    process-group quiescence. No command output is persisted by this function.
    """
    policy = admit_execution_policy(policy or {"observation_interval_seconds": 30, "deadline": None},
                                    deadline_authority=deadline_authority)
    started = time.monotonic()
    deadline = started + policy["deadline"]["seconds"] if policy["deadline"] else None
    next_observation = started
    streams = [_Stream(stdout_capture), _Stream(stderr_capture)]
    owner = group if group is not None else OwnedProcessGroup()
    probe = ObservationProbe(control) if control else None
    process = None
    reason = "exited"
    gaps = 0
    original_error = None
    cleanup_error = None
    spawned_at = None
    execution_ended = None
    pending_input = memoryview(stdin or b"")
    with selectors.DefaultSelector() as selector:
        def drain(timeout):
            # At most one chunk per ready stream, so a noisy stream cannot starve
            # the other stream or cancellation/deadline observations.
            for key, _ in selector.select(timeout):
                if key.data == "stdin":
                    nonlocal pending_input
                    try:
                        count = os.write(key.fd, pending_input[:65536])
                        pending_input = pending_input[count:]
                    except BrokenPipeError:
                        pending_input = memoryview(b"")
                    if not pending_input:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                    continue
                chunk = os.read(key.fd, 65536)
                stream = key.data
                if chunk:
                    stream.accept(chunk)
                    if stream is streams[0] and on_stdout:
                        on_stdout(chunk)
                else:
                    stream.eof = True
                    selector.unregister(key.fileobj)
                    key.fileobj.close()

        try:
            process = subprocess.Popen(argv, cwd=cwd, env=env,
                                       stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       process_group=owner.id)
            spawned_at = time.monotonic()
            if process.stdin is not None:
                if pending_input:
                    os.set_blocking(process.stdin.fileno(), False)
                    selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
                else:
                    process.stdin.close()
            for pipe, stream in zip((process.stdout, process.stderr), streams):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, stream)
            while True:
                now = time.monotonic()
                code = process.poll()
                if code is not None:
                    break
                sample = {"elapsed_seconds": now - started, "process_state": "running",
                          "stdout_bytes": streams[0].count, "stderr_bytes": streams[1].count}
                directive = probe.poll(sample, policy["observation_interval_seconds"]) if probe else "continue"
                if directive not in {"continue", "health_unknown", "cancelled", "confirmed_failure",
                                     "authority_revoked", "resource_safety"}:
                    raise ValueError("invalid process control directive")
                if now >= next_observation:
                    gaps += directive == "health_unknown"
                    if on_observation:
                        on_observation({**sample, "health": directive})
                    next_observation = now + policy["observation_interval_seconds"]
                if directive not in {"continue", "health_unknown"}:
                    reason = directive
                    break
                if deadline is not None and now >= deadline:
                    reason = "bound_deadline"
                    break
                interval = min(.1, max(0, next_observation - now))
                if selector.get_map():
                    drain(interval)
                else:
                    # EOF can precede waitpid readiness. An empty selector
                    # sleeps the full interval; wait observes early child exit.
                    try:
                        process.wait(timeout=interval)
                    except subprocess.TimeoutExpired:
                        pass
        except BaseException as error:
            original_error = error
        finally:
            execution_ended = time.monotonic()
            if reason == "cancelled" and process is not None and process.poll() is None:
                # Give test/harness finally blocks a chance to clean their owned
                # resources. This is cancellation grace, never a work deadline.
                try:
                    os.killpg(owner.id, signal.SIGINT)
                    grace_end = time.monotonic() + cancel_grace
                    while process.poll() is None and time.monotonic() < grace_end:
                        drain(.05)
                except BaseException as error:
                    original_error = original_error or error
            close_group = group is None or original_error is not None or reason != "exited"
            steps = [lambda: owner.close(grace=.05)] if close_group else []
            if process is not None:
                steps.append(lambda: process.wait(timeout=5))
            if close_group:
                steps.append(owner.verify_absent)
            for step in steps:
                try:
                    step()
                except BaseException as error:
                    cleanup_error = cleanup_error or error
            if original_error is not None:
                on_stdout = None
            # Ordinary descendants are now absent. Bound residual draining in
            # case an escaped writer retained a pipe; never claim complete then.
            drain_end = time.monotonic() + 1
            try:
                while selector.get_map() and time.monotonic() < drain_end:
                    drain(.02)
            except BaseException as error:
                original_error = original_error or error
            for key in list(selector.get_map().values()):
                selector.unregister(key.fileobj)
                key.fileobj.close()
    cleanup_ended = time.monotonic()
    if probe:
        probe.close()
    finished = time.monotonic()
    result = {"exit_code": process.returncode if process is not None else None,
              "stop_reason": reason, "elapsed_seconds": finished - started,
              "timing_clock": "time.monotonic; same-process durations",
              "timings": {"setup_seconds": (spawned_at or execution_ended) - started,
                          "execution_seconds": execution_ended - (spawned_at or execution_ended),
                          "cleanup_seconds": cleanup_ended - execution_ended,
                          "observation_finalization_seconds": finished - cleanup_ended},
              "policy": policy, "observation_gaps": gaps,
              "local_group_quiescent": (close_group and cleanup_error is None
                                        and (process is None or all(stream.eof for stream in streams))),
              "stdout": streams[0].result(), "stderr": streams[1].result()}
    if cleanup_error is not None:
        error = ProcessCleanupError("owned process-group termination is unproven")
        error.observation = result
        error.original_error = original_error
        raise error from cleanup_error
    if original_error is not None:
        original_error.observation = result
        raise original_error
    return result


class OwnedProcessGroup:
    """Hold an unreaped group leader until the final signal has been delivered."""

    def __init__(self):
        if os.name != "posix":
            raise ProcessCleanupError("native process-group containment is unavailable")
        self.anchor = subprocess.Popen(
            [sys.executable, "-I", "-c",
             "import os,signal; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
             "signal.signal(signal.SIGINT,signal.SIG_IGN); "
             "os.write(1,b'ready\\n'); os.read(0,1)"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            process_group=0,
        )
        self.closed = False
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(self.anchor.stdout, selectors.EVENT_READ)
                if not selector.select(5) or self.anchor.stdout.readline() != b"ready\n":
                    raise ProcessCleanupError("native process-group anchor failed")
            self.anchor.stdout.close()
        except BaseException:
            try:
                self.close(grace=0)
                self.verify_absent()
            except BaseException as error:
                raise ProcessCleanupError("native process-group startup termination is unproven") from error
            raise

    @property
    def id(self):
        return self.anchor.pid

    def close(self, *, grace: float):
        if self.closed:
            return
        # Do not poll/reap the anchor before the last killpg: its PID pins PGID.
        try:
            try:
                os.killpg(self.id, signal.SIGTERM)
            except ProcessLookupError:
                pass
            time.sleep(max(0, grace))
        finally:
            try:
                os.killpg(self.id, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                self.anchor.wait(timeout=5)
            finally:
                # Even an interrupted wait may have reaped the anchor. Never
                # signal its potentially reused group identity on a retry.
                if self.anchor.returncode is not None:
                    self.closed = True
                    if self.anchor.stdin:
                        self.anchor.stdin.close()

    def verify_absent(self, *, timeout: float = 5):
        deadline = time.monotonic() + timeout
        while True:
            try:
                os.killpg(self.id, 0)
            except ProcessLookupError:
                return
            if time.monotonic() >= deadline:
                raise ProcessCleanupError("native process-group termination is unproven")
            time.sleep(.02)
