"""Helpers for rendering and verifying the portable release catalog."""

from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path
from typing import Any

import yaml

REPO_PREFIX = "git::https://github.com/nebius/nebius-ps-services.git//"
LOCAL_PATH_PATTERN = re.compile(r"^(?:\.\.?/|/|[A-Za-z]:[\\/])")
BUNDLED_COMPONENT_SOURCES_SUFFIX = "nebius_cxcli/component_sources.yaml"


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"{path} root must be a mapping")
    return payload


def _infra_modules(payload: dict[str, Any], *, subject: str) -> list[dict[str, Any]]:
    modules = (((payload or {}).get("infra") or {}).get("tf_modules") or [])
    if not isinstance(modules, list) or not modules:
        raise ValueError(f"{subject} has no infra.tf_modules entries")
    normalized: list[dict[str, Any]] = []
    for module in modules:
        if isinstance(module, dict):
            normalized.append(module)
    return normalized


def render_release_catalog(
    *,
    input_path: Path,
    output_path: Path,
    release_ref: str,
) -> None:
    payload = _load_yaml(input_path)
    modules = _infra_modules(payload, subject=str(input_path))
    for module in modules:
        source = str(module.get("source", "")).strip()
        if source.startswith(REPO_PREFIX):
            module["source"] = source.replace("?ref=main", f"?ref={release_ref}")
    output_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _validate_module_sources(
    modules: list[dict[str, Any]],
    *,
    subject: str,
    release_ref: str,
) -> None:
    bad_sources: list[str] = []
    for module in modules:
        source = str(module.get("source", "")).strip()
        if not source:
            bad_sources.append("<empty>")
            continue
        if LOCAL_PATH_PATTERN.match(source):
            bad_sources.append(source)
            continue
        if source.startswith(REPO_PREFIX):
            if f"?ref={release_ref}" not in source or "?ref=main" in source:
                bad_sources.append(source)
            continue
        if "?ref=main" in source:
            bad_sources.append(source)
    if bad_sources:
        raise ValueError(
            f"{subject} contains non-portable or incorrectly pinned module sources: "
            + ", ".join(bad_sources)
        )


def verify_catalog(*, catalog_path: Path, release_ref: str) -> None:
    payload = _load_yaml(catalog_path)
    modules = _infra_modules(payload, subject=str(catalog_path))
    _validate_module_sources(modules, subject=str(catalog_path), release_ref=release_ref)


def verify_wheel(*, wheel_path: Path, release_ref: str) -> None:
    with zipfile.ZipFile(wheel_path) as zf:
        candidate_names = [
            name for name in zf.namelist() if name.endswith(BUNDLED_COMPONENT_SOURCES_SUFFIX)
        ]
        if not candidate_names:
            raise ValueError(
                f"{wheel_path} is missing bundled {BUNDLED_COMPONENT_SOURCES_SUFFIX}"
            )
        payload = yaml.safe_load(zf.read(candidate_names[0]).decode("utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"{wheel_path} bundled component sources root must be a mapping")
    modules = _infra_modules(payload, subject=str(wheel_path))
    _validate_module_sources(modules, subject=str(wheel_path), release_ref=release_ref)


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
    parser.error(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
