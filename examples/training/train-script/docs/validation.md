# Validation

The application was validated on 2026-11-23. The tests below use original,
randomly initialized tiny models and synthetic data. They verify behavior, not
model quality, training throughput or production capacity.

## Local CPU and packaging

- Python 3.14.10 on macOS arm64: **39 passed**, no skips or expected failures.
- Classification and seq2seq training, weighted accumulation tails, exact
  uninterrupted/resumed CPU weight equality, retained checkpoints after export
  failure, unpadded validation shards, disk-backed preprocessing, strict config,
  malformed-input redaction and asset-manifest integrity are exercised.
- Full non-external coverage run: **79% combined statement/branch coverage**.
  Subprocess CLI and live distributed execution are outside that local coverage
  measurement; the percentage is not a completeness claim.
- A fresh sdist and wheel built successfully. The wheel was installed into a
  separate environment with only base dependencies; import origin, version and
  CLI help/version passed outside the checkout. The project LICENSE is included
  in package metadata.
- Ruff, format checks, Bash syntax, ShellCheck, lock freshness and Markdown
checks
  form the final changed-scope alignment gate.

## Isolated Linux container

Python 3.14.10, native Linux arm64, official CPU PyTorch 2.15 wheel. The test
container ran as UID 65534, with a read-only root/source, no network, two CPUs,
3 GiB memory and a 900-second deadline. It had no host writable mount.

Independent collection froze **39 test IDs**. Execution passed all 39, with no
missing, deselected, skipped, retried, xfailed or exempted tests. Source hashes,
actual non-root readability, read-only access and import origin were verified.
The plan-bound receipt checker passed. The suite process took **8.31 seconds**;
that includes container startup and reporter overhead, so it is not compared to
local pytest wall time. The tested image used a pinned Python base digest:

```text
sha256:3fe1a0b6c2d6b2ebb572906cdabeca75aeb558c0c17db5ffefbcf8094871c32b
```

This qualifies current source tests, not deployment of a production image.
Task-owned containers, image tag and evidence volume were removed after receipt
preservation. No shared image cache or unrelated container was pruned.

## Live Slurm

A clean product submission completed on **two H100 80 GiB nodes**, one GPU per
node, using Slurm 25.11.3, driver 580.173.02, Python 3.14.10, PyTorch 2.15.0,
Transformers 5.24.0 and Datasets 5.2.0. The job used the unchanged product
`submit.sh` → `train.slurm` → torchrun → CLI path, CUDA AMP and two epochs.
Slurm reported **COMPLETED, exit 0:0, 30 seconds**.

Independent post-run verification confirmed:

- Eight training examples and exactly three validation examples in each epoch.
- Finite losses, two per-rank RNG states and retained optimizer/scaler state.
- Twenty-four parameter tensors changed from their initial random values.
- Exported safetensors exactly matched the final checkpoint.
- The completion record matched an independently computed export tree hash.

The frozen execution archive had SHA-256:

```text
81f5b970aeb911fcd711caff5593179d852222a39a374010af518addbe8cbfbb
```

An earlier trial failed before training because the administrative test harness
submitted outside Soperator's shared root filesystem. It passed physical mount
paths that differed inside jobs. The harness was corrected to use the shared
root, the editable environment was reinstalled there, and a new fixture and
trial were used. The failed trial was retained separately; no training source
change or intervention during the successful job was needed. This was a harness
repair, not a demonstrated training-code defect. The README now calls out normal
SSH login context for Soperator submissions.

The borrowed cluster was not reprovisioned or reconfigured. Task-owned runtime
files were removed after verification; no user jobs or shared resources are
deleted.

## Pytest performance review

Implementation was authorized; measurement used the existing locked interpreter,
a fixed 39-test selection, a 60-second deadline per run, fresh task-owned
temporary paths, disabled pytest cache and warm OS/import caches. No dependency
installation or package-manager target was included in the timing command:

```bash
.venv/bin/python -m pytest -p no:cacheprovider \
  -m 'not external' tests -q
```

| Measurement | Three wall-time samples (seconds) | Median | Range |
| --- | --- | --- | --- |
| Collect-only | 0.538, 0.261, 0.315 | 0.315 | 0.261–0.538 |
| Full suite | 5.361, 3.939, 3.602 | 3.939 | 3.602–5.361 |

All repetitions selected the same node IDs and passed all 39 tests. Startup and
collection were measured together; standalone startup was not isolated. A
separate `--durations=0` diagnostic found 1.58 seconds in the first ML fixture
setup, 0.44 seconds in the download integration test and about 0.25 seconds
across two CLI subprocess tests. These are diagnostic samples, not the wall-time
baseline. The instrumented container receipt recorded setup/call/teardown totals
of 4.442/2.274/0.006 seconds; its platform and instrumentation differ from local
runs.

Lazy ML imports keep CLI/collection light. Tiny per-test assets preserve
isolation. The measured suite is already short, so no broader fixtures, test
removal, parallel workers or disabled safety plugins were justified. These
measurements establish a current baseline; there is **no before/after speedup
claim**. Reassess only when representative suite growth or CI measurements
identify a material bottleneck.

## Security and review boundaries

The final read-only review found no further concrete blockers after regression
fixes for duplicated preprocessing, YAML input leakage and incomplete/unverified
asset bundles. A separate validation-split path traversal regression was also
fixed. See [security and licensing](security.md) for dependency scan scope,
proprietary GPU terms and model/data licensing obligations.

External Hugging Face download calls were tested with controlled responses, not
with a private account or third-party model/data. GPU seq2seq, GPU
interruption/resume, large models, sharding, causal-LM training, long-running
fault tolerance and production performance were not qualified. Epoch recovery
equivalence was tested on CPU. No repository commit, push or publication is part
of these checks.
