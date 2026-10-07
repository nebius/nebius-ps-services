# Version qualification

## Completed prepared-environment runs

All 16 practical labs completed both `small` and `large` profiles (32 verified exports) on the prepared NVIDIA H200 cluster. See [lab results](reference/lab-results/) and the [catalog validation record](../docs/profiling-validation.md) for evidence scope and limitations.

The recorded framework is PyTorch `2.14.0+cu130` with CUDA runtime `13.0`. Lab 22 used Transformer Engine `2.19.0` in its separately prepared, compatible environment.

The observed profiler CLI/viewer pairs were Nsight Systems `2026.4.1` and Nsight Compute `2026.2.1`; the scheduler reported Slurm `25.11.3`. These runs qualify the recorded experiments in their prepared environments. They do not establish a clean installation, every optional extension, or performance on H100.

## H100 target and installation candidates

The following targets and pending checks describe the H100 teaching environment. Keep them separate from the completed H200 evidence above.

The introductory Lab 32 was executed locally on CPU with the existing PyTorch
2.13.0 environment. This is algorithmic smoke evidence only: it does not
qualify the clean target environment, authorize a dependency fallback or
establish CUDA/H100 behavior. No additional dependency is needed for the lab.

| Component | Course target | Evidence status |
| --- | --- | --- |
| Python | 3.12 | Local environment exists; cluster parity pending |
| PyTorch | 2.14.0 manifest authority | Clean Linux/H100 install and qualification pending; no fallback approved |
| Transformers, PEFT, TRL, datasets, accelerate | Exact candidate pins in `requirements.txt` | Clean target installation and joint compatibility pending |
| Transformer Engine | 2.19.0, isolated for Lab 22 using `te.autocast` | H100 ABI and runtime pending |
| GPU and Slurm | One full H100; distributed qualification belongs to Advanced Labs | Live validation pending |

Do not use deprecated `fp8_autocast` examples. Record the actual
Transformer Engine recipe, warm-up, PyTorch ABI, and kernels with FP8 results.

## DDP bucket and hook qualification

[Advanced Lab 21](../advanced-gpu-communication/reference/labs/21_ddp_buckets.md) uses the prepared PyTorch environment and the site-qualified
NCCL stack. Hook support, actual bucket behavior and numerical trajectories
require two-node H100 qualification. The Nsight launcher needs a compatible
profiler on both nodes. CPU reference and launcher tests do not establish these
live behaviors or training convergence.

## Shared profiling qualification

Use the [Lab Guide](../lab-guide.html#lab-preparation-scripts) for the preparation
and cluster-managed profiling route. Keep the matching CLI/viewer identities
recorded with each run. Installation does not qualify the driver, either worker,
the selected native runtime or optional container, or hardware-counter access;
those claims require the relevant canaries and report inspections. Keep the
existing AI runtime identities above; changing a profiler does not authorize
changing those environments or adding collectors and metrics databases.

Prepared H200 runtime and applicable live profiler captures are recorded above. This campaign did not rerun the complete infrastructure installer or qualify the H100 target.

## Advanced fabric qualification

The documented base target uses two one-H100 workers; all distributed GPU experiments now belong to the separate two-eight-H100 route. Eight-/sixteen-rank launchers require full devices with MIG disabled, NVLink peer paths and active InfiniBand. Physical topology checks do not replace Fabric Manager health, MPI/NCCL compatibility or GPUDirect registration qualification. No live 16-H100 qualification is claimed by these source changes.

The advanced course owns these fabric experiments and their selected native tool dependencies. Use the Lab Guide for preparation, retain the component build receipts, and qualify the same runtime on both nodes before accepting a comparison.
