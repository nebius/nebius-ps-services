"""Bind owned web images to the clean checkout actually used by Compose build."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit


class RuntimeEvidenceError(RuntimeError):
    """A deployment cannot certify the selected source revision."""


def source_identity(target: dict) -> dict:
    return {key: target[key] for key in ("path", "head")}


def command_json(command, argv):
    result = command(argv)
    if result.returncode:
        raise RuntimeEvidenceError("Owned deployment inspection failed.")
    try:
        return json.loads(result.stdout)
    except (TypeError, ValueError) as error:
        raise RuntimeEvidenceError(
            "Owned deployment inspection returned invalid JSON."
        ) from error


def compose_build_model(state, target, command):
    """Read model privately: resolved environment values must never be emitted."""
    model = command_json(
        command,
        [
            "docker",
            "compose",
            "--project-name",
            state["compose_project"],
            "--project-directory",
            target["path"],
            "config",
            "--format",
            "json",
        ],
    )
    web = model.get("services", {}).get("web", {})
    build = web.get("build")
    if isinstance(build, str):
        build = {"context": build}
    if not isinstance(build, dict):
        raise RuntimeEvidenceError("Owned deployment requires a local web build.")
    root = Path(target["path"]).resolve()
    context = Path(build.get("context", ""))
    context = context if context.is_absolute() else root / context
    dockerfile = Path(build.get("dockerfile", "Dockerfile"))
    dockerfile = dockerfile if dockerfile.is_absolute() else root / dockerfile
    if (
        context.resolve() != root
        or not dockerfile.resolve().is_relative_to(root)
        or dockerfile.is_symlink()
        or not dockerfile.is_file()
        or build.get("additional_contexts")
        or build.get("dockerfile_inline")
        or web.get("volumes")
    ):
        raise RuntimeEvidenceError(
            "Owned deployment build must use the exact checkout without web mounts."
        )
    image = web.get("image") or state["compose_project"] + "-web"
    if not isinstance(image, str) or not image or image.startswith("-"):
        raise RuntimeEvidenceError("Owned deployment image name is invalid.")
    return image, hashlib.sha256(json.dumps(model, sort_keys=True).encode()).hexdigest()


def load_build(state, binding, target):
    if not isinstance(binding, dict) or set(binding) != {"path", "sha256"}:
        raise RuntimeEvidenceError("Owned deployment build receipt is missing.")
    relative = Path(binding["path"])
    root = Path(state["run_root"])
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or not str(relative).startswith("evidence/runtime/")
    ):
        raise RuntimeEvidenceError("Owned deployment receipt path is unsafe.")
    path = root / relative
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink():
            raise RuntimeEvidenceError("Owned deployment receipt path is symlinked.")
    if (
        not path.is_file()
        or path.stat().st_nlink != 1
        or path.stat().st_mode & 0o077
        or path.stat().st_size > 65536
    ):
        raise RuntimeEvidenceError("Owned deployment receipt is unavailable or unsafe.")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != binding["sha256"]:
        raise RuntimeEvidenceError("Owned deployment receipt changed.")
    value = json.loads(raw)
    if (
        value.get("schema") != "agentic-sdlc/runtime-build-v1"
        or value.get("verification_id") != state["verification_id"]
        or value.get("target") != source_identity(target)
        or not str(value.get("image_id", "")).startswith("sha256:")
    ):
        raise RuntimeEvidenceError(
            "Owned deployment does not match the selected checkout revision."
        )
    return value


def validate_deployment(state, target, command, assert_owned):
    binding = state.get("runtime_build")
    build = load_build(state, binding, target)
    containers = state["resources"].get("containers", [])
    if len(containers) != 2 or state["resources"].get("images") != [build["image_id"]]:
        raise RuntimeEvidenceError(
            "Owned deployment inventory does not match its build."
        )
    web_id, db_id = containers
    web_owner = assert_owned("containers", web_id, state)
    assert_owned("containers", db_id, state)
    assert_owned("images", build["image_id"], state)
    if web_owner["labels"].get("com.docker.compose.service") != "web":
        raise RuntimeEvidenceError("Owned deployment container is not the web service.")
    inspected = command_json(
        command, ["docker", "container", "inspect", web_id, "--format", "{{json .}}"]
    )
    if (
        inspected.get("Id") != web_id
        or inspected.get("Image") != build["image_id"]
        or inspected.get("Mounts")
        or inspected.get("State", {}).get("Running") is not True
    ):
        raise RuntimeEvidenceError(
            "Owned deployment image changed, is mounted over, or is not running."
        )
    ports = inspected.get("NetworkSettings", {}).get("Ports", {})
    bindings = [
        item for values in ports.values() if isinstance(values, list) for item in values
    ]
    if len(bindings) != 1 or bindings[0].get("HostIp") not in {"127.0.0.1", "::1"}:
        raise RuntimeEvidenceError("Owned deployment endpoint is not isolated.")
    for name in ("web", "api", "health"):
        endpoint = urlsplit(state["endpoints"].get(name, ""))
        if (
            endpoint.scheme != "http"
            or endpoint.hostname not in {"127.0.0.1", "localhost"}
            or endpoint.username
            or endpoint.password
            or str(endpoint.port) != bindings[0].get("HostPort")
        ):
            raise RuntimeEvidenceError(
                "Owned deployment endpoint does not reach its web container."
            )
    return {"build": binding, "web_container": web_id, "image_id": build["image_id"]}
