"""Helpers for rendering and verifying the portable release catalog."""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path
from typing import Any

import yaml

REPO_PREFIX = "git::https://github.com/nebius/nebius-ps-services.git//"
TREE_PREFIX = "https://github.com/nebius/nebius-ps-services/tree/"
BUNDLED_COMPONENT_SOURCES_SUFFIX = "nebius_cxcli/component_sources.yaml"


def _looks_like_local_path(source: str) -> bool:
    value = str(source).strip()
    if not value:
        return False
    if value.startswith(("./", "../", "/", "~")):
        return True
    return len(value) >= 3 and value[1] == ":" and value[2] in ("\\", "/")


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"{path} root must be a mapping")
    return payload


def _infra_modules(payload: dict[str, Any], *, subject: str) -> list[dict[str, Any]]:
    modules = ((payload or {}).get("components") or {}).get("infra") or {}
    if not isinstance(modules, dict) or not modules:
        raise ValueError(f"{subject} has no components.infra entries")
    normalized: list[dict[str, Any]] = []
    for module in modules.values():
        if isinstance(module, dict):
            normalized.append(module)
    return normalized


def _app_charts(payload: dict[str, Any], *, subject: str) -> list[tuple[str, dict[str, Any]]]:
    charts = ((payload or {}).get("components") or {}).get("apps") or {}
    if not isinstance(charts, dict):
        raise ValueError(f"{subject} has invalid components.apps entries")
    normalized: list[tuple[str, dict[str, Any]]] = []
    for chart_id, chart in charts.items():
        if isinstance(chart, dict):
            normalized.append((str(chart_id), chart))
    return normalized


def render_release_catalog(
    *,
    input_path: Path,
    output_path: Path,
    release_ref: str,
) -> None:
    payload = _load_yaml(input_path)
    modules = _infra_modules(payload, subject=str(input_path))
    charts = _app_charts(payload, subject=str(input_path))
    for module in modules:
        source_block = module.get("source")
        if not isinstance(source_block, dict):
            continue
        source = str(source_block.get("portable", "")).strip()
        if source.startswith(REPO_PREFIX):
            source_block["portable"] = source.replace("?ref=main", f"?ref={release_ref}")
        source_block.pop("local", None)
    for _chart_id, chart in charts:
        source_block = chart.get("source")
        if not isinstance(source_block, dict):
            continue
        portable = source_block.get("portable")
        if isinstance(portable, dict):
            repo = str(portable.get("repo", "")).strip()
            if repo.startswith(f"{TREE_PREFIX}main/"):
                portable["repo"] = repo.replace("/tree/main/", f"/tree/{release_ref}/", 1)
        source_block.pop("local", None)
    output_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _validate_module_sources(
    modules: list[dict[str, Any]],
    *,
    subject: str,
    release_ref: str,
) -> None:
    bad_sources: list[str] = []
    for module in modules:
        source_block = module.get("source")
        if not isinstance(source_block, dict):
            bad_sources.append("<missing-source>")
            continue
        source = str(source_block.get("portable", "")).strip()
        if not source:
            bad_sources.append("<empty>")
            continue
        if _looks_like_local_path(source):
            bad_sources.append(source)
            continue
        if source.startswith(REPO_PREFIX):
            if f"?ref={release_ref}" not in source or "?ref=main" in source:
                bad_sources.append(source)
            continue
        if "?ref=main" in source:
            bad_sources.append(source)
            continue
        local_source = str(source_block.get("local", "")).strip()
        if local_source:
            bad_sources.append(f"source.local={local_source}")
    if bad_sources:
        raise ValueError(
            f"{subject} contains non-portable or incorrectly pinned infra sources: "
            + ", ".join(bad_sources)
        )


