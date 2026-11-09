#!/usr/bin/env python3
"""Hash an explicit reviewed input list, including ancestor directory modes."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

from tsc_support.contracts import (
    PREFIX,
    list_value,
    read_json,
    require,
    seal,
    write_new,
)


def snapshot(root, paths):
    root = Path(root).resolve(strict=True)
    require(root.is_dir(), "source root must be a directory")
    list_value(paths, "reviewed input paths")
    require(bool(paths), "explicit reviewed inputs required")
    pending = list(paths)
    entries = {}
    while pending:
        relative = pending.pop()
        require(
            isinstance(relative, str)
            and relative != ""
            and not Path(relative).is_absolute()
            and ".." not in Path(relative).parts,
            "input path must be contained",
        )
        path = root / relative
        # Do not traverse symlink parents; list the link and its resolved target explicitly.
        for parent in path.parents:
            if parent == root:
                break
            require(not parent.is_symlink(), "symlink parent in reviewed input")
        relative = path.relative_to(root).as_posix()
        if relative in entries:
            continue
        before = path.lstat()
        entry = {"path": relative, "mode": stat.S_IMODE(before.st_mode)}
        if stat.S_ISREG(before.st_mode):
            h = hashlib.sha256()
            with path.open("rb") as stream:
                for data in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(data)
            entry.update(kind="file", sha256=h.hexdigest())
        elif stat.S_ISDIR(before.st_mode):
            entry.update(
                kind="directory"
            )  # Never recursively include unreviewed files.
        elif stat.S_ISLNK(before.st_mode):
            target = path.readlink()
            require(not target.is_absolute(), "absolute source symlink is not portable")
            parent = path.parent
            for part in target.parts[:-1]:
                parent = Path(os.path.normpath(parent / part))
                require(parent.is_relative_to(root), "link traversal escapes root")
                require(not parent.is_symlink(), "symlink parent in link target")
                pending.append(parent.relative_to(root).as_posix())
            resolved = path.resolve(strict=True)
            require(resolved.is_relative_to(root), "source symlink escapes root")
            entry.update(kind="symlink", target=str(target))
            immediate = Path(os.path.normpath(path.parent / target))
            require(immediate.is_relative_to(root), "source symlink escapes root")
            pending.append(immediate.relative_to(root).as_posix())
        else:
            raise ValueError("unsupported source file type")
        after = path.lstat()
        require(
            (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            == (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ),
            "source changed while hashing",
        )
        entries[relative] = entry
        if path != root:
            pending.append(path.parent.relative_to(root).as_posix())
    return seal(
        {
            "schema": f"{PREFIX}.source-manifest.v2",
            "entries": [entries[k] for k in sorted(entries)],
        }
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    parser.add_argument(
        "paths",
        help="JSON array of reviewed relative paths; directories are not recursive",
    )
    parser.add_argument("--output", help="New private manifest; default stdout")
    args = parser.parse_args()
    try:
        text = json.dumps(snapshot(args.root, read_json(args.paths)), indent=2) + "\n"
        write_new(args.output, text) if args.output else print(text, end="")
    except (ValueError, OSError, RuntimeError) as exc:
        print(
            str(exc)
            if isinstance(exc, ValueError)
            else "Source unavailable or changed",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
