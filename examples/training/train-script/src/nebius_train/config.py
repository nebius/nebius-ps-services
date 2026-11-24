"""Validated configuration."""

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

PositiveInt = Annotated[int, Field(strict=True, gt=0)]
NonnegativeInt = Annotated[int, Field(strict=True, ge=0)]


class TrainConfig(BaseModel):
    """One explicit training plan; relative paths are relative to the YAML file."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)
    model_dir: Path
    data_dir: Path
    run_dir: Path
    task: Literal["classification", "seq2seq"] = "classification"
    device: Literal["cuda", "cpu"] = "cuda"
    batch_size: PositiveInt = 16
    learning_rate: Annotated[float, Field(gt=0)] = 2e-5
    num_epochs: PositiveInt = 3
    num_warmup_steps: NonnegativeInt = 0
    num_labels: Annotated[int, Field(strict=True, ge=2)] = 2
    grad_accum_steps: PositiveInt = 1
    max_length: PositiveInt = 512
    use_amp: StrictBool = True
    allow_no_validation: StrictBool = False
    num_workers: NonnegativeInt = 0
    pin_memory: StrictBool = True
    ddp_timeout_seconds: PositiveInt = 600
    tensorboard_logging: StrictBool = True
    save_final_model: StrictBool = True
    seed: Annotated[int, Field(strict=True, ge=0, lt=2**32)] = 42
    deterministic: StrictBool = False
    text_column: str = "text"
    label_column: str = "label"
    target_column: str = "target"
    validation_split: Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")] = "validation"

    @model_validator(mode="after")
    def validate_plan(self):
        if self.device == "cpu" and self.use_amp:
            raise ValueError("CPU validation requires use_amp: false")
        if self.validation_split == "train":
            raise ValueError("validation_split must differ from train")
        for field in ("text_column", "label_column", "target_column", "validation_split"):
            if not getattr(self, field).strip():
                raise ValueError(f"{field} must not be empty")
        return self


def load_config(path: Path) -> TrainConfig:
    """Parse bounded YAML without executable constructors or permissive booleans."""
    if path.stat().st_size > 64 * 1024:
        raise ValueError("Configuration exceeds 64 KiB")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        raise ValueError("Malformed YAML configuration") from None
    if not isinstance(data, dict):
        raise ValueError("Configuration must be a mapping")
    config = TrainConfig.model_validate(data)
    updates = {}
    for name in ("model_dir", "data_dir", "run_dir"):
        value = getattr(config, name).expanduser()
        updates[name] = (path.resolve().parent / value).resolve()
    config = config.model_copy(update=updates)
    for name in ("model_dir", "data_dir"):
        if not getattr(config, name).is_dir():
            raise ValueError(f"{name} must be an existing local directory")
    if any(
        config.run_dir == source
        or config.run_dir.is_relative_to(source)
        or source.is_relative_to(config.run_dir)
        for source in (config.model_dir, config.data_dir)
    ):
        raise ValueError("run_dir must be separate from model_dir and data_dir")
    return config
