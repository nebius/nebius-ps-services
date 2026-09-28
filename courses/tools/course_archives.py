"""Deterministic course downloads shared by authoring and evidence export.

Only manifest-owned public evidence enters results archives. This module has
no runtime/cluster dependencies and never changes canonical source files.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path

MAX_FILE_BYTES = 104_857_600

FIXED_TIME = (2026, 1, 1, 0, 0, 0)


def source_bytes(course: Path, path: Path) -> bytes:
    if (
        not path.is_file()
        or not path.resolve().is_relative_to(course.resolve())
        or any(p.is_symlink() for p in (path, *path.parents))
    ):
        raise ValueError(f"missing or unsafe download asset: {path.name}")
    return path.read_bytes()


def course_identity(course: Path) -> tuple[str, dict]:
    metadata = json.loads(source_bytes(course, course / "reference/course.json"))
    slug = metadata["slug"]
    if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError("invalid course archive identity")
    return slug, metadata


def result_entries(course: Path) -> dict[str, tuple[bytes, int]]:
    slug, metadata = course_identity(course)
    labs = {Path(row["path"]).stem for row in metadata["labs"]}
    dashboards = {"reference/grafana/environment_readiness.json"} | {
        row["dashboard"] for row in metadata["labs"]
    }
    actual = {
        p.relative_to(course).as_posix()
        for p in (course / "reference/grafana").glob("*.json")
    }
    if actual != dashboards:
        raise ValueError("dashboard inventory differs from course metadata")
    entries = {}
    for relative in sorted(dashboards):
        path = Path(relative)
        if path.parent.as_posix() != "reference/grafana" or path.suffix != ".json":
            raise ValueError("unsafe dashboard path")
        content = source_bytes(course, course / path)
        json.loads(content)
        entries["grafana-dashboards/" + path.name] = (content, 0o644)
    root = course / "reference/lab-results"
    if root.is_symlink():
        raise ValueError("public evidence directory must not be a symlink")
    for manifest in sorted(root.glob("*/*/manifest.json")):
        raw = source_bytes(course, manifest)
        data = json.loads(raw)
        lab, profile = manifest.parent.parent.name, manifest.parent.name
        if (
            lab not in labs
            or profile not in ("small", "large")
            or data.get("schema") != "course-lab-results/v1"
            or data.get("course") != slug
            or data.get("lab") != lab
            or data.get("profile") != profile
        ):
            raise ValueError("public evidence identity differs from course metadata")
        prefix = f"{profile}/{lab}/"
        entries[prefix + "manifest.json"] = (raw, 0o644)
        rows = [
            {"file": "summary.csv", "sha256": data["summary_sha256"]},
            *data["artifacts"],
        ]
        seen = {"manifest.json"}
        for row in rows:
            name = row["file"]
            if (
                not isinstance(name, str)
                or Path(name).name != name
                or "\\" in name
                or name in seen
                or Path(name).suffix not in (".json", ".csv", ".png")
            ):
                raise ValueError("invalid or duplicate public bundle artifact")
            seen.add(name)
            content = source_bytes(course, manifest.parent / name)
            if hashlib.sha256(content).hexdigest() != row["sha256"]:
                raise ValueError("Public artifact differs from its manifest")
            entries[prefix + name] = (content, 0o644)
    return entries


def archive_bytes(entries: dict[str, tuple[bytes, int]]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, (content, mode) in sorted(entries.items()):
            path = Path(name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in name
                or name != path.as_posix()
                or not path.parts
            ):
                raise ValueError("unsafe archive member")
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = mode << 16
            archive.writestr(info, content)
    content = output.getvalue()
    if len(content) > MAX_FILE_BYTES:
        raise ValueError(
            f"Results archive exceeds {MAX_FILE_BYTES} bytes: {len(content)}"
        )
    return content


def write_atomic(destination: Path, content: bytes) -> None:
    if any(p.is_symlink() for p in (destination, *destination.parents)):
        raise ValueError("generated destination must not use symlinks")
    if destination.is_file() and destination.read_bytes() == content:
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=".course-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        temporary.chmod(0o644)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def publish_results(course: Path) -> Path:
    """Export under the caller's existing course lock, after complete validation."""
    slug, _ = course_identity(course)
    content = archive_bytes(result_entries(course))
    output = course / "reference" / f"{slug}-lab-results.zip"
    write_atomic(output, content)
    return output
