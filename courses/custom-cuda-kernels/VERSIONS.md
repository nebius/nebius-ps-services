# Version qualification

## Completed prepared-environment runs

All 13 practical labs completed both `small` and `large` profiles (26 verified exports) on the prepared NVIDIA H200 cluster. See [lab results](reference/lab-results/) and the [catalog validation record](../docs/profiling-validation.md) for evidence scope and limitations.

Lab 13 records CUDA runtime and driver API version `13030` on compute capability `9.0`. Those fields identify the observed runtime/API, not the compiler, CMake version or a qualified development-image digest.

The observed profiler CLI/viewer pairs were Nsight Systems `2026.4.1` and Nsight Compute `2026.2.1`; the scheduler reported Slurm `25.11.3`. These runs qualify the recorded experiments in their prepared environments. They do not establish a clean installation, every optional extension, or performance on H100.

## H100 target and installation candidates

The following targets and pending checks describe the H100 teaching environment. Keep them separate from the completed H200 evidence above.

| Component | Course target | Evidence status |
| --- | --- | --- |
| C++ language | C++20 | Source contract established |
| CUDA toolkit | CUDA 13.3 development image candidate | Exact image digest and H100 activation pending |
| Required architecture | SM90 | Compilation and runtime pending |
| Optional architecture | SM90a | Disabled; activation pending |
| CMake | Site/toolkit-compatible current release | Configuration pending on Linux target |
| CUB and cuBLAS | Toolkit-provided | Build/runtime pending |
| CUTLASS | 4.6.1 source contract for required Lab 09 | Exact source checkout, CUDA build, and H100 runtime qualification pending |
| GPU | One full non-MIG H100 | Live validation pending |

Do not describe an image digest or CUTLASS revision as qualified until that
configuration passes the relevant target checks. Label untested configurations
as candidates. Report compiler results, sanitizer results and speedups only
from checks actually executed in the declared environment.

## Shared profiling qualification

Nsight Systems **2026.4.1** and Nsight Compute **2026.2.1** are the cxcli-managed tool/viewer pair. The shared setup uses `soperator profiling install`, which owns package hashes, activation and matching viewers. Successful installation alone does not qualify the driver, both H100 workers, each AI/CUDA container, or hardware-counter access; retain pending status until the canaries and report inspections pass.

The shared setup specifies Soperator **4.1.8**, its release-owned VictoriaMetrics stack **0.39.4**, and private Grafana. Pushgateway **1.11.3** is pinned to `sha256:74fa117cef2d7e383112d25139ff1c2d2e309c35389a9e0554a47136a1482e48`; publisher dependencies are pinned in `tools/profiling-requirements.txt`. No additional DCGM collector or metrics database is installed. Keep the existing AI runtime versions above; a profiler update does not authorize changing those environments.

Prepared H200 runtime and applicable live profiler captures are recorded above. This campaign did not rerun the complete infrastructure installer or qualify the H100 target.

## Advanced fabric qualification

The documented base target uses two one-H100 workers; all distributed GPU experiments now belong to the separate two-eight-H100 route. Eight-/sixteen-rank launchers require full devices with MIG disabled, NVLink peer paths and active InfiniBand. Physical topology checks do not replace Fabric Manager health, MPI/NCCL compatibility or GPUDirect registration qualification. No live 16-H100 qualification is claimed by these source changes.

NVIDIA nvbandwidth source is pinned to `82fc4e8c6afa0babb8687793678f615b3b8d793e`; linux-rdma/perftest to `b513a77278c8061ca6c4dcd1a95d08801c6e7623`; MPI-enabled NCCL Tests 2.20.0 to `b4d5beebca8a76cf01335f724d154b9b9d394d96`. Shared environment setup records build hashes. Preserve existing course AI runtime pins and qualify the same runtime on both nodes before accepting a comparison.
