import json

import pytest

pytestmark = pytest.mark.ml


def test_single_worker_trains_and_exports(tiny_assets):
    from nebius_train.ml.engine import train

    train(tiny_assets)
    run = tiny_assets.run_dir
    assert json.loads((run / "complete.json").read_text())["epochs"] == 2
    assert (run / "checkpoint.pt").is_file()
    assert (run / "final-model/model.safetensors").is_file()
    metrics = json.loads((run / "metrics/epoch-0002.json").read_text())
    assert metrics["validation"]["examples"] == 3
    assert metrics["train"]["units"] == 6


def test_resume_matches_uninterrupted_training(tiny_assets, monkeypatch):
    import torch

    from nebius_train.ml import engine

    uninterrupted = tiny_assets.model_copy(
        update={"run_dir": tiny_assets.run_dir.with_name("reference")}
    )
    engine.train(uninterrupted)
    original = engine.save_checkpoint

    def interrupt(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("simulated interruption after durable epoch")

    monkeypatch.setattr(engine, "save_checkpoint", interrupt)
    with pytest.raises(RuntimeError, match="simulated"):
        engine.train(tiny_assets)
    monkeypatch.setattr(engine, "save_checkpoint", original)
    engine.train(tiny_assets, resume=True)
    actual = torch.load(tiny_assets.run_dir / "checkpoint.pt", weights_only=True)
    expected = torch.load(uninterrupted.run_dir / "checkpoint.pt", weights_only=True)
    assert actual["next_epoch"] == expected["next_epoch"] == 2
    for key in actual["model"]:
        torch.testing.assert_close(actual["model"][key], expected["model"][key], rtol=0, atol=0)


def test_export_failure_retains_checkpoint(tiny_assets, monkeypatch):
    from transformers import DistilBertForSequenceClassification

    from nebius_train.ml.engine import train

    def fail(*args, **kwargs):
        raise OSError("export unavailable")

    monkeypatch.setattr(DistilBertForSequenceClassification, "save_pretrained", fail)
    with pytest.raises(OSError, match="export unavailable"):
        train(tiny_assets)
    assert (tiny_assets.run_dir / "checkpoint.pt").is_file()
    assert not (tiny_assets.run_dir / "complete.json").exists()


def test_no_overwrite_and_resume_plan_binding(tiny_assets, monkeypatch):
    from nebius_train.ml import engine

    original = engine.save_checkpoint

    def interrupt(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("interrupted")

    monkeypatch.setattr(engine, "save_checkpoint", interrupt)
    with pytest.raises(RuntimeError):
        engine.train(tiny_assets)
    with pytest.raises(FileExistsError):
        engine.train(tiny_assets)
    changed = tiny_assets.model_copy(update={"learning_rate": 0.01})
    with pytest.raises(ValueError, match="changed"):
        engine.train(changed, resume=True)


def test_incomplete_download_bundle_is_rejected(tiny_assets):
    from nebius_train.ml.engine import train

    (tiny_assets.model_dir.parent / ".incomplete").touch()
    with pytest.raises(ValueError, match="incomplete"):
        train(tiny_assets)


def test_download_manifest_detects_modified_assets(tiny_assets):
    from nebius_train.artifacts import atomic_json, tree_hash
    from nebius_train.ml.engine import train

    atomic_json(
        tiny_assets.model_dir.parent / "manifest.json",
        {
            "schema": 1,
            "model": {"sha256": tree_hash(tiny_assets.model_dir)},
            "dataset": {"sha256": tree_hash(tiny_assets.data_dir)},
        },
    )
    (tiny_assets.model_dir / "unexpected.txt").write_text("changed asset")
    with pytest.raises(ValueError, match="manifest"):
        train(tiny_assets)
