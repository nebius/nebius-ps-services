import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.ml


def test_download_records_complete_pinned_assets(tmp_path, monkeypatch):
    import datasets
    import huggingface_hub
    from datasets import Dataset, DatasetDict

    from nebius_train.download import download_bundle

    seen = []

    def snapshot(**kwargs):
        seen.append(kwargs)
        path = Path(kwargs["local_dir"])
        path.mkdir()
        (path / "model.safetensors").write_bytes(b"fixture")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot)
    monkeypatch.setattr(
        datasets,
        "load_dataset",
        lambda *args, **kwargs: DatasetDict(train=Dataset.from_dict({"text": ["fixture"]})),
    )
    args = dict(
        model="example/model",
        model_revision="a" * 40,
        model_license="Apache-2.0",
        dataset="example/data",
        dataset_revision="b" * 40,
        dataset_license="CC0-1.0",
        output=tmp_path / "bundle",
    )
    download_bundle(**args)
    manifest = json.loads((args["output"] / "manifest.json").read_text())
    assert manifest["model"]["revision"] == "a" * 40
    assert not (args["output"] / ".incomplete").exists()
    assert seen[0]["token"] is False
    with pytest.raises(FileExistsError):
        download_bundle(**args)
    assert len(seen) == 1


def test_failed_download_is_not_reused(tmp_path, monkeypatch):
    import huggingface_hub

    from nebius_train.download import download_bundle

    def fail(**kwargs):
        raise OSError("network unavailable")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fail)
    args = dict(
        model="example/model",
        model_revision="a" * 40,
        model_license="Apache-2.0",
        dataset="example/data",
        dataset_revision="b" * 40,
        dataset_license="CC0-1.0",
        output=tmp_path / "partial",
    )
    with pytest.raises(OSError):
        download_bundle(**args)
    assert (args["output"] / ".incomplete").exists()
    assert not (args["output"] / "manifest.json").exists()


def test_mutable_revision_rejected_before_writes(tmp_path):
    from nebius_train.download import download_bundle

    with pytest.raises(ValueError, match="commit"):
        download_bundle(
            model="example/model",
            model_revision="main",
            model_license="Apache-2.0",
            dataset="example/data",
            dataset_revision="b" * 40,
            dataset_license="CC0-1.0",
            output=tmp_path / "bundle",
        )
    assert not (tmp_path / "bundle").exists()
