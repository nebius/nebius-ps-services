"""Resolve the CLI's deployable wheel without guessing from local filenames.

The cache retains immutable bytes referenced by operation journals. No automatic
pruning is performed: a later invocation may need an interrupted operation's wheel.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from importlib import metadata
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname, urlopen

from packaging.version import Version

SCHEMA = "nebius-vpngw/release-artifact-v1"
RELEASE_ROOT = "https://github.com/nebius/nebius-ps-services/releases/download"
LIMIT = 64 * 1024 * 1024

_SELECTION: ContextVar[list[tuple[Path, str, str]] | None] = ContextVar(
    "frozen_agent_selection", default=None
)


@contextmanager
def frozen_selection():
    """Keep one immutable release across composed apply phases in this invocation."""
    token = _SELECTION.set([])
    try:
        yield
    finally:
        _SELECTION.reset(token)


def failure(message: str):
    from .ssh_push import VMHAAgentArtifactError, VMHAAgentArtifactProblem

    return VMHAAgentArtifactError(VMHAAgentArtifactProblem.INCOMPATIBLE, message)


def _read(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > LIMIT:
            raise failure("Agent artifact must be a bounded regular file")
        content = stream.read(LIMIT + 1)
        after = os.fstat(stream.fileno())
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ) or len(content) > LIMIT:
        raise failure("Agent artifact changed during inspection")
    return content


def _download(url: str, *, limit: int = LIMIT) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise failure("Agent artifact provenance requires an HTTPS URL without credentials")
    try:
        with urlopen(url, timeout=60) as response:
            if urlparse(response.geturl()).scheme != "https":
                raise failure("Agent artifact download redirected outside HTTPS")
            content = response.read(limit + 1)
    except OSError as error:
        raise failure(
            "The exact release artifact is unavailable; restore access or set VPNGW_AGENT_WHEEL"
        ) from error
    if len(content) > limit:
        raise failure("Agent artifact download exceeded its size limit")
    return content


def _cache_root() -> Path:
    root = Path.home() / ".cache"
    for path in (root, root / "nebius-vpngw", root / "nebius-vpngw" / "artifacts"):
        path.mkdir(mode=0o700, exist_ok=True)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise failure("Agent artifact cache has unsafe ownership or permissions")
    return root / "nebius-vpngw" / "artifacts"


def _publish(path: Path, content: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix=".artifact-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if _read(path) != content:
                raise failure("Immutable artifact cache entry conflicts with its digest") from None
    finally:
        temporary.unlink()


def cache_wheel(path: Path, *, expected: str | None = None) -> Path:
    content = _read(path)
    digest = hashlib.sha256(content).hexdigest()
    if expected is not None and digest != expected:
        raise failure("Agent wheel does not match its recorded SHA-256")
    if not re.fullmatch(r"nebius_vpngw-[A-Za-z0-9_.+!]+-py3-none-any\.whl", path.name):
        raise failure("Agent wheel filename is invalid")
    directory = _cache_root() / digest
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise failure("Agent artifact cache entry has unsafe ownership or permissions")
    target = directory / path.name
    _publish(target, content)
    return target


def release_manifest(wheel: Path, version: str) -> dict[str, str]:
    """Called by the existing release owner before it freezes all asset hashes."""
    version = str(Version(version))
    if wheel.name != f"nebius_vpngw-{version}-py3-none-any.whl":
        raise failure("Release wheel filename does not match its version")
    return {
        "schema": SCHEMA,
        "version": version,
        "filename": wheel.name,
        "sha256": hashlib.sha256(_read(wheel)).hexdigest(),
    }


def _json_object(content: bytes | str) -> dict:
    try:
        value = json.loads(content)
    except (ValueError, UnicodeError) as error:
        raise failure("Agent artifact provenance is malformed") from error
    if not isinstance(value, dict):
        raise failure("Agent artifact provenance must be an object")
    return value


def _manifest(version: str) -> dict[str, str]:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise failure("This development install needs its exact wheel or a source checkout")
    payload = _json_object(
        _download(f"{RELEASE_ROOT}/nebius-vpngw-v{version}/agent-artifact.json", limit=16384)
    )
    if (
        set(payload) != {"schema", "version", "filename", "sha256"}
        or payload.get("schema") != SCHEMA
        or payload.get("version") != version
    ):
        raise failure("Release artifact manifest does not match this CLI")
    if payload.get("filename") != f"nebius_vpngw-{version}-py3-none-any.whl" or not re.fullmatch(
        r"[0-9a-f]{64}", str(payload.get("sha256", ""))
    ):
        raise failure("Release artifact manifest has invalid wheel identity")
    return payload


def _source_files(root: Path) -> dict[str, bytes]:
    names = ["pyproject.toml", "README.md", "LICENSE"]
    package = root / "src" / "nebius_vpngw"
    names.extend(
        str(path.relative_to(root))
        for path in sorted(package.rglob("*"))
        if path.is_file()
        and "__pycache__" not in path.parts
        and (path.suffix == ".py" or "systemd" in path.relative_to(package).parts)
        and path.name != "_version.py"
    )
    return {name: _read(root / name) for name in names}


def build_source(root: Path) -> Path:
    from .. import __version__

    version = str(Version(__version__))
    files = _source_files(root)
    source_id = hashlib.sha256(
        json.dumps(
            {
                "version": version,
                "files": {name: hashlib.sha256(value).hexdigest() for name, value in files.items()},
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    pointer = _cache_root() / f"source-{source_id}.json"
    if pointer.exists():
        prior = _json_object(_read(pointer))
        digest, filename = prior["sha256"], prior["filename"]
        if (
            not re.fullmatch(r"[0-9a-f]{64}", digest)
            or filename != f"nebius_vpngw-{version}-py3-none-any.whl"
        ):
            raise failure("Source artifact cache identity is invalid")
        cached = _cache_root() / digest / filename
        if hashlib.sha256(_read(cached)).hexdigest() != digest:
            raise failure("Cached source artifact no longer matches its digest")
        return cached
    with tempfile.TemporaryDirectory(prefix="vpngw-source-wheel-") as directory:
        snapshot = Path(directory) / "source"
        snapshot.mkdir()
        for name, content in files.items():
            target = snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        if _source_files(root) != files:
            raise failure("Source changed while freezing the agent artifact; rerun apply")
        output = Path(directory) / "dist"
        result = subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(output)],
            cwd=snapshot,
            capture_output=True,
            timeout=180,
            env={
                **os.environ,
                "SOURCE_DATE_EPOCH": "315532800",
                "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_NEBIUS_VPNGW": version,
            },
        )
        wheels = list(output.glob("*.whl"))
        if result.returncode or len(wheels) != 1:
            raise failure(
                "Building the current source wheel failed; install development build dependencies and rerun apply"
            )
        if _source_files(root) != files:
            raise failure("Source changed during the agent build; rerun apply")
        cached = cache_wheel(wheels[0])
        _publish(
            pointer,
            json.dumps(
                {"sha256": cached.parent.name, "filename": cached.name}, sort_keys=True
            ).encode(),
        )
        return cached


def resolve(project_root: Path | None) -> tuple[Path, str]:
    selection = _SELECTION.get()
    if selection is None:
        return _resolve(project_root)
    if not selection:
        path, source = _resolve(project_root)
        digest = hashlib.sha256(_read(path)).hexdigest()
        selection.append((cache_wheel(path, expected=digest), source, digest))
    path, source, digest = selection[0]
    if hashlib.sha256(_read(path)).hexdigest() != digest:
        raise failure("Selected agent artifact changed within this invocation")
    return path, source


def _resolve(project_root: Path | None) -> tuple[Path, str]:
    override = os.environ.get("VPNGW_AGENT_WHEEL")
    if "VPNGW_AGENT_WHEEL" in os.environ:
        if not override:
            raise failure("VPNGW_AGENT_WHEEL is set but empty")
        return Path(override).expanduser(), "VPNGW_AGENT_WHEEL"
    if getattr(sys, "frozen", False):
        directory = Path(__file__).parent.parent / "agent-wheel"
        wheels = list(directory.glob("*.whl"))
        if len(wheels) != 1:
            raise failure("Standalone CLI is missing its exact bundled agent wheel")
        return cache_wheel(wheels[0]), "bundled-wheel"
    if project_root is not None:
        return build_source(project_root), "source-snapshot"
    dist = metadata.distribution("nebius-vpngw")
    version = str(Version(dist.version))
    direct = dist.read_text("direct_url.json")
    url = None
    expected = None
    if direct:
        value = _json_object(direct)
        url = value.get("url")
        archive = value.get("archive_info", {})
        if not isinstance(archive, dict) or not isinstance(archive.get("hashes", {}), dict):
            raise failure("Installed artifact provenance has malformed hashes")
        expected = archive.get("hashes", {}).get("sha256")
        if expected is None and str(archive.get("hash", "")).startswith("sha256="):
            expected = archive["hash"].split("=", 1)[1]
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        manifest = _manifest(version)
        expected = manifest["sha256"]
        url = f"{RELEASE_ROOT}/nebius-vpngw-v{version}/{manifest['filename']}"
    filename = f"nebius_vpngw-{version}-py3-none-any.whl"
    cached = _cache_root() / expected / filename
    if cached.exists():
        return cache_wheel(cached, expected=expected), "installed-provenance-cache"
    if not isinstance(url, str):
        raise failure("Installed CLI has no exact artifact provenance")
    parsed = urlparse(url)
    if Path(unquote(parsed.path)).name != filename:
        raise failure("Recorded install artifact does not match the CLI version")
    if parsed.scheme == "file" and not parsed.netloc:
        original = Path(url2pathname(unquote(parsed.path)))
        if original.exists():
            return cache_wheel(original, expected=expected), "installed-provenance"
        # Official release retrieval is acceptable only when its immutable digest
        # is identical to the original install's recorded digest.
        manifest = _manifest(version)
        if manifest["sha256"] != expected:
            raise failure("Official release differs from the originally installed wheel")
        url = f"{RELEASE_ROOT}/nebius-vpngw-v{version}/{filename}"
    content = _download(url)
    if hashlib.sha256(content).hexdigest() != expected:
        raise failure("Downloaded agent wheel does not match its recorded SHA-256")
    with tempfile.TemporaryDirectory(prefix="vpngw-release-wheel-") as download_directory:
        wheel = Path(download_directory) / filename
        wheel.write_bytes(content)
        return cache_wheel(wheel, expected=expected), "installed-provenance"
