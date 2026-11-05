"""Portable source inventories and canonical obligation coverage (no publication)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

from .contracts import ProjectSpecError, inspect_pair_bytes


SCHEMA = "maintain-project-specs/source-bundle-v1"
SHA = re.compile(r"[0-9a-f]{64}\Z")
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9._:-]{0,127}\Z")
MAX_FILE = 8 * 1024 * 1024
MAX_BUNDLE = 64 * 1024 * 1024


def fail(message):
    raise ProjectSpecError("SOURCE_BUNDLE_INVALID", message)


def relative_path(value):
    if not isinstance(value, str) or not value or "\\" in value:
        fail("source paths must be portable relative paths")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(p in {"..", ".git"} for p in path.parts):
        fail("source path escapes its project")
    if not path.parts:
        fail("source path is empty")
    return path


def read_source(root: Path, relative: str, limit=MAX_FILE) -> bytes:
    """Pin the root and every descendant; reject links and concurrent replacement."""
    parts = relative_path(relative).parts
    root = root.absolute()
    if root != root.resolve():
        fail("source root must not contain symlinks")
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_uid != os.getuid() or before.st_mode & 0o022 or before.st_size > limit):
                fail("source must be a bounded owner-controlled regular file")
            chunks = bytearray()
            while chunk := os.read(fd, min(65536, limit + 1 - len(chunks))):
                chunks.extend(chunk)
                if len(chunks) > limit:
                    fail("source exceeds its size bound")
            def identity(s):
                return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            if identity(before) != identity(os.fstat(fd)) or identity(before) != identity(os.stat(parts[-1], dir_fd=descriptor, follow_symlinks=False)):
                fail("source changed during observation")
            return bytes(chunks)
        finally:
            os.close(fd)
    finally:
        os.close(descriptor)


def _catalog_ids(value, field):
    result = []
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if field in item:
                result.append(item[field])
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return result


def validate_bundle(project: Path, manifest_path: str, expected_sha256: str, *, specs=True):
    relative_path(manifest_path)
    if not manifest_path.startswith("docs/"):
        fail("portable manifests must live under docs")
    data = read_source(project, manifest_path)
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        fail("source manifest changed; accept a new objective revision")
    value = json.loads(data)
    if (not isinstance(value, dict) or set(value) != {"schema", "sources", "coverage", "conflicts"}
            or value["schema"] != SCHEMA or value["conflicts"] != []
            or not isinstance(value["sources"], list) or not value["sources"]
            or not isinstance(value["coverage"], list)):
        fail("manifest shape or unresolved normative conflicts are invalid")
    paths, identifiers, size = set(), set(), len(data)
    obligation_sources = {}
    references = []
    for source in value["sources"]:
        if (not isinstance(source, dict) or set(source) != {"path", "sha256", "authority", "role", "ids", "references", "id_field"}
                or not isinstance(source["authority"], str) or source["authority"] not in {"normative", "informative"}
                or not isinstance(source["role"], str) or source["role"] not in {"requirements", "design", "contract", "acceptance", "context"}
                or not isinstance(source["ids"], list) or not isinstance(source["references"], list)
                or SHA.fullmatch(str(source["sha256"])) is None):
            fail("source inventory entry is invalid")
        relative_path(source["path"])
        if not source["path"].startswith("docs/contracts/"):
            fail("portable source snapshots must live under docs/contracts")
        if source["path"] in paths or source["path"] == manifest_path:
            fail("duplicate or self-referencing source path")
        paths.add(source["path"])
        body = read_source(project, source["path"])
        size += len(body)
        if size > MAX_BUNDLE or hashlib.sha256(body).hexdigest() != source["sha256"]:
            fail("source bytes changed or package exceeds its bound")
        ids = source["ids"]
        if any(not isinstance(i, str) or IDENTIFIER.fullmatch(i) is None for i in ids) or len(ids) != len(set(ids)):
            fail("source identifiers are invalid or duplicated")
        if (source["authority"] == "normative" and source["path"].endswith(".json")
                and source["id_field"] is None):
            fail("normative JSON catalogs require exact identifier extraction")
        if source["id_field"] is not None:
            if not isinstance(source["id_field"], str) or not source["id_field"]:
                fail("catalog identifier field is invalid")
            extracted = _catalog_ids(json.loads(body), source["id_field"])
            if any(not isinstance(i, str) for i in extracted) or len(extracted) != len(set(extracted)) or set(extracted) != set(ids):
                fail("catalog inventory omits or duplicates source identifiers")
        elif any(i.encode() not in body for i in ids):
            fail("source identifier is absent from its declared document")
        if source["authority"] == "normative":
            if identifiers.intersection(ids):
                fail("multiple normative definitions own the same identifier")
            identifiers.update(ids)
            obligation_sources.update({identifier: source["sha256"] for identifier in ids})
        references.extend(source["references"])
    if any(not isinstance(p, str) or p not in paths for p in references):
        fail("source reference closure is incomplete")
    if specs:
        pair = inspect_pair_bytes(read_source(project, "docs/requirements.md"), read_source(project, "docs/design.md"))
        if not pair["requirements"]["managed"] or not pair["design"]["managed"]:
            fail("canonical source mappings require owner-managed specifications")
        reqs = {r["id"] for r in pair["requirements"]["records"]}
        feats = {f["id"]: set(f["requirements"]) for f in pair["design"]["records"]}
    seen = set()
    for row in value["coverage"]:
        if (not isinstance(row, dict) or set(row) != {"source_id", "kind", "disposition", "requirements", "features", "reason"}
                or not isinstance(row["source_id"], str)
                or row["source_id"] not in identifiers or row["source_id"] in seen
                or not isinstance(row["kind"], str) or row["kind"] not in {"acceptance", "scoped-check", "release-gate", "decision", "contract"}
                or not isinstance(row["disposition"], str) or row["disposition"] not in {"required", "deferred", "rejected"}
                or not isinstance(row["reason"], str) or not row["reason"].strip()
                or not isinstance(row["requirements"], list) or not isinstance(row["features"], list)):
            fail("coverage identity or disposition is invalid")
        seen.add(row["source_id"])
        for field, prefix in (("requirements", "REQ"), ("features", "FEAT")):
            entries = row[field]
            if (any(not isinstance(v, str) or re.fullmatch(prefix + r"-[0-9]{3,}", v) is None for v in entries)
                    or len(entries) != len(set(entries))):
                fail("coverage mappings require unique canonical identifiers")
        if row["disposition"] == "required" and (not row["requirements"] or not row["features"]):
            fail("required source obligations need requirement and feature mappings")
        if specs and (not set(row["requirements"]) <= reqs or any(f not in feats for f in row["features"])
                      or not set(row["requirements"]) <= set().union(*(feats[f] for f in row["features"]))):
            fail("source mapping disagrees with canonical requirements/design")
    if seen != identifiers:
        fail("every normative identifier needs an explicit coverage disposition")
    obligation_basis = {row["source_id"]: {"source_sha256": obligation_sources[row["source_id"]],
                        "coverage_sha256": hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()}
                        for row in value["coverage"]}
    return {"sha256": expected_sha256, "coverage": value["coverage"], "obligation_basis": obligation_basis,
            "paths": sorted(paths | {manifest_path}),
            "source_count": len(paths), "obligation_count": len(seen)}
