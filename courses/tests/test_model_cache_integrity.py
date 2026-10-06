"""A cached download must not turn corrupt bytes into a valid receipt."""

from pathlib import Path
import sys

import pytest

from course_bootstrap.installer import Installer
from course_bootstrap.models import validate
from course_bootstrap.state import State, atomic_json, private_dir


@pytest.mark.parametrize(
    "damage", [None, "weights", "inventory", "missing-inventory", "missing-receipt"]
)
def test_reinstall_refreshes_only_untrusted_snapshot(tmp_path, damage):
    root = tmp_path.resolve()
    state = State(root)
    spec = {
        "kind": "models",
        "python": sys.executable,
        "models": [{"repo": "example/model", "revision": "a" * 40}],
        "artifacts": ["models.json", "inventory.json"],
    }
    definitions = {"components": {"models-test": spec}}
    installer = Installer(root, {}, definitions, state)
    refreshes = []

    def download(argv, **_kwargs):
        # Execute the actual embedded downloader against an isolated fake Hub.
        import types
        from unittest.mock import patch

        def snapshot_download(*, repo_id, revision, cache_dir, force_download):
            refreshes.append(force_download)
            folder = Path(cache_dir)
            for part in ("models--example--model", "snapshots", revision):
                folder = private_dir(folder / part)
            for name, content in (
                ("config.json", "{}"),
                ("model.safetensors", "valid weights"),
            ):
                path = folder / name
                if force_download or not path.exists():
                    path.write_text(content)

        module = types.ModuleType("huggingface_hub")
        module.snapshot_download = snapshot_download
        with (
            patch.dict(sys.modules, {"huggingface_hub": module}),
            patch.object(sys, "argv", ["-c", *argv[3:]]),
        ):
            exec(argv[2], {})

    installer.run = download
    installer.install("models-test")
    original = installer.records["models-test"]
    snapshot = (
        state.cache / "huggingface/hub/models--example--model/snapshots" / ("a" * 40)
    )
    if damage == "weights":
        (snapshot / "model.safetensors").write_text("damaged")
    elif damage == "inventory":
        atomic_json(Path(original["prefix"]) / "inventory.json", {"tampered": "digest"})
    elif damage == "missing-inventory":
        (Path(original["prefix"]) / "inventory.json").unlink()
    elif damage == "missing-receipt":
        (state.records / "models-test.json").unlink()
        (snapshot / "model.safetensors").write_text("untrusted cached bytes")
    # Force a new installation generation without invalidating the snapshot's identity.
    spec["description"] = "Changed installation source"
    installer.install("models-test")
    assert refreshes == [True, damage is not None]
    assert (snapshot / "model.safetensors").read_text() == "valid weights"
    assert validate(root, installer.records["models-test"], hashes=True)
    installer.install("models-test")
    assert len(refreshes) == 2


def test_unreceipted_preexisting_snapshot_is_refreshed(tmp_path):
    from course_bootstrap.models import reusable

    state = State(tmp_path.resolve())
    assert not reusable(
        state, "unknown", [{"repo": "example/model", "revision": "a" * 40}]
    )
