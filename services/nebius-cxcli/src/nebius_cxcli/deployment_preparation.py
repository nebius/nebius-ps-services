"""Invocation-local preparation, never durable recovery or live admission authority."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def terraform_inputs(root: Path, *, initialization: bool = False, ignore_lock: bool = False) -> str:
    """Bind actual input bytes; runtime plans, state and credentials are excluded."""
    suffixes = (".tf", ".tf.json", ".hcl")
    if not initialization:
        suffixes += (".tfvars", ".tfvars.json")
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if ".terraform" in relative.parts or not path.is_file():
            continue
        if ignore_lock and path.name == ".terraform.lock.hcl":
            continue
        if path.name.endswith(suffixes):
            # Length framing prevents different file boundaries from producing
            # the same byte stream without a cryptographic hash collision.
            for value in (relative.as_posix().encode(), path.read_bytes()):
                digest.update(len(value).to_bytes(8, "big"))
                digest.update(value)
    return digest.hexdigest()


def initialized_identity(root: Path) -> str | None:
    """Missing or replaced initialization state always invalidates cached work."""
    directory = root / ".terraform"
    if not directory.is_dir():
        return None
    status = directory.stat()
    digest = hashlib.sha256(f"{status.st_dev}:{status.st_ino}".encode())
    for name in ("terraform.tfstate", "modules/modules.json"):
        path = directory / name
        if path.is_file():
            digest.update(path.read_bytes())
    # A surviving .terraform directory alone does not prove installed modules
    # and provider binaries survived cleanup or local source edits.
    seen: set[tuple[int, int]] = set()
    for current, directories, files in os.walk(directory, followlinks=True):
        current_path = Path(current)
        item = current_path.stat()
        inode = (item.st_dev, item.st_ino)
        if inode in seen:
            directories[:] = []
            continue
        seen.add(inode)
        directories[:] = sorted(name for name in directories if name != "cxcli-plans")
        digest.update(f"{current_path.relative_to(directory)}:{inode}".encode())
        for name in sorted(files):
            path = current_path / name
            if not path.is_file():
                return None  # Includes dangling provider-cache links.
            item = path.stat()
            digest.update(
                f"{path.relative_to(directory)}:{item.st_ino}:{item.st_size}:{item.st_mtime_ns}".encode()
            )
    module_index = directory / "modules/modules.json"
    if module_index.is_file():
        try:
            modules = json.loads(module_index.read_text()).get("Modules", [])
            for module in modules:
                source = str(module.get("Source", ""))
                if source.startswith(("./", "../", "/", "file://")):
                    module_dir = (root / module["Dir"]).resolve()
                    digest.update(terraform_inputs(module_dir).encode())
        except (ValueError, KeyError, TypeError, AttributeError):
            return None
    return digest.hexdigest()


@dataclass(frozen=True)
class TerraformObservation:
    root: Path
    inputs: str
    raw: Mapping[str, Any]

    def matches(self, root: Path) -> bool:
        return self.root == root.resolve() and self.inputs == terraform_inputs(root)


@dataclass(frozen=True)
class PreparedRelease:
    """Pure candidate data only; no lease, receipt, auth environment or live proof."""

    binding: str
    values: Mapping[str, Any]


@dataclass
class PreparedDeployment:
    initialized: dict[str, dict[str, str]] = field(default_factory=dict)
    validated: dict[str, set[str]] = field(default_factory=dict)
    notices: set[str] = field(default_factory=set)

    def key(
        self,
        root: Path,
        tool: str,
        env: Mapping[str, str] | None,
        *,
        backend: bool = True,
        initialization: bool = False,
    ) -> str:
        # Environment values remain ephemeral and are never logged or persisted.
        executable = Path(tool)
        stat = executable.stat() if executable.exists() else None
        effective_env = {
            key: value for key, value in os.environ.items() if key.startswith(("TF_", "AWS_"))
        }
        effective_env.update(env or {})
        payload = [
            str(root.resolve()),
            tool,
            (stat.st_mtime_ns, stat.st_size) if stat else None,
            backend,
            terraform_inputs(root, initialization=initialization),
            sorted(effective_env.items()),
        ]
        return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


_PREPARED: ContextVar[PreparedDeployment | None] = ContextVar("prepared_deployment", default=None)


def current_preparation() -> PreparedDeployment | None:
    return _PREPARED.get()


@contextmanager
def prepared_deployment() -> Iterator[PreparedDeployment]:
    current = _PREPARED.get()
    if current is not None:
        yield current
        return
    prepared = PreparedDeployment()
    token = _PREPARED.set(prepared)
    try:
        yield prepared
    finally:
        _PREPARED.reset(token)
