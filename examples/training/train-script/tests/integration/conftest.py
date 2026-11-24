import pytest


@pytest.fixture
def tiny_assets(tmp_path):
    import torch
    from datasets import Dataset, DatasetDict
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace
    from transformers import (
        DistilBertConfig,
        DistilBertForSequenceClassification,
        PreTrainedTokenizerFast,
    )

    previous = torch.get_num_threads()
    torch.set_num_threads(1)
    torch.manual_seed(42)
    model_dir = tmp_path / "model"
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
    model.save_pretrained(model_dir)
    fast.save_pretrained(model_dir)
    data_dir = tmp_path / "data"
    DatasetDict(
        {
            "train": Dataset.from_dict(
                {
                    "text": ["good film", "bad film"] * 3,
                    "label": [1, 0] * 3,
                    "unused": ["private text"] * 6,
                }
            ),
            "validation": Dataset.from_dict(
                {
                    "text": ["good film", "bad film", "good"],
                    "label": [1, 0, 1],
                    "unused": ["private text"] * 3,
                }
            ),
        }
    ).save_to_disk(str(data_dir))
    from nebius_train.config import TrainConfig

    config = TrainConfig(
        model_dir=model_dir,
        data_dir=data_dir,
        run_dir=tmp_path / "run",
        device="cpu",
        use_amp=False,
        tensorboard_logging=False,
        batch_size=2,
        grad_accum_steps=2,
        num_epochs=2,
        max_length=16,
        deterministic=True,
    )
    yield config
    torch.set_num_threads(previous)
