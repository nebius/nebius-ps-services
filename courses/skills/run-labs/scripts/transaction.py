"""Journaled replacement of owned evidence, preserving unrelated files."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from run_labs_common import directory, fsync_dir, read, safe_path, write

OWNER = ".run-labs-owned.json"


def files(root):
    if not root.exists():
        return set()
    result = set()
    for p in root.rglob("*"):
        if p.is_symlink():
            raise ValueError("Evidence trees cannot contain symlinks")
        if p.is_file():
            result.add(p.relative_to(root).as_posix())
    return result


def owned(root):
    if not root.exists():
        return set()
    path = root / OWNER
    if not path.exists():
        return set()
    doc = read(path)
    if doc.get("schema") != "run-labs-owned/v1":
        raise ValueError("Foreign evidence owner")
    names = set(doc["files"])
    if any(Path(n).is_absolute() or ".." in Path(n).parts for n in names):
        raise ValueError("Unsafe owned file path")
    return names | {OWNER}


def prepare(incoming, final, identity, *, private):
    safe_path(incoming)
    safe_path(final)
    if not incoming.is_dir():
        raise ValueError("Missing staged evidence")
    previous = owned(final)
    existing = files(final)
    supplied = files(incoming)
    if OWNER in supplied:
        raise ValueError("Incoming set must not supply ownership metadata")
    if (existing - previous) & supplied:
        raise ValueError("New evidence would overwrite an unrelated file")
    for name in sorted(existing - previous):
        source = final / name
        target = incoming / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    write(
        incoming / OWNER,
        {
            "schema": "run-labs-owned/v1",
            "identity": identity,
            "files": sorted(supplied),
        },
        private=private,
    )
    for p in incoming.rglob("*"):
        if p.is_file():
            with p.open("rb") as stream:
                os.fsync(stream.fileno())
    fsync_dir(incoming)


def recover(journal, destinations):
    """Rollback an interrupted pre-commit replacement; complete committed cleanup."""
    if not journal.exists():
        return
    doc = read(journal)
    allowed = {str(safe_path(p)) for p in destinations}
    if (
        doc.get("schema") != "run-labs-replacement/v1"
        or {r["final"] for r in doc["rows"]} != allowed
    ):
        raise ValueError("Replacement journal does not match selected destinations")
    for row in doc["rows"]:
        final = Path(row["final"])
        backup = Path(row["backup"])
        incoming = Path(row["incoming"])
        if backup != final.with_name(
            "." + final.name + ".run-labs-backup"
        ) or incoming != final.with_name("." + final.name + ".run-labs-incoming"):
            raise ValueError("Unsafe replacement journal")
        safe_path(final)
        safe_path(backup)
        safe_path(incoming)
        if doc["phase"] == "committed":
            if backup.exists():
                shutil.rmtree(backup)
        elif doc["phase"] != "preparing":
            if backup.exists():
                if final.exists():
                    if read(final / OWNER).get("identity") != doc["identity"]:
                        raise ValueError("Refuse rollback over a changed destination")
                    shutil.rmtree(final)
                os.replace(backup, final)
            elif not row["existed"] and final.exists():
                if read(final / OWNER).get("identity") != doc["identity"]:
                    raise ValueError("Refuse rollback over foreign output")
                shutil.rmtree(final)
        if incoming.exists():
            shutil.rmtree(incoming)
        fsync_dir(final.parent)
    journal.unlink()
    fsync_dir(journal.parent)


def replace_sets(journal, pairs, identity, *, fail_after=None):
    """Caller holds the per-lab/profile lock. Pairs: (staged, final, private)."""
    destinations = [final for _, final, _ in pairs]
    recover(journal, destinations)
    rows = []
    for staged, final, private in pairs:
        safe_path(staged)
        safe_path(final)
        directory(final.parent, private=private)
        incoming = final.with_name("." + final.name + ".run-labs-incoming")
        backup = final.with_name("." + final.name + ".run-labs-backup")
        if incoming.exists() or backup.exists():
            raise ValueError("Unjournaled replacement files need review")
        rows.append(
            {
                "final": str(final),
                "incoming": str(incoming),
                "backup": str(backup),
                "existed": final.exists(),
            }
        )
    doc = {
        "schema": "run-labs-replacement/v1",
        "identity": identity,
        "phase": "preparing",
        "rows": rows,
    }
    # Record exact ownership before copying even the first potentially large file.
    write(journal, doc)
    try:
        for (staged, final, private), row in zip(pairs, rows):
            incoming = Path(row["incoming"])
            shutil.copytree(staged, incoming)
            if private:
                incoming.chmod(0o700)
            prepare(incoming, final, identity, private=private)
        doc["phase"] = "replacing"
        write(journal, doc)
        for index, row in enumerate(rows):
            final = Path(row["final"])
            if row["existed"]:
                os.replace(final, row["backup"])
            os.replace(row["incoming"], final)
            fsync_dir(final.parent)
            if fail_after == index:
                raise RuntimeError("Injected replacement interruption")
        doc["phase"] = "committed"
        write(journal, doc)
        recover(journal, destinations)
    except BaseException:
        if journal.exists():
            recover(journal, destinations)
        else:
            for row in rows:
                p = Path(row["incoming"])
                if p.exists():
                    shutil.rmtree(p)
        raise
