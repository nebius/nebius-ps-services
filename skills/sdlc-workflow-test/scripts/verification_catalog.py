"""Pin the physical live-owner catalog; never select executable code from evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import three_tier_target as target

SCHEMA = "agentic-sdlc/runtime-catalog-v1"
RELATIVE = "private/runtime-catalog.json"


class CatalogError(ValueError):
    pass


def anchor() -> Path:
    return Path(__file__).resolve().parents[2]


def snapshot(project: Path) -> dict:
    """Reuse the execution owner's closure, adding this verifier's payload."""
    try:
        return _snapshot(project)
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        if isinstance(error, CatalogError):
            raise
        raise CatalogError("Live runtime catalog closure is unavailable or invalid.") from error


def _snapshot(project: Path) -> dict:
    import three_tier_native as native

    catalog = anchor()
    with target.workflow_imports(catalog):
        import workflow_impact

        if Path(workflow_impact.__file__).resolve() != catalog / "worktree/scripts/workflow_impact.py":
            raise CatalogError("Mixed runtime catalogs; run the live owner in a fresh process.")
        files = {
            str(Path(path).relative_to(catalog)): digest
            for path, digest in workflow_impact._identity_files(project).items()
            if Path(path).is_relative_to(catalog)
        }
    for relative, data in native.hook_sources(catalog).items():
        files[relative] = hashlib.sha256(data).hexdigest()
    skill = catalog / "sdlc-workflow-test"
    for path in sorted(skill.rglob("*")):
        if path.is_symlink():
            raise CatalogError("Live runtime catalog contains a symlink.")
        if not path.is_file() or path.name == ".install-source-id" or "__pycache__" in path.parts:
            continue
        files[str(path.relative_to(catalog))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"schema": SCHEMA, "catalog_root": str(catalog), "files": files}


def read(state: dict) -> dict:
    binding = state.get("runtime_catalog")
    if not isinstance(binding, dict) or set(binding) != {"path", "sha256"} or binding["path"] != RELATIVE:
        raise CatalogError("Live runtime catalog binding is missing or invalid.")
    boundary = Path(state["run_root"])
    raw = target._private_bytes(boundary / RELATIVE, boundary)
    if hashlib.sha256(raw).hexdigest() != binding["sha256"]:
        raise CatalogError("Live runtime catalog binding changed.")
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {"schema", "catalog_root", "files"} or value["schema"] != SCHEMA:
        raise CatalogError("Live runtime catalog receipt is invalid.")
    return value


def validate(state: dict) -> None:
    validate_record(read(state), Path(state["project_root"]))


def validate_record(value: dict, project: Path) -> None:
    """Also validate a retained cleanup checkpoint after raw receipt deletion."""
    if not isinstance(value, dict):
        raise CatalogError("Live runtime catalog receipt is invalid.")
    if value.get("catalog_root") != str(anchor()):
        raise CatalogError("Live runtime catalog differs; invoke the same installed owner used by prepare.")
    if value != snapshot(project):
        raise CatalogError("Live runtime catalog changed; preserve the trial and restore its exact owner for cleanup.")


def require_native_installation(state: dict, agent: str) -> None:
    from agent_runtime import installed_skills_dir

    if agent != "codex":
        raise CatalogError("Native three-tier qualification supports Codex only; Claude catalog binding remains unqualified.")
    expected = installed_skills_dir(agent)
    if expected.resolve() != expected or expected != anchor():
        raise CatalogError("Native qualification requires prepare and launch from the host's installed catalog.")
    validate(state)
