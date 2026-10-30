"""Lossless, reviewed, conditional policy edits; never a second policy authority."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from .managed_tools import _cache_root, _download_bytes, _platform_tokens
from .tailscale_config import digest, networks

GO_VERSION = "1.27.3"
GO_SHA256 = {
    "darwin-amd64": "b6f8e931e4ab497fcde6da8ae4499a58f9d258e55c8a80824a516eae5950a750",
    "darwin-arm64": "dd3908cf20959d798c1e06ebe5b72213f0505ce01373b40ce3110975a6cd99c2",
    "linux-amd64": "65efbc80fdc1fa2cd2631cad5b481777a68e8725f34e89f50fc6ac714e379b40",
    "linux-arm64": "a06e99309dbcbdca1a8ccd2282c3ee146e55930886e26b4f1193269587165f49",
}


def helper() -> Path:
    """Build/cache the pinned helper for source installs; no system Go installation."""
    source = Path(__file__).with_name("hujson_helper")
    files = [source / name for name in ("go.mod", "go.sum", "main.go")]
    identity = hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest()
    platform = "-".join(_platform_tokens())
    folder = _cache_root() / "nebius-cxcli" / "tools" / "hujson" / identity / platform
    binary, checksum = folder / "cxcli-hujson", folder / "sha256"
    if binary.exists() and checksum.exists():
        if (
            binary.is_symlink()
            or checksum.is_symlink()
            or binary.stat().st_uid != os.getuid()
            or binary.stat().st_mode & 0o022
            or hashlib.sha256(binary.read_bytes()).hexdigest() != checksum.read_text().strip()
        ):
            raise RuntimeError("Cached policy helper failed integrity verification")
        return binary
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if folder.is_symlink() or folder.stat().st_uid != os.getuid() or folder.stat().st_mode & 0o022:
        raise RuntimeError("Unsafe policy helper cache")
    bundled = source / "bin"
    if (bundled / "manifest.json").is_file():
        metadata = json.loads((bundled / "manifest.json").read_text())
        payload = (bundled / (platform + ".bin")).read_bytes()
        sha = hashlib.sha256(payload).hexdigest()
        if (
            metadata.get("source_sha256") != identity
            or metadata.get("binaries", {}).get(platform) != sha
        ):
            raise RuntimeError("Bundled policy helper failed integrity verification")
        with tempfile.TemporaryDirectory(prefix=".install-", dir=folder) as directory:
            staged = Path(directory) / "helper"
            staged.write_bytes(payload)
            staged.chmod(0o700)
            os.replace(staged, binary)
            staged.write_text(sha)
            os.replace(staged, checksum)
        return binary
    with tempfile.TemporaryDirectory(prefix=".build-", dir=folder) as directory:
        staging = Path(directory)
        # System Go is a developer convenience. Release builds can cache the verified binary.
        go = shutil.which("go")
        if go is not None:
            version = subprocess.run(
                [go, "version"],
                env={**os.environ, "GOTOOLCHAIN": "local"},
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            match = re.search(r"\bgo(\d+)\.(\d+)(?:\.|\s)", version.stdout)
            if version.returncode or not match or tuple(map(int, match.groups())) < (1, 26):
                raise RuntimeError(
                    "Source policy helper requires Go 1.26 or newer; upgrade Go or install a release wheel with the bundled helper"
                )
        if go is None:
            data = _download_bytes(
                f"https://go.dev/dl/go{GO_VERSION}.{platform}.tar.gz",
                tool_name="Tailscale policy build tool",
                max_bytes=128 * 1024 * 1024,
            )
            if hashlib.sha256(data).hexdigest() != GO_SHA256[platform]:
                raise RuntimeError("Go toolchain checksum mismatch")
            archive = staging / "go.tar.gz"
            archive.write_bytes(data)
            with tarfile.open(archive) as bundle:
                bundle.extractall(staging, filter="data")
            go = str(staging / "go" / "bin" / "go")
        work = staging / "source"
        work.mkdir()
        for path in files:
            shutil.copyfile(path, work / path.name)
        env = {**os.environ, "GOTOOLCHAIN": "local", "CGO_ENABLED": "0", "GOWORK": "off"}
        result = subprocess.run(
            [go, "build", "-mod=readonly", "-trimpath", "-o", str(staging / "cxcli-hujson"), "."],
            cwd=work,
            env=env,
            capture_output=True,
            timeout=240,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(
                "Pinned policy helper build failed; verify Go/module download connectivity"
            )
        built = staging / "cxcli-hujson"
        built.chmod(0o700)
        (staging / "sha256").write_text(hashlib.sha256(built.read_bytes()).hexdigest())
        os.replace(built, binary)
        os.replace(staging / "sha256", checksum)
    return binary


def transform(document: str, patch: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    result = subprocess.run(
        [str(helper())],
        input=json.dumps({"document": document, "patch": patch or []}),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode or len(result.stdout) > 8 * 1024 * 1024:
        raise RuntimeError("Policy parsing/patch failed; existing policy was not changed")
    return json.loads(result.stdout)


def proposal(document: str, config: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    original = transform(document)["value"]
    if not isinstance(original, dict):
        raise ValueError("Tailnet policy must be an object")
    working = copy.deepcopy(original)
    policy = config.get("policy", {})
    operations = []

    def pointer(key: str) -> str:
        return key.replace("~", "~0").replace("/", "~1")

    def add(path: str, value: Any) -> None:
        operations.append({"op": "add", "path": path, "value": value})

    owners = policy.get("tag_owners", [])
    if owners:
        if "tagOwners" not in working:
            add("/tagOwners", {})
            working["tagOwners"] = {}
        for tag in config["tags"]:
            if tag in working["tagOwners"]:
                if not set(owners).issubset(set(working["tagOwners"][tag])):
                    raise RuntimeError(
                        "An existing tag has different owners; use an administrator-managed tag"
                    )
            else:
                add("/tagOwners/" + pointer(tag), owners)
    if policy.get("auto_approve", True):
        if "autoApprovers" not in working:
            add("/autoApprovers", {})
            working["autoApprovers"] = {}
        if "routes" not in working["autoApprovers"]:
            add("/autoApprovers/routes", {})
            working["autoApprovers"]["routes"] = {}
        for route in networks(config["routes"]):
            existing = working["autoApprovers"]["routes"].get(route)
            path = "/autoApprovers/routes/" + pointer(route)
            if existing is None:
                add(path, config["tags"])
            else:
                for tag in config["tags"]:
                    if tag not in existing:
                        add(path + "/-", tag)
    from .tailscale_config import grants

    for grant in grants(config):
        if "grants" not in working:
            add("/grants", [])
            working["grants"] = []
        if grant not in working["grants"]:
            add("/grants/-", grant)
            working["grants"].append(grant)
    return transform(document, operations)["document"], operations


def validate_policy(api: Any, document: str) -> None:
    raw, _ = api.request("POST", api.tailnet + "/acl/validate", document, raw=True)
    response = json.loads(raw)
    if not isinstance(response, dict) or response.get("message") or response.get("errors"):
        raise RuntimeError(
            "Tailscale rejected the policy proposal; inspect its rules and tests before applying"
        )


def make_plan(api: Any, config: dict[str, Any], component: str) -> dict[str, Any]:
    before, etag = api.request("GET", api.tailnet + "/acl", raw=True)
    if not etag:
        raise RuntimeError("Policy API did not supply an ETag; cannot safely prepare an update")
    after, operations = proposal(before, config)
    validate_policy(api, after)
    result = {
        "schema": 1,
        "tailnet": config["tailnet"],
        "component": component,
        "etag": etag,
        "baseline_sha256": hashlib.sha256(before.encode()).hexdigest(),
        "intent_sha256": digest(config),
        "document": after,
        "patch": operations,
    }
    return {**result, "sha256": digest(result)}


def apply_plan(
    api: Any, config: dict[str, Any], component: str, plan: dict[str, Any]
) -> dict[str, str]:
    signed = {k: v for k, v in plan.items() if k != "sha256"}
    if (
        plan.get("schema") != 1
        or digest(signed) != plan.get("sha256")
        or plan.get("tailnet") != config["tailnet"]
        or plan.get("component") != component
        or plan.get("intent_sha256") != digest(config)
    ):
        raise RuntimeError("Policy proposal was changed or belongs to another intent; replan")
    before, etag = api.request("GET", api.tailnet + "/acl", raw=True)
    if (
        etag != plan["etag"]
        or hashlib.sha256(before.encode()).hexdigest() != plan["baseline_sha256"]
    ):
        raise RuntimeError(
            "Tailnet policy changed since review; replan without overwriting administrator edits"
        )
    # Recompute the exact bounded proposal: a valid hash is not authority for arbitrary edits.
    after, patch = proposal(before, config)
    if after != plan["document"] or patch != plan["patch"]:
        raise RuntimeError("Reviewed proposal contains changes outside configured intent")
    validate_policy(api, after)
    api.request("POST", api.tailnet + "/acl", after, raw=True, etag=etag)
    observed, _ = api.request("GET", api.tailnet + "/acl", raw=True)
    if transform(observed)["value"] != transform(after)["value"]:
        raise RuntimeError(
            "Policy write/readback differs; inspect current policy before another mutation"
        )
    return {"status": "PASS", "reason": "Reviewed policy applied and independently read back"}
