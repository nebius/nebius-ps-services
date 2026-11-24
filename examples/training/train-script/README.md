# Train Script

Fine-tune Hugging Face **sequence classification and seq2seq models** with
PyTorch, locally or across Slurm GPU nodes.
It does not implement causal-language-model training or model sharding.

Python **3.14** is required. The current locked stack uses PyTorch 2.15,
Transformers 5.24 and Datasets 5.2. Models must fit on each worker's device.
Linux is the Slurm target; macOS supports explicit CPU validation.

## First local run

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and run
these commands from this directory. Setup never runs sudo or installs OS packages.

```bash
uv sync --locked --extra ml
uv run --locked --extra ml python scripts/create_fixture.py /tmp/train-example
uv run --locked --extra ml nebius-train-script validate --config /tmp/train-example/config.yaml
uv run --locked --extra ml nebius-train-script train --config /tmp/train-example/config.yaml
```

The example creates a randomly initialized tiny model and original synthetic
sentences; it downloads no model or dataset and demonstrates execution, not
useful model quality. Choose a new fixture directory when repeating it.
On Linux CPU-only hosts, replace `--extra ml` with `--extra cpu` in **every**
sync/run command to select the official CPU wheel without installing CUDA libraries.

Training dependencies are optional: `uv sync --locked` installs the CLI and
validation tools without the ML runtime. Training requires either `ml` or `cpu`;
these extras are mutually exclusive.

## Train your model

Use a trusted local model containing safetensors weights and tokenizer files,
and a Hugging Face `DatasetDict` saved with `save_to_disk()`. Downloading and
training are separate commands; training loads local files only and never enables
remote repository code.

For downloads, review the model/dataset licenses and access terms first. Provide
full immutable commit hashes, license identifiers, and a new destination:

```bash
uv run --locked --extra ml nebius-train-script download \
  --model OWNER/MODEL --model-revision FULL_40_CHARACTER_COMMIT \
  --model-license LICENSE_IDENTIFIER \
  --dataset OWNER/DATASET --dataset-revision FULL_40_CHARACTER_COMMIT \
  --dataset-license LICENSE_IDENTIFIER --output ./assets
```

Use `--dataset-config NAME` when needed. For private assets, supply `HF_TOKEN`
through your secret manager/environment. The tool does not log in or persist the
token. A manifest records revisions, declared licenses and content hashes only
after completion. Existing and interrupted destinations are rejected; a refresh
uses a new destination. License identifiers record your review, not a license check.

Edit [config.yaml](config.yaml). Paths resolve relative to the YAML file:

- `model_dir`, `data_dir`: existing local input directories.
- `run_dir`: a new output directory for each fresh run.
- `task`: `classification` or `seq2seq`; select columns explicitly.
- Classification labels must be integers in `[0, num_labels)`.
- Seq2seq uses `target_column`; `max_length` bounds input and target lengths.
- Pretokenized inputs may use `input_ids`, `attention_mask`, `token_type_ids`
  and `labels`; raw text is then unnecessary. Labels must match the selected task.
- `device: cuda` is the default; CPU runs require `use_amp: false`.
- `allow_no_validation: true` explicitly permits a missing validation split.
- `grad_accum_steps` controls optimizer updates, including incomplete groups.

Unknown fields and invalid booleans fail early. Old shell environment files,
legacy flags and old checkpoints are not supported by this refactor.

## Slurm

Put this project, its `.venv`, inputs and output directory on storage shared by
all allocated nodes. Install the locked environment on a compatible Linux host.
Ensure the installed NVIDIA driver supports the locked CUDA runtime; do not
reuse a macOS environment on Linux. On Soperator, submit from a normal SSH login
session inside the shared root filesystem so login and worker paths match.

```bash
uv sync --locked --extra ml
./submit.sh config.yaml --nodes=2 --gpus-per-node=1 --time=00:10:00
```

The defaults request one GPU, four CPU cores and 16 GiB RAM for 30 minutes.
Pass site-specific account, partition and resource options to `submit.sh`.
`SLURM_LOGS_DIR` optionally selects logs. Set `MASTER_PORT` to override the
rendezvous port when required by site policy; valid values are 1024–65535.
Each node runs one torchrun launcher, which starts one process per allocated GPU.
No automatic job restarts occur.
The script uses offline Hugging Face mode and stops peers after worker failure.

For a local multi-process CPU smoke test:

```bash
uv run --locked --extra ml torchrun --standalone --nproc-per-node=2 \
  --module nebius_train train --config /tmp/train-example/config.yaml
```

Use a fresh `run_dir`. DDP training may repeat a few examples to give every worker
equal batches. Validation never pads or duplicates examples, including when some
workers receive no validation examples. Classification loss is example-weighted;
seq2seq loss is weighted by non-padding target tokens.

## Results and recovery

Each run owns `run.json`, per-epoch JSON metrics, optional TensorBoard events,
`checkpoint.pt`, and `final-model/`. `complete.json` appears only after successful
completion/export. Export failure returns an error and retains the checkpoint.
Cancellation is unsuccessful; no signal handler turns it into a success.

Resume an interrupted run using the same configuration and worker count:

```bash
uv run --locked --extra ml nebius-train-script train --config config.yaml --resume
./submit.sh --resume config.yaml --nodes=2 --gpus-per-node=1
```

Resume restores the last complete epoch, optimizer, scheduler, AMP scaler and
per-rank random states. It checks configuration, dependency versions and input
hashes. Work after the last checkpoint is replayed. Completed runs are not
resumed. Checkpoints are always retained; remove your own outputs deliberately
when they are no longer needed. Inputs and run directories must be trusted,
immutable during a run, and protected from concurrent writers.

## Development and evidence

```bash
make check          # Lock freshness, lint, formatting, shell syntax, offline tests
make coverage       # Full non-external suite with branch coverage
make smoke-wheel    # Build sdist/wheel, install and check outside the checkout
make build
```

Use `make EXTRA=cpu check` on Linux CPU-only hosts. The installed-wheel smoke
checks the lightweight CLI outside the checkout; ML execution is tested separately.

Tests use original tiny local assets and block accidental Python socket access.
Subprocess/native networking requires OS/container isolation; pytest-socket is
not a security sandbox. See [validation](docs/validation.md) for measured outcomes
and explicit GPU/container coverage, and [security](docs/security.md) for trust
and licensing boundaries. There is no automatic cloud provisioning or live CI.

The [requirements](docs/requirements.md) and [design](docs/design.md) describe
the maintained contract. [References](docs/references.md) link primary sources.
Code is licensed under [Apache-2.0](LICENSE).
