# Version qualification

## Completed prepared-environment runs

All 22 practical labs completed both `small` and `large` profiles (44 verified exports) on the prepared NVIDIA H200 cluster. See [lab results](reference/lab-results/) and the [catalog validation record](../docs/profiling-validation.md) for evidence scope and limitations.

Recorded mechanics runs include PyTorch `2.14.0+cu130` and `2.13.0+cu130`, both with CUDA runtime `13.0`. Serving profiles use their own prepared environments; an empty software field in an export is not evidence of a particular engine or framework version. CPU-only lessons remain CPU evidence.

The observed profiler CLI/viewer pairs were Nsight Systems `2026.4.1` and Nsight Compute `2026.2.1`; the scheduler reported Slurm `25.11.3`. These runs qualify the recorded experiments in their prepared environments. They do not establish a clean installation, every optional extension, or performance on H100.

## H100 target and installation candidates

The following targets and pending checks describe the H100 teaching environment. Keep them separate from the completed H200 evidence above.

The introductory Lab 35 was executed locally on CPU with an existing PyTorch
2.13.0 environment. This is algorithmic smoke evidence only: it does not
qualify the separate mechanics/serving environments, authorize a dependency
fallback or establish CUDA/H100 behavior. It downloads no model or engine.

| Component | Course target | Evidence status |
| --- | --- | --- |
| Python | 3.12 | Local serving environment exists; cluster parity pending |
| PyTorch | 2.14.0 mechanics-manifest authority | Clean Linux/H100 install and qualification pending; no fallback approved |
| Transformers | Pinned mechanics candidate set | Target installation and joint compatibility pending |
| Native vLLM | 0.28.0 | Linux/H100 install and engine activation pending |
| TensorRT-LLM and Triton | Explicit optional pinned container variant | Installation and target qualification are separate |
| Native AIPerf | 0.12.0 | Target qualification pending |
| Dynamo container preflight | Explicit optional pinned container variant | Deferred pending transport/topology qualification |

For optional containers, do not substitute an unqualified “latest” image. Once validated, record the
full immutable digest, model revision, driver, runtime, and launch arguments.

## KV-retention policy qualification

Lab 36 uses only Python standard-library code and course result helpers. Its
capacities, transfer rates and prefill costs are declared modeling inputs, not
measurements. Running the model does not qualify CUDA, a serving engine,
persistent KV recovery or a GPUDirect Storage path.

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
