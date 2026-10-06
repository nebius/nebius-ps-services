# Version qualification

## Completed prepared-environment runs

All 11 practical labs completed both `small` and `large` profiles (22 verified exports) on the prepared NVIDIA H200 cluster. See [lab results](reference/lab-results/) and the [catalog validation record](../docs/profiling-validation.md) for evidence scope and limitations.

The recorded framework is PyTorch `2.14.0+cu130` with CUDA runtime `13.0`.

The observed profiler CLI/viewer pairs were Nsight Systems `2026.4.1` and Nsight Compute `2026.2.1`; the scheduler reported Slurm `25.11.3`. These runs qualify the recorded experiments in their prepared environments. They do not establish a clean installation, every optional extension, or performance on H100.

## H100 target and installation candidates

The following targets and pending checks describe the H100 teaching environment. Keep them separate from the completed H200 evidence above.

| Component | Course target | Evidence status |
| --- | --- | --- |
| Python | 3.12 | Installed locally; target-cluster parity pending |
| PyTorch | 2.14.0 manifest authority | Clean Linux/H100 install and qualification pending; no fallback approved |
| CUDA runtime | PyTorch-qualified H100 runtime | Target activation pending |
| GPU | One full non-MIG NVIDIA H100, compute capability 9.0 | Live validation pending |
| Slurm | Site-supported version | Live validation pending |

Do not infer driver compatibility from a toolkit version alone. Record the
actual framework runtime, driver, GPU, and executed kernel path with results.

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
