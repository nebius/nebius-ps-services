"""Non-destructive acquisition."""

import os
import re
from pathlib import Path

from nebius_train.artifacts import atomic_json, tree_hash


def download_bundle(
    *,
    model: str,
    model_revision: str,
    model_license: str,
    dataset: str,
    dataset_revision: str,
    dataset_license: str,
    output: Path,
    dataset_config: str | None = None,
) -> None:
    for revision in (model_revision, dataset_revision):
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("Revisions must be full lowercase commit hashes")
    for license_name in (model_license, dataset_license):
        if not license_name.strip() or len(license_name) > 200:
            raise ValueError("An explicit reviewed license identifier is required")
    from datasets import load_dataset
    from huggingface_hub import snapshot_download
    from huggingface_hub.utils import validate_repo_id

    validate_repo_id(model)
    validate_repo_id(dataset)
    # Existing, partial, and symlink destinations all fail; no force/delete path.
    output = output.expanduser().absolute()
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    incomplete = output / ".incomplete"
    incomplete.touch(mode=0o600)
    token = os.environ.get("HF_TOKEN") or False
    snapshot_download(
        repo_id=model,
        revision=model_revision,
        local_dir=output / "model",
        token=token,
        allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model", "*.tiktoken"],
    )
    if not any((output / "model").glob("*.safetensors")):
        raise ValueError("Model must supply safetensors weights")
    data = load_dataset(dataset, name=dataset_config, revision=dataset_revision, token=token)
    data.save_to_disk(str(output / "data"))
    atomic_json(
        output / "manifest.json",
        {
            "schema": 1,
            "model": {
                "repo": model,
                "revision": model_revision,
                "license": model_license,
                "sha256": tree_hash(output / "model"),
            },
            "dataset": {
                "repo": dataset,
                "revision": dataset_revision,
                "config": dataset_config,
                "license": dataset_license,
                "sha256": tree_hash(output / "data"),
            },
        },
    )
    incomplete.unlink()
