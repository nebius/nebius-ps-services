from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml
from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

_LOCAL_SOURCE_PATTERN = re.compile(r"^(?:\.\.?/|/|~/|[A-Za-z]:[\\/])")
_REPO_PREFIX = "git::https://github.com/nebius/nebius-ps-services.git//"


def _staged_catalog_is_portable(path: Path) -> bool:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False

    infra = payload.get("infra") or {}
    if not isinstance(infra, dict):
        return False
    modules = infra.get("tf_modules") or []
    if not isinstance(modules, list) or not modules:
        return False

    for module in modules:
        if not isinstance(module, dict):
            return False
        source = str(module.get("source", "")).strip()
        if not source or _LOCAL_SOURCE_PATTERN.match(source):
            return False
    return True


def _build_release_ref() -> str | None:
    for env_name in (
        "NEBIUS_CXCLI_BUILD_RELEASE_REF",
        "RELEASE_REF",
        "NEBIUS_CXCLI_REF",
    ):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--exact-match"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    tag = result.stdout.strip()
    if result.returncode == 0 and tag:
        return tag
    return None


def _rewrite_internal_repo_refs(payload: dict[str, Any], *, release_ref: str) -> dict[str, Any]:
    modules = (((payload or {}).get("infra") or {}).get("tf_modules") or [])
    if not isinstance(modules, list):
        return payload
    for module in modules:
        if not isinstance(module, dict):
            continue
        source = str(module.get("source", "")).strip()
        if source.startswith(_REPO_PREFIX):
            module["source"] = source.replace("?ref=main", f"?ref={release_ref}")
    return payload


def _render_bundled_component_sources(source: Path) -> str:
    payload = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Portable component sources root must be a mapping: {source}")
    release_ref = _build_release_ref()
    if release_ref:
        payload = _rewrite_internal_repo_refs(payload, release_ref=release_ref)
    return yaml.safe_dump(payload, sort_keys=False)


def _select_bundled_component_sources(project_root: Path) -> Path:
    override = os.environ.get("NEBIUS_CXCLI_BUILD_COMPONENT_SOURCES_FILE", "").strip()
    if override:
        candidate = Path(override).expanduser().resolve()
        if not candidate.exists() or not candidate.is_file():
            raise FileNotFoundError(f"Build component sources override not found: {candidate}")
        return candidate

    staged = project_root / "component_sources.yaml"
    if staged.exists() and staged.is_file() and _staged_catalog_is_portable(staged):
        return staged

    return project_root / "component_sources.release.yaml"


class build_py(_build_py):
    def run(self) -> None:
        super().run()
        project_root = Path(__file__).resolve().parent
        source = _select_bundled_component_sources(project_root)
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"Portable component sources file not found: {source}")

        target = Path(self.build_lib) / "nebius_cxcli" / "component_sources.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_render_bundled_component_sources(source), encoding="utf-8")


setup(cmdclass={"build_py": build_py})
