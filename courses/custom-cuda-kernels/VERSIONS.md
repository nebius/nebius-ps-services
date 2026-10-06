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
| CUDA toolkit | Managed native CUDA 13.3.0 | Checksum-pinned archives; H100 activation pending |
| Required architecture | SM90 | Compilation and runtime pending |
| Lab 10 architecture | SM90a | Prepared separately; activation pending |
| CMake | Site/toolkit-compatible current release | Configuration pending on Linux target |
| CUB and cuBLAS | Toolkit-provided | Build/runtime pending |
| CUTLASS | 4.6.1 source contract for required Lab 09 | Exact source checkout, CUDA build, and H100 runtime qualification pending |
| GPU | One full non-MIG H100 | Live validation pending |

Do not describe native toolkit/build identities or the CUTLASS revision as
qualified until that configuration passes the relevant target checks. The
optional teaching container also requires its own immutable image identity. Label untested configurations
as candidates. Report compiler results, sanitizer results and speedups only
from checks actually executed in the declared environment.

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
