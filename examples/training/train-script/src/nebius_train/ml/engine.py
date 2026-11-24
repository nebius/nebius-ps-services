"""Training orchestration."""

import fcntl
import json
import math
import os
from contextlib import ExitStack, nullcontext
from importlib.metadata import version

import torch
import torch.distributed as dist
from datasets import disable_progress_bars, load_from_disk
from torch.nn.parallel import DistributedDataParallel
from torch.utils.data import DataLoader, DistributedSampler
from transformers import (
    AutoModelForSeq2SeqLM,
    AutoModelForSequenceClassification,
    AutoTokenizer,
    DataCollatorForSeq2Seq,
    DataCollatorWithPadding,
    get_linear_schedule_with_warmup,
)

from nebius_train.artifacts import atomic_json, input_hash, tree_hash
from nebius_train.config import TrainConfig
from nebius_train.ml.checkpoint import load_checkpoint, save_checkpoint, seed_all
from nebius_train.ml.data import EvaluationSampler, prepare_data
from nebius_train.ml.distributed import Runtime, distributed


def loss_units(labels: torch.Tensor, task: str) -> int:
    return int((labels != -100).sum()) if task == "seq2seq" else labels.numel()


def optimizer_step(model, optimizer, scheduler, scaler, units: int, runtime: Runtime) -> None:
    """Normalize summed microbatch losses, accounting for DDP's gradient average."""
    global_units = runtime.sum([units])[0]
    if global_units <= 0:
        raise ValueError("An accumulation group contains no target units")
    scaler.unscale_(optimizer)
    factor = runtime.world_size / global_units
    for parameter in model.parameters():
        if parameter.grad is not None:
            parameter.grad.mul_(factor)
    before = scaler.get_scale()
    scaler.step(optimizer)
    scaler.update()
    if scaler.get_scale() >= before:
        scheduler.step()
    optimizer.zero_grad(set_to_none=True)


def epoch(
    model,
    loader,
    config: TrainConfig,
    runtime: Runtime,
    optimizer=None,
    scheduler=None,
    scaler=None,
) -> dict:
    training = optimizer is not None
    model.train(training)
    totals = [0.0, 0.0, 0.0, 0.0]  # loss sum, units, correct, examples
    pending_units = 0
    if training:
        optimizer.zero_grad(set_to_none=True)
    for index, batch in enumerate(loader):
        batch = {key: value.to(runtime.device) for key, value in batch.items()}
        labels = batch["labels"]
        units = loss_units(labels, config.task)
        if units == 0:
            raise ValueError("Batch has no target units")
        boundary = (index + 1) % config.grad_accum_steps == 0 or index + 1 == len(loader)
        sync = (
            model.no_sync()
            if training and not boundary and isinstance(model, DistributedDataParallel)
            else nullcontext()
        )
        with sync, torch.set_grad_enabled(training):
            with torch.autocast(runtime.device.type, enabled=config.use_amp):
                outputs = model(**batch)
                loss = outputs.loss
            if not torch.isfinite(loss):
                raise FloatingPointError("Non-finite loss")
            if training:
                scaler.scale(loss * units).backward()
        totals[0] += loss.item() * units
        totals[1] += units
        totals[3] += labels.shape[0]
        if config.task == "classification":
            totals[2] += (outputs.logits.argmax(dim=-1) == labels).sum().item()
        if training:
            pending_units += units
            if boundary:
                optimizer_step(model, optimizer, scheduler, scaler, pending_units, runtime)
                pending_units = 0
    loss_sum, units, correct, examples = runtime.sum(totals)
    if not units:
        raise ValueError("Epoch has no target units")
    return {
        "loss": loss_sum / units,
        "units": int(units),
        "examples": int(examples),
        "accuracy": correct / examples if config.task == "classification" else None,
    }


