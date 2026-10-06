#!/usr/bin/env python3
"""Verify synchronized source before narrowly retiring known old entrypoints."""

import argparse
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import sys

from course_bootstrap.retirement import retire
from course_bootstrap.state import setup_lock


def identity(path):
    if path.is_symlink():
        return {"link": os.readlink(path)}
    return {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "executable": bool(path.stat().st_mode & 0o111),
    }


def verify(root, manifest):
    root = root.absolute()
    if manifest.get("schema") != "course-source-sync/v1":
        raise ValueError("Unsupported source verification manifest")
    for relative, expected in manifest["files"].items():
        path = root / relative
        if (
            Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or any(p.is_symlink() for p in path.parents)
        ):
            raise ValueError("Unsafe source verification path")
        if identity(path) != expected:
            raise ValueError(f"Synchronized source differs: {relative}")
    return len(manifest["files"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("manifest", "verify"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--files", type=Path)
    args = parser.parse_args()
    if args.action == "manifest":
        if args.files is None:
            parser.error("manifest requires --files")
        files = sorted(set(args.files.read_text().split("\0")) - {""})
        # Match rsync --safe-links: outside links are deliberately not transferred.
        root = args.root.resolve()
        files = [
            p
            for p in files
            if not (args.root / p).is_symlink()
            or (args.root / p).resolve().is_relative_to(root)
        ]
        print(
            json.dumps(
                {
                    "schema": "course-source-sync/v1",
                    "files": {p: identity(args.root / p) for p in files},
                }
            )
        )
    else:
        manifest = json.load(sys.stdin)
        runtime = args.root / ".runtime"
        with setup_lock(runtime) if runtime.exists() else nullcontext():
            count = verify(args.root, manifest)
            scripts = (
                "regular",
                "cuda",
                "communication",
                "serving",
                "transformer-engine",
            )
            scopes = []
            for relative in manifest["files"]:
                if relative.endswith("tools/regular-lab-setup.py"):
                    course = Path(relative).parent.parent
                    required = [
                        course / "tools" / f"{group}-lab-setup.py" for group in scripts
                    ]
                    required += [
                        course / "tools/course_bootstrap/cli.py",
                        course / "tools/course_bootstrap/support.py",
                    ]
                    if all(
                        "sha256" in manifest["files"].get(path.as_posix(), {})
                        for path in required
                    ):
                        scopes.append(course)
            result = retire(args.root, scopes)
        print(
            f"Verified {count} source files; retired {len(result['retired'])} known old entrypoints; preserved {len(result['preserved_modified'])} modified entries."
        )


if __name__ == "__main__":
    main()
