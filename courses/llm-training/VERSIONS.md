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
| Transformer Engine | Site-qualified optional version using `te.autocast` | H100 ABI and runtime pending |
| GPU and Slurm | One full H100; two nodes for DDP/FSDP2 mechanics | Live validation pending |

Do not use deprecated `fp8_autocast` examples. Record the actual
Transformer Engine recipe, warm-up, PyTorch ABI, and kernels with FP8 results.

## DDP bucket and hook qualification

[Advanced Lab 21](../advanced-gpu-communication/reference/labs/21_ddp_buckets.md) uses the prepared PyTorch environment and the site-qualified
NCCL stack. Hook support, actual bucket behavior and numerical trajectories
require two-node H100 qualification. The Nsight launcher needs a compatible
profiler on both nodes. CPU reference and launcher tests do not establish these
live behaviors or training convergence.

## Shared profiling qualification

Nsight Systems **2026.4.1** and Nsight Compute **2026.2.1** are the cxcli-managed tool/viewer pair. The shared setup uses `soperator profiling install`, which owns package hashes, activation and matching viewers. Successful installation alone does not qualify the driver, both H100 workers, each AI/CUDA container, or hardware-counter access; retain pending status until the canaries and report inspections pass.

The shared setup specifies Soperator **4.1.8**, its release-owned VictoriaMetrics stack **0.39.4**, and private Grafana. Pushgateway **1.11.3** is pinned to `sha256:74fa117cef2d7e383112d25139ff1c2d2e309c35389a9e0554a47136a1482e48`; publisher dependencies are pinned in `tools/profiling-requirements.txt`. No additional DCGM collector or metrics database is installed. Keep the existing AI runtime versions above; a profiler update does not authorize changing those environments.

Prepared H200 runtime and applicable live profiler captures are recorded above. This campaign did not rerun the complete infrastructure installer or qualify the H100 target.

## Advanced fabric qualification

The documented base target uses two one-H100 workers; all distributed GPU experiments now belong to the separate two-eight-H100 route. Eight-/sixteen-rank launchers require full devices with MIG disabled, NVLink peer paths and active InfiniBand. Physical topology checks do not replace Fabric Manager health, MPI/NCCL compatibility or GPUDirect registration qualification. No live 16-H100 qualification is claimed by these source changes.

NVIDIA nvbandwidth source is pinned to `82fc4e8c6afa0babb8687793678f615b3b8d793e`; linux-rdma/perftest to `b513a77278c8061ca6c4dcd1a95d08801c6e7623`; MPI-enabled NCCL Tests 2.20.0 to `b4d5beebca8a76cf01335f724d154b9b9d394d96`. Shared environment setup records build hashes. Preserve existing course AI runtime pins and qualify the same runtime on both nodes before accepting a comparison.
