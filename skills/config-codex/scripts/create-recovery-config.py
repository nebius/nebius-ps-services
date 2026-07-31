#!/usr/bin/env python3
"""Create a missing public-safe Codex config without following or clobbering."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import tomllib


PLACEHOLDERS = ("{{CODEX_HOME}}", "{{PROJECT_ROOT}}")
PLACEHOLDER_PATTERN = re.compile(r"\{\{(CODEX_HOME|PROJECT_ROOT)\}\}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Render config-codex's public recovery template into a missing "
            "Codex home with an atomic no-clobber write and mode 0600."
        )
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")),
        help="Existing Codex home directory. Defaults to CODEX_HOME or ~/.codex.",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        required=True,
        help="Existing project root to add as the reviewed trusted-project entry.",
    )
    return parser.parse_args(argv)


def fail(message: str) -> int:
    print(f"ERROR {message}", file=sys.stderr)
    return 1


def require_existing_directory(path: Path, label: str) -> Path:
    expanded = path.expanduser().absolute()
    metadata = expanded.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"{label} must be an existing non-symlink directory")
    return expanded


def render_config(codex_home: Path, project_root: Path) -> bytes:
    template_path = (
        Path(__file__).resolve().parent.parent / "assets" / "config.toml.template"
    )
    replacements = {
        "CODEX_HOME": json.dumps(str(codex_home), ensure_ascii=False)[1:-1],
        "PROJECT_ROOT": json.dumps(str(project_root), ensure_ascii=False)[1:-1],
    }
    rendered = PLACEHOLDER_PATTERN.sub(
        lambda match: replacements[match.group(1)],
        template_path.read_text(encoding="utf-8"),
    )
    if any(placeholder in rendered for placeholder in PLACEHOLDERS):
        raise ValueError("recovery template contains an unresolved placeholder")
    tomllib.loads(rendered)
    return rendered.encode("utf-8")


def create_private_file(directory: Path, content: bytes) -> bool:
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    directory_descriptor = os.open(directory, directory_flags)
    temporary_name = f".config.toml.recovery-{os.getpid()}-{secrets.token_hex(8)}"
    file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        file_flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    published = False
    post_publication_warning = False
    try:
        descriptor = os.open(
            temporary_name,
            file_flags,
            0o600,
            dir_fd=directory_descriptor,
        )
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(
            temporary_name,
            "config.toml",
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        published = True
        try:
            os.fsync(directory_descriptor)
        except OSError:
            post_publication_warning = True
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                if published:
                    post_publication_warning = True
                else:
                    raise
        try:
            os.unlink(temporary_name, dir_fd=directory_descriptor)
        except FileNotFoundError:
            pass
        except OSError:
            if published:
                post_publication_warning = True
            else:
                raise
        try:
            os.close(directory_descriptor)
        except OSError:
            if published:
                post_publication_warning = True
            else:
                raise
    return post_publication_warning


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        codex_home = require_existing_directory(args.codex_home, "Codex home")
        project_root = require_existing_directory(args.project_root, "project root")
        content = render_config(codex_home, project_root)
        post_publication_warning = create_private_file(codex_home, content)
    except FileExistsError:
        return fail(
            "config.toml already exists; use the existing-file patch-only workflow"
        )
    except FileNotFoundError:
        return fail("the Codex home or reviewed project root does not exist")
    except PermissionError:
        return fail("permission was denied while creating config.toml")
    except (ValueError, tomllib.TOMLDecodeError):
        return fail("the recovery inputs or rendered template are invalid")
    except OSError:
        return fail("a filesystem operation failed while creating config.toml")
    if post_publication_warning:
        print(
            "WARNING config.toml was created, but one post-publication "
            "durability or cleanup step could not be confirmed. Do not retry "
            "creation; run the read-only idempotency preflight.",
            file=sys.stderr,
        )
    print("Created config.toml from the public-safe recovery baseline with mode 0600.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
