#!/usr/bin/env python3
"""Mechanical extraction support; the agent owns interpretation and research."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import sys
import tempfile
import time

CATALOG = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CATALOG / "maintain-project-specs/scripts"))
from project_specs_lib.source_bundle import MAX_BUNDLE, read_source, relative_path  # noqa: E402
from project_specs_lib.roadmap import validate_roadmap  # noqa: E402
from project_specs_lib.contracts import ProjectSpecError, canonical_digest  # noqa: E402
from project_specs_lib.targets import resolve_target  # noqa: E402
from project_specs_lib.transaction import spec_pair_lock  # noqa: E402

STAGES = (
    "inventory",
    "requirements",
    "research",
    "architecture",
    "review",
    "planning",
    "publication",
)
UNSET = object()
EXTENSIONS = {
    ".md",
    ".txt",
    ".rst",
    ".adoc",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".html",
    ".htm",
    ".pdf",
    ".docx",
    ".csv",
    ".tsv",
    ".xlsx",
    ".pptx",
    ".png",
    ".jpg",
    ".jpeg",
    ".svg",
    ".webp",
}
SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".cache",
}
GENERATED = ("docs/contracts/design-extractions", "docs/reports/design-extractions")
SECRET = re.compile(
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bAKIA[0-9A-Z]{16}\b"
)


def fail(message):
    raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode()


def read_json(path):
    return json.loads(read_source(path.parent.resolve(), path.name, MAX_BUNDLE))


def listing(source):
    rows = []

    def unreadable(error):
        try:
            path = Path(error.filename).relative_to(source).as_posix()
        except (TypeError, ValueError):
            path = "."
        rows.append(
            {
                "path": path,
                "status": "unreadable",
                "reason": "directory could not be inventoried",
            }
        )

    for directory, dirs, files in os.walk(
        source, followlinks=False, onerror=unreadable
    ):
        base = Path(directory)
        for name in sorted(dirs[:]):
            path = base / name
            relative = path.relative_to(source).as_posix()
            generated = (
                name == "design-extractions"
                and base.name in {"contracts", "reports"}
                and base.parent.name == "docs"
            )
            if (
                path.is_symlink()
                or name in SKIP_DIRS
                or relative in GENERATED
                or generated
            ):
                dirs.remove(name)
                rows.append(
                    {
                        "path": relative,
                        "status": "excluded",
                        "reason": "generated, dependency, or linked directory",
                    }
                )
        for name in sorted(files):
            path = base / name
            relative = path.relative_to(source).as_posix()
            status = "source"
            if (
                path.is_symlink()
                or name.startswith(".env")
                or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}
            ):
                status = "excluded"
            elif path.suffix.lower() not in EXTENSIONS:
                status = "unsupported"
            rows.append({"path": relative, "status": status})
    return sorted(rows, key=lambda row: row["path"])


def snapshot(source, target):
    source = source.expanduser().absolute()
    if source != source.resolve() or not source.is_dir():
        fail("source must be a real directory without symlinks")
    identity = resolve_target(target)
    scratch = Path(tempfile.mkdtemp(prefix="design-intake-")).resolve()
    try:
        return _capture_snapshot(source, identity, scratch)
    except BaseException:
        shutil.rmtree(scratch)
        raise


def _capture_snapshot(source, identity, scratch):
    rows = listing(source)
    total = 0
    for row in rows:
        if row["status"] != "source":
            row.setdefault(
                "reason",
                "unsupported format or sensitive/linked path; review this omission",
            )
            continue
        try:
            data = read_source(source, row["path"])
            if SECRET.search(data):
                fail("potential secret material cannot enter the snapshot")
            total += len(data)
            if total > MAX_BUNDLE:
                fail("source package exceeds inventory byte limit")
            row["sha256"] = sha(data)
            destination = scratch / "sources" / row["path"]
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            destination.write_bytes(data)
            destination.chmod(0o600)
        except (OSError, ValueError, ProjectSpecError):
            row.update(
                status="unreadable",
                reason="source unsafe, unavailable, changing, sensitive, or over size limit",
            )
    if [
        (
            r["path"],
            r["status"] if r["status"] in {"excluded", "unsupported"} else "source",
        )
        for r in rows
    ] != [
        (
            r["path"],
            r["status"] if r["status"] in {"excluded", "unsupported"} else "source",
        )
        for r in listing(source)
    ]:
        fail(
            "source inventory changed during capture; retry with a stable source folder"
        )
    for row in rows:
        if (
            row["status"] == "source"
            and sha(read_source(source, row["path"])) != row["sha256"]
        ):
            fail("source changed during capture; retry with a stable source folder")
    identity.check()
    value = {
        "schema": "design/source-inventory-v1",
        "sources": rows,
        "snapshot_sha256": canonical_digest(rows),
        "target_kind": identity.kind,
        "target_basis": identity.basis,
    }
    (scratch / "snapshot.json").write_bytes(encode(value))
    (scratch / "snapshot.json").chmod(0o600)
    return {
        "snapshot": str(scratch / "snapshot.json"),
        "source_count": sum(r["status"] == "source" for r in rows),
        "omission_count": sum(r["status"] != "source" for r in rows),
    }


def verify_snapshot(path):
    inventory = read_json(path)
    validate_inventory(inventory)
    for row in inventory["sources"]:
        if (
            row["status"] == "source"
            and sha(read_source(path.parent / "sources", row["path"])) != row["sha256"]
        ):
            fail("frozen source snapshot changed")
    return inventory


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def keyed(rows, label):
    if not isinstance(rows, list) or any(
        not isinstance(row, dict) or not nonempty(row.get("id")) for row in rows
    ):
        fail(label + " must contain identified records")
    result = {row["id"]: row for row in rows}
    if len(result) != len(rows):
        fail(label + " identifiers must be unique")
    return result


def validate_inventory(inventory):
    if (
        not isinstance(inventory, dict)
        or inventory.get("schema") != "design/source-inventory-v1"
    ):
        fail("source inventory schema is invalid")
    rows = inventory.get("sources")
    if not isinstance(rows, list):
        fail("source inventory needs source records")
    paths = set()
    for row in rows:
        if not isinstance(row, dict) or not nonempty(row.get("path")):
            fail("source inventory path is invalid")
        path = row["path"]
        if path != ".":
            relative_path(path)
        if path in paths or row.get("status") not in {
            "source",
            "excluded",
            "unsupported",
            "unreadable",
        }:
            fail("source inventory status or uniqueness is invalid")
        paths.add(path)
        if row["status"] == "source" and (
            path == "."
            or not isinstance(row.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
        ):
            fail("source inventory digest is invalid")
    if canonical_digest(rows) != inventory.get("snapshot_sha256"):
        fail("source inventory identity is invalid")


def validate_review(inventory, review, pair=None):
    validate_inventory(inventory)
    if (
        not isinstance(review, dict)
        or review.get("schema") != "design/review-v1"
        or review.get("snapshot_sha256") != inventory["snapshot_sha256"]
    ):
        fail("review is not bound to this snapshot")
    sources = {row["path"]: row for row in inventory["sources"]}
    dispositions = review.get("source_reviews")
    if not isinstance(dispositions, dict) or set(dispositions) != set(sources):
        fail("every discovered source or omission needs an explicit review disposition")
    for path, row in dispositions.items():
        if (
            not isinstance(row, dict)
            or row.get("disposition")
            not in {"reviewed", "context", "excluded", "unreadable"}
            or not nonempty(row.get("reason"))
        ):
            fail("source review disposition is invalid")
        if sources[path]["status"] != "source" and row["disposition"] in {
            "reviewed",
            "context",
        }:
            fail("unavailable source cannot be claimed reviewed")
    evidence = keyed(review.get("evidence"), "evidence")
    for row in evidence.values():
        if row.get("kind") not in {"local", "primary", "secondary"} or any(
            not nonempty(row.get(key))
            for key in ("reference", "claim", "checked_at", "version")
        ):
            fail(
                "evidence needs provenance, claim, version scope, and observation date"
            )
    items = keyed(review.get("items"), "items")
    if not items:
        fail("empty extraction cannot establish coverage")
    canonical_reqs = (
        {r["id"]: r for r in pair["requirements"]["records"]} if pair else {}
    )
    canonical_feats = {r["id"]: r for r in pair["design"]["records"]} if pair else {}
    reviewed_sources = set()
    for row in items.values():
        if row.get("kind") not in {
            "requirement",
            "design",
            "contract",
            "acceptance",
        } or row.get("status") not in {
            "supported",
            "review-required",
            "deferred",
            "rejected",
        }:
            fail("item kind or review status is invalid")
        if any(
            not nonempty(row.get(key))
            for key in ("layer", "summary", "rationale", "validation")
        ):
            fail("each item needs a layer, decision rationale and validation method")
        if not isinstance(row.get("sources"), list) or not row["sources"]:
            fail("each item needs original-source locators")
        for locator in row["sources"]:
            if (
                not isinstance(locator, dict)
                or locator.get("path") not in sources
                or not nonempty(locator.get("locator"))
            ):
                fail("source locator is invalid")
            if sources[locator["path"]]["status"] != "source":
                fail("item cannot claim evidence from unavailable source")
            reviewed_sources.add(locator["path"])
        references = row.get("evidence")
        if not isinstance(references, list) or any(
            not isinstance(ref, str) or ref not in evidence for ref in references
        ):
            fail("item evidence references are invalid")
        if row["status"] == "supported" and not references:
            fail("supported item requires evidence coverage")
        for field, prefix in (("requirements", "REQ"), ("features", "FEAT")):
            values = row.get(field)
            if not isinstance(values, list) or any(
                not isinstance(v, str) or not re.fullmatch(prefix + r"-\d{3,}", v)
                for v in values
            ):
                fail("canonical item mappings are invalid")
            if row["status"] in {"supported", "review-required"} and not values:
                fail("accepted and unresolved items need canonical mappings")
        if pair:
            if (
                not set(row["requirements"]) <= canonical_reqs.keys()
                or not set(row["features"]) <= canonical_feats.keys()
            ):
                fail("review references missing canonical requirements or features")
            covered = set().union(
                *(
                    canonical_feats[feature]["requirements"]
                    for feature in row["features"]
                )
            )
            if not set(row["requirements"]) <= covered:
                fail("canonical features do not cover item requirements")
            for feature in row["features"]:
                record = canonical_feats[feature]
                if row["status"] == "review-required" and record["status"] not in {
                    "draft",
                    "blocked",
                }:
                    fail("unresolved item has a ready canonical design")
    if any(
        row["disposition"] == "reviewed" and path not in reviewed_sources
        for path, row in dispositions.items()
    ):
        fail("reviewed source has no extracted item mapping")
    findings = keyed(review.get("findings"), "findings")
    unresolved = set()
    for row in findings.values():
        if not re.fullmatch(r"GAP-\d{3,}", row["id"]) or row.get("status") not in {
            "resolved",
            "review-required",
        }:
            fail("finding identity or status is invalid")
        if row.get("kind") not in {"decision", "evidence", "coverage", "planning"}:
            fail("finding kind is invalid")
        if any(
            not nonempty(row.get(key))
            for key in ("description", "research_summary", "next_action")
        ):
            fail("finding needs research and actionable follow-up")
        if not isinstance(row.get("items"), list) or any(
            not isinstance(item, str) or item not in items for item in row["items"]
        ):
            fail("finding item references are invalid")
        if (
            not isinstance(row.get("sources"), list)
            or not row["sources"]
            or any(
                not isinstance(path, str) or path not in sources
                for path in row["sources"]
            )
        ):
            fail("finding must reference original source files")
        located_paths = {
            locator["path"]
            for item in row["items"]
            for locator in items[item]["sources"]
        }
        locators = row.get("locators", [])
        if not isinstance(locators, list):
            fail("finding locators must be a list")
        for locator in locators:
            if (
                not isinstance(locator, dict)
                or locator.get("path") not in row["sources"]
                or not nonempty(locator.get("locator"))
            ):
                fail("finding locator is invalid")
            located_paths.add(locator["path"])
        if not set(row["sources"]) <= located_paths:
            fail("finding needs precise original-source locators")
        if row["status"] == "review-required":
            unresolved.update(row["items"])
    if any(
        row["status"] == "review-required" and key not in unresolved
        for key, row in items.items()
    ):
        fail("unresolved item requires an end-of-run finding")
    if any(items[key]["status"] == "supported" for key in unresolved):
        fail("unresolved finding contradicts supported item readiness")
    for path, row in dispositions.items():
        if (
            row["disposition"] == "unreadable"
            or sources[path]["status"] in {"unreadable", "unsupported"}
        ) and not any(
            path in f["sources"] and f["status"] == "review-required"
            for f in findings.values()
        ):
            fail("unreadable or unsupported source requires an end-of-run finding")
    obligations = {
        key
        for key, row in items.items()
        if row["status"] in {"supported", "review-required"}
    }
    roadmap = review.get("roadmap")
    if roadmap is not None:
        validate_roadmap(roadmap, obligations)
    elif not any(
        row["status"] == "review-required" and row["kind"] == "planning"
        for row in findings.values()
    ):
        fail("missing roadmap requires an unresolved planning finding")
    return {
        "status": "review-required"
        if any(row["status"] == "review-required" for row in findings.values())
        else "reviewed",
        "items_reviewed": len(items),
        "supported_items": sum(r["status"] == "supported" for r in items.values()),
        "unresolved_findings": sum(
            r["status"] == "review-required" for r in findings.values()
        ),
        "revision": canonical_digest(
            {"snapshot": inventory["snapshot_sha256"], "review": review}
        ),
    }


def progress(
    state,
    stage,
    layer=None,
    reviewed=None,
    total=UNSET,
    findings=None,
    now=None,
    *,
    resume=False,
):
    now = time.time() if now is None else now
    previous = state or {}
    layer = previous.get("layer", "") if layer is None else layer
    reviewed = previous.get("reviewed", 0) if reviewed is None else reviewed
    total = previous.get("total") if total is UNSET else total
    findings = previous.get("findings", 0) if findings is None else findings
    if (
        stage not in STAGES
        or reviewed < 0
        or findings < 0
        or total is not None
        and (total < reviewed or total < 0)
    ):
        fail("invalid stage or review counters")
    if state is None:
        state = {
            "schema": "design/progress-v1",
            "started_at": now,
            "updated_at": now,
            "stage": stage,
            "stage_seconds": {key: 0 for key in STAGES},
            "unobserved_seconds": 0,
        }
    if state.get("schema") != "design/progress-v1" or now < state["updated_at"]:
        fail("progress clock or state is invalid")
    if resume:
        state["unobserved_seconds"] += now - state["updated_at"]
    else:
        state["stage_seconds"][state["stage"]] += now - state["updated_at"]
    state.update(
        updated_at=now,
        stage=stage,
        layer=layer,
        reviewed=reviewed,
        total=total,
        findings=findings,
    )
    elapsed = int(now - state["started_at"])
    timer = f"{elapsed // 3600:02d}:{elapsed % 3600 // 60:02d}:{elapsed % 60:02d}"
    if total is None:
        bar = f"{reviewed} items discovered; total pending"
    else:
        count = int(10 * reviewed / total) if total else 0
        bar = (
            "["
            + "█" * count
            + "░" * (10 - count)
            + f"] {reviewed}/{total} items reviewed"
        )
    return (
        state,
        f"{stage} · {layer or 'all layers'} | {bar} | Open findings: {findings} | Elapsed: {timer}",
    )


def _directory(parent, name):
    try:
        os.mkdir(name, 0o755, dir_fd=parent)
    except FileExistsError:
        pass
    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    value = os.fstat(child)
    if value.st_uid != os.getuid() or value.st_mode & 0o022:
        os.close(child)
        fail("artifact directory has unsafe ownership or permissions")
    return child


def _recover_pending_link(directory, name, expected, check_binding):
    """Recover only an exact final/pending inode pair left after atomic linking."""
    descriptor = os.open(
        name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
    )
    try:
        before = os.fstat(descriptor)
        if before.st_nlink == 1:
            return
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 2
            or before.st_uid != os.getuid()
            or before.st_mode & 0o022
            or before.st_size != len(expected)
        ):
            fail("existing artifact is not a recoverable pending publication")
        matches = []
        for entry in os.listdir(directory):
            if re.fullmatch(r"\.pending-[0-9a-f]{24}", entry):
                info = os.stat(entry, dir_fd=directory, follow_symlinks=False)
                if (info.st_dev, info.st_ino) == (before.st_dev, before.st_ino):
                    matches.append(entry)
        if len(matches) != 1:
            fail("existing artifact has an unrelated hard link")
        with os.fdopen(os.dup(descriptor), "rb") as handle:
            if handle.read(len(expected) + 1) != expected:
                fail("immutable artifact conflicts with existing bytes")
        check_binding()

        def identity(info):
            return (
                info.st_dev,
                info.st_ino,
                info.st_mode,
                info.st_uid,
                info.st_nlink,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            )

        for current in (
            os.fstat(descriptor),
            os.stat(name, dir_fd=directory, follow_symlinks=False),
            os.stat(matches[0], dir_fd=directory, follow_symlinks=False),
        ):
            if identity(current) != identity(before):
                fail("pending publication changed during recovery")
        os.unlink(matches[0], dir_fd=directory)
        os.fsync(directory)
    finally:
        os.close(descriptor)


def publish_assets(package, target, revision, *, expected_target_basis):
    """Create immutable supplementary files; canonical publication is separate."""
    if not re.fullmatch(r"[0-9a-f]{64}", revision):
        fail("revision must be the validated review digest")
    identity = resolve_target(target)
    if identity.basis != expected_target_basis:
        fail("original target identity changed")
    from project_specs_lib.contracts import load_project_config

    if (
        load_project_config(identity.project, require_git=identity.kind == "git")[
            "mode"
        ]
        == "disabled"
    ):
        fail("project specifications are disabled")
    if package.absolute() != package.resolve() or not package.is_dir():
        fail("artifact package must be a real directory")
    files = []
    total = 0
    for path in sorted(package.rglob("*")):
        relative = path.relative_to(package).as_posix()
        if path.is_symlink():
            fail("artifact package contains a symlink")
        if path.is_dir():
            continue
        relative_path(relative)
        if len(Path(relative).parts) < 2 or Path(relative).parts[0] not in {
            "contracts",
            "reports",
        }:
            fail("artifact files must be under contracts or reports")
        data = read_source(package.resolve(), relative)
        total += len(data)
        if total > MAX_BUNDLE:
            fail("artifact package is too large")
        if SECRET.search(data):
            fail("potential secret cannot be published")
        files.append((relative, data))
    if not files:
        fail("artifact package is empty")
    written = []
    with spec_pair_lock(identity.project, identity.storage_root):
        identity.check()
        for relative, data in files:
            identity.check()
            parts = Path(relative).parts
            destination = ("docs", parts[0], "design-extractions", revision, *parts[1:])
            fd = os.open(identity.project, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptors = [fd]
            bindings = []
            try:
                for part in destination[:-1]:
                    child = _directory(fd, part)
                    bindings.append((fd, part, child))
                    descriptors.append(child)
                    fd = child
                from project_specs_lib.transaction import (
                    _assert_child_binding,
                    _assert_directory_binding,
                )

                def check_binding():
                    identity.check()
                    _assert_directory_binding(
                        identity.project, descriptors[0], "project"
                    )
                    for parent, component, descriptor in bindings:
                        _assert_child_binding(parent, component, descriptor, "artifact")

                check_binding()
                name = destination[-1]
                temp = ".pending-" + secrets.token_hex(12)
                out = os.open(
                    temp,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o644,
                    dir_fd=fd,
                )
                try:
                    with os.fdopen(out, "wb") as handle:
                        handle.write(data)
                        handle.flush()
                        os.fsync(handle.fileno())
                    try:
                        check_binding()
                        os.link(
                            temp,
                            name,
                            src_dir_fd=fd,
                            dst_dir_fd=fd,
                            follow_symlinks=False,
                        )
                    except FileExistsError:
                        _recover_pending_link(fd, name, data, check_binding)
                        if read_source(identity.project, "/".join(destination)) != data:
                            fail("immutable artifact conflicts with existing bytes")
                finally:
                    os.unlink(temp, dir_fd=fd)
                os.fsync(fd)
                check_binding()
                written.append({"path": "/".join(destination), "sha256": sha(data)})
            finally:
                for descriptor in reversed(descriptors):
                    os.close(descriptor)
        identity.check()
    return {
        "status": "assets-published",
        "revision": revision,
        "files": written,
        "canonical_pair": "not-published-by-this-helper",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    snap = actions.add_parser("snapshot")
    snap.add_argument("--source-folder", type=Path, required=True)
    snap.add_argument("--project-root", type=Path, default=Path.cwd())
    validate = actions.add_parser("validate-review")
    validate.add_argument("--snapshot", type=Path, required=True)
    validate.add_argument("--review", type=Path, required=True)
    validate.add_argument("--requirements-candidate", type=Path)
    validate.add_argument("--design-candidate", type=Path)
    show = actions.add_parser("progress")
    show.add_argument("--state", type=Path, required=True)
    show.add_argument("--stage", choices=STAGES, required=True)
    show.add_argument("--layer")
    show.add_argument("--reviewed", type=int)
    denominator = show.add_mutually_exclusive_group()
    denominator.add_argument("--total", type=int, default=UNSET)
    denominator.add_argument(
        "--unknown-total", dest="total", action="store_const", const=None
    )
    show.add_argument("--findings", type=int)
    show.add_argument(
        "--resume",
        action="store_true",
        help="Keep interruption time unattributed to a phase",
    )
    publish = actions.add_parser("publish-assets")
    publish.add_argument("--package", type=Path, required=True)
    publish.add_argument("--project-root", type=Path, required=True)
    publish.add_argument("--revision", required=True)
    publish.add_argument("--snapshot", type=Path, required=True)
    publish.add_argument("--review", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "snapshot":
            result = snapshot(args.source_folder, args.project_root)
        elif args.action == "validate-review":
            pair = None
            if bool(args.requirements_candidate) != bool(args.design_candidate):
                fail("supply both canonical candidates together")
            if args.requirements_candidate:
                from project_specs_lib.contracts import inspect_pair_bytes

                pair = inspect_pair_bytes(
                    read_source(
                        args.requirements_candidate.parent.resolve(),
                        args.requirements_candidate.name,
                    ),
                    read_source(
                        args.design_candidate.parent.resolve(),
                        args.design_candidate.name,
                    ),
                )
            result = validate_review(
                verify_snapshot(args.snapshot), read_json(args.review), pair
            )
        elif args.action == "publish-assets":
            inventory = verify_snapshot(args.snapshot)
            reviewed = validate_review(inventory, read_json(args.review))
            if (
                reviewed["revision"] != args.revision
                or resolve_target(args.project_root).basis != inventory["target_basis"]
            ):
                fail("review revision or original target identity changed")
            result = publish_assets(
                args.package,
                args.project_root,
                args.revision,
                expected_target_basis=inventory["target_basis"],
            )
        else:
            if not args.state.absolute().is_relative_to(
                Path(tempfile.gettempdir()).resolve()
            ):
                fail("progress state must be in task-owned temporary scratch")
            if (
                args.state.is_symlink()
                or args.state.parent.resolve() != args.state.parent.absolute()
            ):
                fail("progress state path must not contain symlinks")
            state = read_json(args.state) if args.state.exists() else None
            previous = json.loads(json.dumps(state))
            state, display = progress(
                state,
                args.stage,
                args.layer,
                args.reviewed,
                args.total,
                args.findings,
                resume=args.resume,
            )
            from project_specs_lib.transaction import _replace_at, _read_optional_at

            parent = os.open(
                args.state.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            try:
                prior = _read_optional_at(parent, args.state.name, "progress state")
                if (json.loads(prior) if prior is not None else None) != previous:
                    fail("progress state changed concurrently")
                _replace_at(parent, args.state.name, encode(state), prior)
                os.chmod(args.state.name, 0o600, dir_fd=parent, follow_symlinks=False)
            finally:
                os.close(parent)
            result = {
                "display": display,
                "elapsed_seconds": state["updated_at"] - state["started_at"],
                "observed_at": datetime.now(timezone.utc).isoformat(),
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, ProjectSpecError) as error:
        print(json.dumps({"status": "blocked", "error": str(error)}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
