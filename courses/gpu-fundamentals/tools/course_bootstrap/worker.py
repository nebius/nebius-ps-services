"""One supervised container worker; parent retains the setup lock until reaped."""

from __future__ import annotations

import json
import os
import select
import signal
import stat


SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


class Cancellation:
    def __init__(self):
        self.signum = None

    def stop(self, signum, _frame):
        if self.signum is None:
            self.signum = signum

    def check(self):
        if self.signum is not None:
            raise SystemExit(128 + self.signum)

    def __enter__(self):
        self.previous = {s: signal.signal(s, self.stop) for s in SIGNALS}
        return self

    def __exit__(self, *_exc):
        for signum, handler in self.previous.items():
            signal.signal(signum, handler)


class WorkerProgress:
    """Only bounded diagnostic events cross the pipe; receipts remain authority."""

    def __init__(self, fd):
        self.fd = fd

    def send(self, kind, **value):
        payload = (json.dumps({"kind": kind, **value}) + "\n").encode()
        while payload:
            payload = payload[os.write(self.fd, payload) :]

    def update(self, phase, **kwargs):
        self.send(
            "progress",
            phase=phase,
            **{k: str(v) if k == "log" else v for k, v in kwargs.items()},
        )

    def message(self, text):
        self.send("message", text=str(text)[:2000])

    def outcome(self, name, value):
        self.send("outcome", name=name, value=value)


def supervise(operation, progress, cancellation, *, log=None):
    """Fork with no UI threads; cancellation reaches only the worker handler.

    Installer subprocesses own separate sessions. Their existing handlers wait
    for package transactions and clean ordinary commands before worker exit.
    """
    progress.stop()
    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, SIGNALS)
    reader = writer = log_fd = None
    try:
        reader, writer = os.pipe()
        if log is not None:
            log_fd = os.open(
                log,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
            )
            info = os.fstat(log_fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
                or info.st_mode & 0o077
            ):
                raise ValueError(
                    "Container worker log must be a private owned regular file"
                )
        pid = os.fork()
    except BaseException:
        if reader is not None:
            os.close(reader)
            os.close(writer)
        if log_fd is not None:
            os.close(log_fd)
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        progress.start()
        raise
    if pid == 0:
        os.close(reader)
        channel = WorkerProgress(writer)
        status = 1
        try:
            if log_fd is not None:
                os.dup2(log_fd, 1)
                os.dup2(log_fd, 2)
                os.close(log_fd)
            os.setsid()
            with Cancellation() as child_cancel:
                channel.send("ready")
                signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
                child_cancel.check()
                operation(child_cancel, channel)
            status = 0
        except SystemExit as exc:
            status = exc.code if isinstance(exc.code, int) else 1
        except BaseException as exc:
            channel.send("fatal", reason=str(exc)[:2000])
        finally:
            os.close(writer)
            os._exit(status)
    ready, forwarded, eof = False, False, False
    outcomes, fatal, pending = {}, None, b""
    status = None
    try:
        if log_fd is not None:
            os.close(log_fd)
            log_fd = None
        os.close(writer)
        writer = None
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        progress.start()
        while status is None or not eof:
            if ready and cancellation.signum is not None and not forwarded:
                try:
                    os.kill(pid, cancellation.signum)
                except ProcessLookupError:
                    pass
                forwarded = True
            if not eof and select.select([reader], [], [], 0.1)[0]:
                chunk = os.read(reader, 65536)
                eof = not chunk
                pending += chunk
                if len(pending) > 1024 * 1024:
                    raise ValueError("Container worker diagnostic message is too large")
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    value = json.loads(line)
                    kind = value.pop("kind")
                    if kind == "ready":
                        ready = True
                    elif kind == "progress":
                        progress.update(**value)
                    elif kind == "message":
                        progress.message(value["text"])
                    elif kind == "outcome":
                        outcomes[value["name"]] = value["value"]
                    elif kind == "fatal":
                        fatal = value["reason"]
                    else:
                        raise ValueError("Invalid container worker message")
            if status is None:
                waited, value = os.waitpid(pid, os.WNOHANG)
                if waited:
                    status = os.waitstatus_to_exitcode(value)
            if eof and status is None:
                # No diagnostic writers remain; wait still retains the lock.
                _, value = os.waitpid(pid, 0)
                status = os.waitstatus_to_exitcode(value)
        return outcomes, status, fatal
    finally:
        os.close(reader)
        if writer is not None:
            os.close(writer)
        if log_fd is not None:
            os.close(log_fd)
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        if status is None:
            if not forwarded:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            os.waitpid(pid, 0)
