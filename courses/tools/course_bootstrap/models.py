"""Validate immutable snapshots in the installation's shared Hub cache."""

from pathlib import Path
import json

from .state import file_digest, read_json, regular


def reusable(state, name, models):
    """A changed installer fingerprint does not invalidate intact model bytes."""
    try:
        previous = read_json(state.records / f"{name}.json")
        record = state.completed(name, previous["fingerprint"])
        if not record or not {"models.json", "inventory.json"} <= set(
            record["artifacts"]
        ):
            return False
        snapshots = read_json(Path(record["prefix"]) / "models.json")
        expected = {model["repo"]: model["revision"] for model in models}
        if {repo: value["revision"] for repo, value in snapshots.items()} != expected:
            return False
        return validate(state.root.parent, record, hashes=True)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def validate(root, record, *, hashes):
    hub = root / ".runtime/cache/huggingface/hub"
    for parent in (hub, *hub.parents):
        if parent.is_symlink():
            raise ValueError("Model cache must not use symlinked directories")
    manifest = Path(record["prefix"]) / "inventory.json"
    regular(manifest, private=True)
    inventory = json.loads(manifest.read_text())
    if not inventory:
        raise ValueError("Model snapshot inventory is empty")
    for relative, expected in inventory.items():
        path = hub / relative
        if (
            Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or not path.resolve().is_relative_to(hub)
        ):
            raise ValueError("Model artifact escapes the shared cache")
        if not path.is_file() or (hashes and file_digest(path) != expected):
            return False
    return True
