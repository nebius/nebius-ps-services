"""Give privileged jail setup its own namespace root before running installers."""

from __future__ import annotations

import ctypes
import os
import signal
import sys
from pathlib import Path


def identity(path):
    value = os.stat(path)
    return value.st_dev, value.st_ino


class LinuxMounts:
    def __init__(self):
        # Resolve every binding before temporarily reanchoring the filesystem root.
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.mount.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_ulong,
            ctypes.c_void_p,
        ]
        self.libc.pivot_root.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        self.libc.umount2.argtypes = [ctypes.c_char_p, ctypes.c_int]

    def checked(self, name, *args):
        if getattr(self.libc, name)(*args) != 0:
            raise OSError(ctypes.get_errno(), name)

    def private(self):
        self.checked("mount", None, b"/", None, 16384 | 262144, None)

    def pivot(self):
        self.checked("pivot_root", b".", b".")
        self.checked("umount2", b".", 2)


def abort_transition():
    # Never unwind into imports, exception formatting or installer code with a
    # partially changed root. The private namespace disappears with this process.
    try:
        os.write(
            2,
            b"ERROR: Cannot establish a private jail mount root; setup stopped before "
            b"installation. Ask the cluster administrator to provide mount namespace "
            b"and pivot_root support.\n",
        )
    finally:
        os._exit(1)


def normalize(mounts):
    before = {path: identity(path) for path in ("/", ".", "/proc", "/sys", "/dev")}
    previous_namespace = identity("/proc/self/ns/mnt")
    descriptors = []
    blocked = signal.pthread_sigmask(
        signal.SIG_BLOCK, {signal.SIGINT, signal.SIGHUP, signal.SIGTERM}
    )
    try:
        os.unshare(os.CLONE_NEWNS)
        # Open after unshare: descriptors must refer to this namespace's copied
        # mounts, not to mount objects retained from the parent's namespace.
        for path in ("/", ".", "/proc/self/ns/mnt"):
            descriptors.append(os.open(path, os.O_RDONLY | os.O_CLOEXEC))
        root, cwd, namespace = descriptors
        if identity("/proc/self/ns/mnt") == previous_namespace:
            raise RuntimeError("Mount namespace was not isolated")
        os.setns(namespace, os.CLONE_NEWNS)
        changed = identity("/") != before["/"]
        if changed:
            mounts.private()
            os.fchdir(root)
            mounts.pivot()
            os.chdir("/")
        if identity("/") != before["/"]:
            raise RuntimeError("Jail root changed")
        os.setns(namespace, os.CLONE_NEWNS)
        os.fchdir(cwd)
        if any(identity(path) != expected for path, expected in before.items()):
            raise RuntimeError("Jail path identity changed")
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    except BaseException:  # noqa: BLE001 -- a partial root transition must terminate
        abort_transition()
        raise AssertionError("Unreachable after failed jail transition") from None
    signal.pthread_sigmask(signal.SIG_SETMASK, blocked)
    return changed


def prepare():
    if sys.platform != "linux":
        return
    status = Path("/proc/self/status").read_text()
    effective = int(
        next(
            row.split()[1] for row in status.splitlines() if row.startswith("CapEff:")
        ),
        16,
    )
    required = (1 << 21) | (1 << 18)  # CAP_SYS_ADMIN and CAP_SYS_CHROOT.
    if os.geteuid() != 0 or effective & required != required:
        try:
            jailed = identity("/") != identity("/proc/1/root")
        except PermissionError:
            jailed = False
        if jailed:
            raise RuntimeError(
                "Jailed setup requires root with existing CAP_SYS_ADMIN and "
                "CAP_SYS_CHROOT; ask the cluster administrator for a supported session"
            )
        return
    if normalize(LinuxMounts()):
        print("Prepared private jail mount root for setup", flush=True)
