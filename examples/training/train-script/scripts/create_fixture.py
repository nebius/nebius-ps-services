"""Create a small, original, offline classification fixture for training smoke tests."""

import argparse
from pathlib import Path

import torch
import yaml
from datasets import Dataset, DatasetDict
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace
from transformers import (
    DistilBertConfig,
    DistilBertForSequenceClassification,
    PreTrainedTokenizerFast,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(mode=0o700)
    torch.manual_seed(42)
    tokenizer = Tokenizer(
        WordLevel({"[PAD]": 0, "[UNK]": 1, "good": 2, "bad": 3, "film": 4}, unk_token="[UNK]")
    )
    tokenizer.pre_tokenizer = Whitespace()
    fast = PreTrainedTokenizerFast(
        tokenizer_object=tokenizer, pad_token="[PAD]", unk_token="[UNK]", model_max_length=32
    )
    fast.model_input_names = ["input_ids", "attention_mask"]
    model = DistilBertForSequenceClassification(
        DistilBertConfig(
            vocab_size=5,
            dim=16,
            hidden_dim=32,
            n_layers=1,
            n_heads=2,
            num_labels=2,
            max_position_embeddings=32,
        )
    )
    model.save_pretrained(root / "model")
    fast.save_pretrained(root / "model")
    DatasetDict(
        {
            "train": Dataset.from_dict(
                {"text": ["good film", "bad film"] * 4, "label": [1, 0] * 4}
            ),
            "validation": Dataset.from_dict(
                {"text": ["good film", "bad film", "good"], "label": [1, 0, 1]}
            ),
        }
    ).save_to_disk(str(root / "data"))
    (root / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "model_dir": "model",
                "data_dir": "data",
                "run_dir": "run",
                "device": args.device,
                "use_amp": args.device == "cuda",
                "batch_size": 2,
                "grad_accum_steps": 3,
                "num_epochs": 2,
                "tensorboard_logging": False,
                "max_length": 16,
            }
        )
    )
    print("Created original synthetic fixture and config.yaml")


if __name__ == "__main__":
    main()
