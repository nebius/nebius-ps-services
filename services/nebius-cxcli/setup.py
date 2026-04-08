from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import yaml
from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

_REPO_PREFIX = "git::https://github.com/nebius/nebius-ps-services.git//"


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
    modules = (((payload or {}).get("components") or {}).get("infra") or {})
    if not isinstance(modules, dict):
        return payload
    for module in modules.values():
        if not isinstance(module, dict):
            continue
        source_block = module.get("source")
        if not isinstance(source_block, dict):
            continue
        source = str(source_block.get("portable", "")).strip()
        if source.startswith(_REPO_PREFIX):
            source_block["portable"] = source.replace("?ref=main", f"?ref={release_ref}")
    return payload


def _render_bundled_component_sources(source: Path) -> str:
    payload = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Component sources root must be a mapping: {source}")
    release_ref = _build_release_ref()
    if release_ref:
        payload = _rewrite_internal_repo_refs(payload, release_ref=release_ref)
    modules = (((payload or {}).get("components") or {}).get("infra") or {})
    if isinstance(modules, dict):
        for module in modules.values():
            if not isinstance(module, dict):
                continue
            source_block = module.get("source")
            if isinstance(source_block, dict):
                source_block.pop("local", None)
    return yaml.safe_dump(payload, sort_keys=False)


def _select_bundled_component_sources(project_root: Path) -> Path:
    override = os.environ.get("NEBIUS_CXCLI_BUILD_COMPONENT_SOURCES_FILE", "").strip()
    if override:
        candidate = Path(override).expanduser().resolve()
        if not candidate.exists() or not candidate.is_file():
            raise FileNotFoundError(f"Build component sources override not found: {candidate}")
        return candidate

    return project_root / "component_sources.yaml"


class build_py(_build_py):
    def run(self) -> None:
        super().run()
        project_root = Path(__file__).resolve().parent
        source = _select_bundled_component_sources(project_root)
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"Component sources file not found: {source}")

        target = Path(self.build_lib) / "nebius_cxcli" / "component_sources.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_render_bundled_component_sources(source), encoding="utf-8")


setup(cmdclass={"build_py": build_py})
