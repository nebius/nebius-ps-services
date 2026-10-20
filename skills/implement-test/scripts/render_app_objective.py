#!/usr/bin/env python3
"""Render the direct disposable-app objective without leaking state."""

from __future__ import annotations

import argparse
import os
import re
import tempfile
import uuid
from pathlib import Path


COMPOSE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,62}$")


def render(template: str, generation_id: str, compose_project: str) -> str:
    uuid.UUID(generation_id)
    if not COMPOSE_RE.fullmatch(compose_project):
        raise ValueError("invalid Compose project name")
    values = {
        "{{GENERATION_ID}}": generation_id,
        "{{COMPOSE_PROJECT}}": compose_project,
    }
    rendered = template
    for token, value in values.items():
        if token not in rendered:
            raise ValueError(f"template is missing {token}")
        rendered = rendered.replace(token, value)
    if "{{" in rendered or "}}" in rendered:
        raise ValueError("template contains an unresolved placeholder")
    return rendered


def write_private(path: Path, content: str) -> None:
    if path.is_symlink():
        raise ValueError("output must not be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    os.chmod(temp_path, 0o600)
    os.replace(temp_path, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--compose-project", required=True)
    args = parser.parse_args()
    rendered_body = render(
        args.template.read_text(encoding="utf-8"),
        args.generation_id,
        args.compose_project,
    )
    write_private(args.output, rendered_body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
