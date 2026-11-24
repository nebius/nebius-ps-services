import pytest

pytestmark = pytest.mark.ml


def test_accumulation_tail_matches_large_batch(tiny_assets):
    from types import SimpleNamespace

    import torch

    from nebius_train.ml.distributed import Runtime
    from nebius_train.ml.engine import epoch

    class Model(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.weight = torch.nn.Parameter(torch.tensor(0.5))

        def forward(self, input_ids, labels):
            prediction = self.weight * input_ids
            return SimpleNamespace(
                loss=((prediction - labels) ** 2).mean(),
                logits=torch.stack([prediction, -prediction], -1),
            )

    config = tiny_assets.model_copy(update={"grad_accum_steps": 3})
    model = Model()
    optimizer = torch.optim.SGD(model.parameters(), lr=0.1)
    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lambda _: 1)
    scaler = torch.amp.GradScaler("cpu", enabled=False)
    batches = [
        dict(input_ids=torch.tensor([1.0, 2.0]), labels=torch.tensor([0.0, 1.0])),
        dict(input_ids=torch.tensor([3.0]), labels=torch.tensor([1.0])),
    ]
    reference = Model()
    large = torch.optim.SGD(reference.parameters(), lr=0.1)
    reference(torch.tensor([1.0, 2.0, 3.0]), torch.tensor([0.0, 1.0, 1.0])).loss.backward()
    large.step()
    epoch(
        model, batches, config, Runtime(0, 0, 1, torch.device("cpu")), optimizer, scheduler, scaler
    )
    torch.testing.assert_close(model.weight, reference.weight)
    assert scheduler.last_epoch == 1


def test_evaluation_shards_do_not_pad():
    from nebius_train.ml.data import EvaluationSampler

    shards = [list(EvaluationSampler(3, rank, 5)) for rank in range(5)]
    assert sorted(index for shard in shards for index in shard) == [0, 1, 2]


def test_custom_labels_and_unused_text(tiny_assets):
    from datasets import load_from_disk
    from transformers import AutoTokenizer

    from nebius_train.ml.data import prepare_data

    data = load_from_disk(str(tiny_assets.data_dir)).rename_column("label", "category")
    path = tiny_assets.data_dir.with_name("custom")
    data.save_to_disk(str(path))
    config = tiny_assets.model_copy(update={"data_dir": path, "label_column": "category"})
    result = prepare_data(
        config, AutoTokenizer.from_pretrained(config.model_dir, local_files_only=True)
    )
    assert result["train"]["labels"] == [1, 0, 1, 0, 1, 0]
    assert "unused" not in result["train"].column_names


def test_seq2seq_training(tiny_assets):
    from datasets import Dataset, DatasetDict
    from transformers import AutoTokenizer, T5Config, T5ForConditionalGeneration

    from nebius_train.ml.engine import train

    model_dir = tiny_assets.model_dir.with_name("seq-model")
    model = T5ForConditionalGeneration(
        T5Config(
            vocab_size=5,
            d_model=16,
            d_ff=32,
            num_layers=1,
            num_decoder_layers=1,
            num_heads=2,
            decoder_start_token_id=0,
            pad_token_id=0,
            eos_token_id=3,
        )
    )
    model.save_pretrained(model_dir)
    tokenizer = AutoTokenizer.from_pretrained(tiny_assets.model_dir, local_files_only=True)
    tokenizer.save_pretrained(model_dir)
    data_dir = tiny_assets.data_dir.with_name("seq-data")
    split = Dataset.from_dict(
        {"text": ["good film", "bad film", "film"], "target": ["good", "bad film", "good film"]}
    )
    DatasetDict(train=split, validation=split).save_to_disk(str(data_dir))
    config = tiny_assets.model_copy(
        update={"model_dir": model_dir, "data_dir": data_dir, "task": "seq2seq", "num_epochs": 1}
    )
    train(config)
    import json

    metrics = json.loads((config.run_dir / "metrics/epoch-0001.json").read_text())
    assert metrics["validation"]["units"] == 5
    assert metrics["validation"]["accuracy"] is None


def test_preprocessing_returns_disk_backed_splits(tiny_assets):
    from transformers import AutoTokenizer

    from nebius_train.ml.data import prepare_data

    tokenizer = AutoTokenizer.from_pretrained(tiny_assets.model_dir, local_files_only=True)
    result = prepare_data(tiny_assets, tokenizer)
    assert result["train"].cache_files, "Tokenized corpus must remain disk-backed"


def test_pretokenized_data_needs_no_text_column(tiny_assets):
    from datasets import Dataset, DatasetDict
    from transformers import AutoTokenizer

    from nebius_train.ml.data import prepare_data

    data = tiny_assets.data_dir.with_name("pretokenized")
    split = Dataset.from_dict(
        {"input_ids": [[2, 4], [3, 4]], "attention_mask": [[1, 1], [1, 1]], "labels": [1, 0]}
    )
    DatasetDict(train=split, validation=split).save_to_disk(str(data))
    config = tiny_assets.model_copy(update={"data_dir": data})
    result = prepare_data(
        config, AutoTokenizer.from_pretrained(config.model_dir, local_files_only=True)
    )
    assert result["train"]["labels"] == [1, 0]
