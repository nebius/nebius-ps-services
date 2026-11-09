"""Local encrypted operator key material; private bytes never cross the SSH boundary."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import io
import os
import re
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\Z")


def safe_name(value: str) -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ValueError("Operator and gateway identifiers must be simple nonempty names")
    return value


def output_path(project: str, gateway: str, name: str, override: Path | None = None) -> Path:
    for value in (project, gateway, name):
        safe_name(value)
    return (
        (
            override
            or Path.home()
            / ".ssh"
            / "nebius-vpngw"
            / "operators"
            / project
            / gateway
            / name
            / "id_ed25519"
        )
        .expanduser()
        .absolute()
    )


def safe_ancestors(path: Path) -> None:
    """Reject key selectors that another local user can redirect."""
    for ancestor in path.absolute().parents:
        metadata = ancestor.lstat()
        sticky_root = metadata.st_uid == 0 and bool(metadata.st_mode & stat.S_ISVTX)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid not in {0, os.getuid()}
            or (metadata.st_mode & 0o022 and not sticky_root)
        ):
            raise ValueError("SSH key path has an unsafe ancestor")


def read_public_file(path: Path) -> str:
    safe_ancestors(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_mode & 0o022
        ):
            raise ValueError(
                "Operator public key must be an operator-owned regular file without foreign write access"
            )
        with os.fdopen(fd, "rb", closefd=False) as handle:
            content = handle.read(65537)
        if len(content) > 65536:
            raise ValueError("Operator public key file is too large")

        def signature(item):
            return (
                item.st_dev,
                item.st_ino,
                item.st_uid,
                item.st_mode,
                item.st_size,
                item.st_mtime_ns,
                item.st_ctime_ns,
            )

        if signature(os.fstat(fd)) != signature(before) or signature(path.lstat()) != signature(
            before
        ):
            raise ValueError("Operator public key changed while being read")
        return content.decode("utf-8")
    finally:
        os.close(fd)


def protected_directory(path: Path) -> None:
    """Create missing parents privately, without following symbolic links."""
    if not path.is_absolute():
        raise ValueError("Operator state paths must be absolute")
    missing = []
    current = path
    while not current.exists():
        if current.is_symlink():
            raise ValueError("Operator state directory must not contain symlinks")
        missing.append(current)
        current = current.parent
    for ancestor in (current, *current.parents):
        metadata = ancestor.lstat()
        if ancestor.is_symlink():
            raise ValueError("Operator state directory must not contain symlinks")
        sticky_root = metadata.st_uid == 0 and bool(metadata.st_mode & stat.S_ISVTX)
        if metadata.st_uid not in {0, os.getuid()} or (
            metadata.st_mode & 0o022 and not sticky_root
        ):
            raise ValueError("Operator state directory has an unsafe writable ancestor")
    for item in reversed(missing):
        item.mkdir(mode=0o700)
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
    ):
        raise ValueError("Operator key directory must be owned by the operator with mode 0700")


class PrivateFileChanged(ValueError):
    """A protected read raced a writer; the captured bytes are not usable."""


def read_private_file(path: Path, *, required: bool = True) -> bytes | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        if required:
            raise ValueError("Operator key file is missing") from None
        return None
    try:
        metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise ValueError(
                "Operator key file must be a regular private file owned by the operator"
            )
        with os.fdopen(fd, "rb", closefd=False) as handle:
            content = handle.read(65537)
        if len(content) > 65536:
            raise ValueError("Operator key file is too large")

        def signature(item):
            return (
                item.st_dev,
                item.st_ino,
                item.st_uid,
                item.st_mode,
                item.st_size,
                item.st_mtime_ns,
                item.st_ctime_ns,
            )

        if signature(os.fstat(fd)) != signature(metadata) or signature(path.lstat()) != signature(
            metadata
        ):
            raise PrivateFileChanged("Operator key file changed while being read")
        return content
    finally:
        os.close(fd)


def publish_new(path: Path, content: bytes) -> None:
    # The canonical file becomes visible only after all bytes are durable. A
    # process death cannot leave an empty/truncated private key at its final path.
    fd, temporary_name = tempfile.mkstemp(prefix=".operator-key-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def public_identity(content: str) -> tuple[str, str]:
    """Accept one plain Ed25519 key; enrollment cannot inject authorized_keys options."""
    from cryptography.hazmat.primitives import serialization

    fields = content.strip().split()
    if len(content.strip().splitlines()) != 1 or len(fields) < 2 or fields[0] != "ssh-ed25519":
        raise ValueError("New operators require one plain Ed25519 public key")
    canonical = " ".join(fields[:2])
    try:
        serialization.load_ssh_public_key(canonical.encode("ascii"))
        blob = base64.b64decode(fields[1], validate=True)
    except (ValueError, UnicodeError) as error:
        raise ValueError("Invalid operator public key") from error
    return canonical, "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip(
        "="
    )


@dataclass(frozen=True)
class OperatorKey:
    public_key: str
    fingerprint: str
    private_path: Path | None
    public_path: Path
    pkey: Any = field(default=None, repr=False, compare=False)


def generate_or_resume(path: Path, passphrase: str) -> OperatorKey:
    """Retry the same encrypted pair, including a crash before public-file creation."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if not passphrase:
        raise ValueError("A nonempty passphrase is required for generated operator keys")
    path = path.expanduser().absolute()
    protected_directory(path.parent)
    public_path = Path(str(path) + ".pub")
    lock_fd = os.open(
        path.parent / ".key-generation.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    try:
        metadata = os.fstat(lock_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise ValueError("Unsafe operator key generation lock")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        existing = read_private_file(path, required=False)
        existing_public = read_private_file(public_path, required=False)
        if existing is None:
            if existing_public is not None:
                raise ValueError(
                    "Public key already exists without its private key; refusing regeneration"
                )
            private = Ed25519PrivateKey.generate()
            encrypted = private.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.OpenSSH,
                serialization.BestAvailableEncryption(passphrase.encode()),
            )
            publish_new(path, encrypted)
        else:
            try:
                loaded = serialization.load_ssh_private_key(existing, password=passphrase.encode())
            except (TypeError, ValueError) as error:
                raise ValueError(
                    "Existing operator key requires its original passphrase and encrypted format"
                ) from error
            if not isinstance(loaded, Ed25519PrivateKey):
                raise ValueError("Existing operator key is not Ed25519; refusing replacement")
            private = loaded
        public, fingerprint = public_identity(
            private.public_key()
            .public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
            .decode()
        )
        if existing_public is None:
            publish_new(public_path, (public + "\n").encode())
        elif public_identity(existing_public.decode())[0] != public:
            raise ValueError("Existing public and private keys disagree; refusing replacement")
        import paramiko  # type: ignore[import-untyped]

        # Only the local in-memory verifier sees an unencrypted serialization.
        clear = private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        )
        pkey = paramiko.Ed25519Key.from_private_key(io.StringIO(clear.decode()))
        return OperatorKey(public, fingerprint, path, public_path, pkey)
    finally:
        os.close(lock_fd)


def import_public(path: Path) -> OperatorKey:
    path = path.expanduser().absolute()
    public, fingerprint = public_identity(read_public_file(path))
    return OperatorKey(public, fingerprint, None, path)
