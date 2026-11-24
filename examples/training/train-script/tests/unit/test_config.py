from pathlib import Path

import pytest
from pydantic import ValidationError

from nebius_train.config import TrainConfig, load_config


def values(tmp_path):
    return dict(model_dir=tmp_path / "model", data_dir=tmp_path / "data", run_dir=tmp_path / "run")


@pytest.mark.parametrize(
    "update",
    [
        {"batch_size": 0},
        {"grad_accum_steps": 0},
        {"num_workers": -1},
        {"use_amp": "false"},
        {"learning_rate": float("nan")},
        {"unknown": 1},
        {"device": "cpu", "use_amp": True},
        {"batch_size": True},
        {"validation_split": "train"},
        {"text_column": ""},
        {"validation_split": "../../outside"},
    ],
)
def test_rejects_invalid_plan(tmp_path, update):
    with pytest.raises(ValidationError):
        TrainConfig(**(values(tmp_path) | update))


def test_paths_are_config_relative(tmp_path, monkeypatch):
    (tmp_path / "model").mkdir()
    (tmp_path / "data").mkdir()
    path = tmp_path / "config.yaml"
    path.write_text("model_dir: model\ndata_dir: data\nrun_dir: runs/one\n")
    monkeypatch.chdir(Path("/"))
    assert load_config(path).run_dir == tmp_path / "runs/one"


@pytest.mark.parametrize(
    "body", ["[]", '!!python/object/apply:os.system ["false"]', "batch_size: 1"]
)
def test_rejects_nonconfiguration_yaml(tmp_path, body):
    import yaml

    path = tmp_path / "config.yaml"
    path.write_text(body)
    with pytest.raises((ValueError, yaml.YAMLError)):
        load_config(path)


def test_output_cannot_overwrite_inputs(tmp_path):
    (tmp_path / "model").mkdir()
    (tmp_path / "data").mkdir()
    path = tmp_path / "config.yaml"
    path.write_text("model_dir: model\ndata_dir: data\nrun_dir: model/run\n")
    with pytest.raises(ValueError, match="separate"):
        load_config(path)
