"""Task-specific preprocessing."""

from datasets import DatasetDict, load_from_disk
from torch.utils.data import Sampler

from nebius_train.config import TrainConfig


class EvaluationSampler(Sampler[int]):
    """Disjoint evaluation indices without DistributedSampler padding."""

    def __init__(self, size: int, rank: int, world_size: int):
        self.indices = range(rank, size, world_size)

    def __iter__(self):
        return iter(self.indices)

    def __len__(self):
        return len(self.indices)


def prepare_data(config: TrainConfig, tokenizer) -> DatasetDict:
    """Prepare once on rank zero; return memory-mapped, run-local Arrow data."""
    cache = config.run_dir / "tokenized"
    cache.mkdir(parents=True, exist_ok=False)
    source = load_from_disk(str(config.data_dir))
    if not isinstance(source, DatasetDict) or "train" not in source:
        raise ValueError("Data must be a saved DatasetDict with a train split")
    names = ["train"]
    if config.validation_split in source:
        names.append(config.validation_split)
    elif not config.allow_no_validation:
        raise ValueError("The configured validation split is absent")
    result = DatasetDict()
    for name in names:
        split = source[name]
        if not len(split):
            raise ValueError("Selected data splits must not be empty")
        columns = split.column_names
        pretokenized = "input_ids" in columns
        if pretokenized:
            label = "labels" if "labels" in columns else config.label_column
            if label not in columns:
                raise ValueError("Pretokenized data requires labels")
            keep = {"input_ids", "attention_mask", "token_type_ids", label}
            split = split.remove_columns([c for c in columns if c not in keep])
            if label != "labels":
                split = split.rename_column(label, "labels")
        else:
            target = config.target_column if config.task == "seq2seq" else config.label_column
            if config.text_column not in columns or target not in columns:
                raise ValueError("Configured text or target column is absent")

            def tokenize(batch, target=target):
                kwargs = {"truncation": True, "max_length": config.max_length}
                if config.task == "seq2seq":
                    kwargs["text_target"] = batch[target]
                output = tokenizer(batch[config.text_column], **kwargs)
                if config.task == "classification":
                    output["labels"] = batch[target]
                return output

            split = split.map(
                tokenize,
                batched=True,
                remove_columns=columns,
                load_from_cache_file=False,
                keep_in_memory=False,
                cache_file_name=str(cache / f"{name}.arrow"),
            )
        if config.task == "classification":
            for label in split["labels"]:
                if (
                    isinstance(label, bool)
                    or not isinstance(label, int)
                    or not 0 <= label < config.num_labels
                ):
                    raise ValueError("Classification labels must be integer class IDs in range")
        elif any(
            not labels or all(label == -100 for label in labels) for labels in split["labels"]
        ):
            raise ValueError("Seq2seq examples require at least one target token")
        result[name] = split
    result.save_to_disk(str(cache / "dataset"))
    # Remove only exact intermediate files created by this invocation.
    for name in names:
        (cache / f"{name}.arrow").unlink(missing_ok=True)
    return load_from_disk(str(cache / "dataset"))