def _validate_chart_sources(
    charts: list[tuple[str, dict[str, Any]]],
    *,
    subject: str,
    release_ref: str,
) -> None:
    bad_sources: list[str] = []
    for chart_id, chart in charts:
        source_block = chart.get("source")
        if not isinstance(source_block, dict):
            bad_sources.append(f"{chart_id}:<missing-source>")
            continue
        portable = source_block.get("portable")
        if not isinstance(portable, dict):
            bad_sources.append(f"{chart_id}:source.portable=<missing>")
            continue
        repo = str(portable.get("repo", "")).strip()
        chart_name = str(portable.get("chart", "")).strip()
        if not repo or not chart_name:
            bad_sources.append(f"{chart_id}:source.portable is incomplete")
            continue
        if _looks_like_local_path(repo):
            bad_sources.append(f"{chart_id}:{repo}")
            continue
        if repo.startswith(TREE_PREFIX) and (
            f"/tree/{release_ref}/" not in repo or "/tree/main/" in repo
        ):
            bad_sources.append(f"{chart_id}:{repo}")
            continue
        if "?ref=main" in repo:
            bad_sources.append(f"{chart_id}:{repo}")
            continue
        if source_block.get("local") not in (None, "", {}):
            bad_sources.append(f"{chart_id}:source.local is present")
    if bad_sources:
        raise ValueError(
            f"{subject} contains non-portable or incorrectly pinned app sources: "
            + ", ".join(bad_sources)
        )


def verify_catalog(*, catalog_path: Path, release_ref: str) -> None:
    payload = _load_yaml(catalog_path)
    modules = _infra_modules(payload, subject=str(catalog_path))
    charts = _app_charts(payload, subject=str(catalog_path))
    _validate_module_sources(modules, subject=str(catalog_path), release_ref=release_ref)
    _validate_chart_sources(charts, subject=str(catalog_path), release_ref=release_ref)


def verify_wheel(*, wheel_path: Path, release_ref: str) -> None:
    payload = _load_bundled_wheel_catalog(wheel_path)
    modules = _infra_modules(payload, subject=str(wheel_path))
    charts = _app_charts(payload, subject=str(wheel_path))
    _validate_module_sources(modules, subject=str(wheel_path), release_ref=release_ref)
    _validate_chart_sources(charts, subject=str(wheel_path), release_ref=release_ref)


def verify_wheel_bundle(*, wheel_path: Path) -> None:
    payload = _load_bundled_wheel_catalog(wheel_path)
    _infra_modules(payload, subject=str(wheel_path))
    _app_charts(payload, subject=str(wheel_path))


def _load_bundled_wheel_catalog(wheel_path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(wheel_path) as zf:
        candidate_names = [
            name for name in zf.namelist() if name.endswith(BUNDLED_COMPONENT_SOURCES_SUFFIX)
        ]
        if not candidate_names:
            raise ValueError(f"{wheel_path} is missing bundled {BUNDLED_COMPONENT_SOURCES_SUFFIX}")
        payload = yaml.safe_load(zf.read(candidate_names[0]).decode("utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"{wheel_path} bundled component sources root must be a mapping")
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    render = subparsers.add_parser("render", help="Render the release catalog with a pinned ref.")
    render.add_argument("--input", required=True, dest="input_path")
    render.add_argument("--output", required=True, dest="output_path")
    render.add_argument("--release-ref", required=True)

    verify_catalog_parser = subparsers.add_parser(
        "verify-catalog",
        help="Verify a rendered release catalog has portable pinned sources.",
    )
    verify_catalog_parser.add_argument("--catalog", required=True, dest="catalog_path")
    verify_catalog_parser.add_argument("--release-ref", required=True)

    verify_wheel_parser = subparsers.add_parser(
        "verify-wheel",
        help="Verify bundled component sources inside a wheel.",
    )
    verify_wheel_parser.add_argument("--wheel", required=True, dest="wheel_path")
    verify_wheel_parser.add_argument("--release-ref", required=True)

    verify_wheel_bundle_parser = subparsers.add_parser(
        "verify-wheel-bundle",
        help="Verify a wheel bundles component_sources.yaml with a valid catalog shape.",
    )
    verify_wheel_bundle_parser.add_argument("--wheel", required=True, dest="wheel_path")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.command == "render":
        render_release_catalog(
            input_path=Path(args.input_path),
            output_path=Path(args.output_path),
            release_ref=args.release_ref,
        )
        return
    if args.command == "verify-catalog":
        verify_catalog(catalog_path=Path(args.catalog_path), release_ref=args.release_ref)
        return
    if args.command == "verify-wheel":
        verify_wheel(wheel_path=Path(args.wheel_path), release_ref=args.release_ref)
        return
    if args.command == "verify-wheel-bundle":
        verify_wheel_bundle(wheel_path=Path(args.wheel_path))
        return
    parser.error(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