def _open_run(config: TrainConfig, resume: bool, runtime: Runtime, stack: ExitStack) -> dict:
    def prepare():
        run = config.run_dir
        if resume:
            if not run.is_dir() or run.is_symlink():
                raise ValueError("Resume requires an existing run directory")
        else:
            run.parent.mkdir(parents=True, exist_ok=True)
            run.mkdir(mode=0o700)
        fd = os.open(run / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        handle = stack.enter_context(os.fdopen(fd, "w"))
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        identity = {
            "schema": 1,
            "config": config.model_dump(mode="json"),
            "world_size": runtime.world_size,
            "model_sha256": input_hash(config.model_dir, "model"),
            "data_sha256": input_hash(config.data_dir, "dataset"),
            "versions": {name: version(name) for name in ("torch", "transformers", "datasets")},
        }
        if resume:
            if json.loads((run / "run.json").read_text()) != identity:
                raise ValueError("Run configuration, dependencies or artifacts changed")
            if (run / "complete.json").exists():
                raise ValueError("This run already completed successfully")
            if not (run / "checkpoint.pt").is_file():
                raise ValueError("No completed epoch is available to resume")
        else:
            atomic_json(run / "run.json", identity)
            (run / "metrics").mkdir(mode=0o700)

    runtime.primary(prepare)
    return json.loads((config.run_dir / "run.json").read_text())


def train(config: TrainConfig, *, resume: bool = False) -> None:
    """Execute one immutable training plan; retain checkpoints on every outcome."""
    disable_progress_bars()
    with distributed(config) as runtime, ExitStack() as stack:
        identity = _open_run(config, resume, runtime, stack)
        seed_all(config.seed, config.deterministic)
        common = {"local_files_only": True, "trust_remote_code": False}
        tokenizer = AutoTokenizer.from_pretrained(config.model_dir, **common)
        model_class = (
            AutoModelForSeq2SeqLM
            if config.task == "seq2seq"
            else AutoModelForSequenceClassification
        )
        kwargs = {"num_labels": config.num_labels} if config.task == "classification" else {}
        base = model_class.from_pretrained(
            config.model_dir, use_safetensors=True, **common, **kwargs
        )
        base.to(runtime.device)

        def prepare_once():
            derived = config.run_dir / "tokenized" / "dataset"
            record = config.run_dir / "tokenized.json"
            if resume:
                if json.loads(record.read_text())["sha256"] != tree_hash(derived):
                    raise ValueError("Prepared data changed since the checkpoint")
            else:
                prepare_data(config, tokenizer)
                atomic_json(record, {"sha256": tree_hash(derived)})

        runtime.primary(prepare_once)
        data = load_from_disk(str(config.run_dir / "tokenized" / "dataset"))
        collator = (
            DataCollatorForSeq2Seq(tokenizer, model=base)
            if config.task == "seq2seq"
            else DataCollatorWithPadding(tokenizer)
        )
        sampler = DistributedSampler(
            data["train"],
            num_replicas=runtime.world_size,
            rank=runtime.rank,
            seed=config.seed,
            shuffle=True,
        )
        generator = torch.Generator()
        loader_options = {
            "batch_size": config.batch_size,
            "num_workers": config.num_workers,
            "pin_memory": config.pin_memory and runtime.device.type == "cuda",
            "collate_fn": collator,
            "generator": generator,
        }
        training = DataLoader(data["train"], sampler=sampler, **loader_options)
        validation = None
        if config.validation_split in data:
            split = data[config.validation_split]
            validation = DataLoader(
                split,
                sampler=EvaluationSampler(len(split), runtime.rank, runtime.world_size),
                **loader_options,
            )
        model = base
        if runtime.world_size > 1:
            model = DistributedDataParallel(
                base, device_ids=[runtime.local_rank] if runtime.device.type == "cuda" else None
            )
        optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate)
        scheduler = get_linear_schedule_with_warmup(
            optimizer,
            config.num_warmup_steps,
            math.ceil(len(training) / config.grad_accum_steps) * config.num_epochs,
        )
        scaler = torch.amp.GradScaler(runtime.device.type, enabled=config.use_amp)
        seed_all(config.seed + runtime.rank, config.deterministic)
        start = (
            load_checkpoint(
                config.run_dir / "checkpoint.pt",
                model=base,
                optimizer=optimizer,
                scheduler=scheduler,
                scaler=scaler,
                identity=identity,
                runtime=runtime,
            )
            if resume
            else 0
        )
        if not 0 <= start <= config.num_epochs:
            raise ValueError("Checkpoint epoch is outside the training plan")
        writer = None
        if runtime.rank == 0 and config.tensorboard_logging:
            from torch.utils.tensorboard import SummaryWriter

            writer = stack.enter_context(
                SummaryWriter(str(config.run_dir / "tensorboard"), purge_step=start)
            )
        for number in range(start, config.num_epochs):
            sampler.set_epoch(number)
            generator.manual_seed(config.seed + runtime.rank + number * runtime.world_size)
            metrics = {
                "epoch": number + 1,
                "train": epoch(model, training, config, runtime, optimizer, scheduler, scaler),
            }
            if validation is not None:
                # DDP forward may perform collectives: uneven eval uses the base model.
                if runtime.world_size > 1:
                    for buffer in base.buffers():
                        dist.broadcast(buffer, src=0)
                metrics["validation"] = epoch(base, validation, config, runtime)

            def report(number=number, metrics=metrics):
                atomic_json(config.run_dir / "metrics" / f"epoch-{number + 1:04}.json", metrics)
                print(json.dumps(metrics, allow_nan=False), flush=True)
                if writer:
                    for split in ("train", "validation"):
                        if split in metrics:
                            writer.add_scalar(f"loss/{split}", metrics[split]["loss"], number)
                            if metrics[split]["accuracy"] is not None:
                                writer.add_scalar(
                                    f"accuracy/{split}", metrics[split]["accuracy"], number
                                )
                    writer.flush()

            runtime.primary(report)
            save_checkpoint(
                config.run_dir / "checkpoint.pt",
                model=base,
                optimizer=optimizer,
                scheduler=scheduler,
                scaler=scaler,
                next_epoch=number + 1,
                identity=identity,
                runtime=runtime,
            )

        def finish():
            if config.save_final_model:
                final = config.run_dir / "final-model"
                base.save_pretrained(final)
                tokenizer.save_pretrained(final)
                export_hash = tree_hash(final)
            else:
                export_hash = None
            atomic_json(
                config.run_dir / "complete.json",
                {"epochs": config.num_epochs, "final_model_sha256": export_hash},
            )

        runtime.primary(finish)
