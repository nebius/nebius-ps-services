# Versions and environment

## Completed prepared-environment runs

All 34 practical labs completed both `small` and `large` profiles (68 verified exports) on the prepared NVIDIA H200 cluster. See [lab results](reference/lab-results/) and the [catalog validation record](../docs/profiling-validation.md) for evidence scope and limitations.

Recorded framework environments include PyTorch `2.14.0+cu130` with CUDA `13.0` and `2.13.0a0+8145d630e8.nv26.06` with CUDA `13.3`. Vendor engines retain their separate prepared environments. Allocation GPU counts do not establish the number of active ranks; use each lab's launch and result records.

The observed profiler CLI/viewer pairs were Nsight Systems `2026.4.1` and Nsight Compute `2026.2.1`; the scheduler reported Slurm `25.11.3`. These runs qualify the recorded experiments in their prepared environments. They do not establish a clean installation, every optional extension, or performance on H100.

## H100 target and installation candidates

The following targets and pending checks describe the H100 teaching environment. Keep them separate from the completed H200 evidence above.

| Environment | Qualification candidate | Identity and boundary |
| --- | --- | --- |
| Course PyTorch | requirements.txt | Separate shared virtual environment for the mechanics labs; record resolved packages. |
| Nsight Systems / Compute | Cluster-managed CLI/viewer pair | Record matching tool and viewer versions and qualify both-worker counter permissions; completed campaign versions remain above. |
| NIXLBench |v1.4.1 | Commit 778edd1d1a50936b12c264879e12ef465e629002; UCX 1.22.0 isolated from Dynamo. |
| Megatron Bridge |tag v0.6.0 | Commit 51885cf132b2814188b6855c25a8588254274c2a; tag declares package 0.6.1 plus source suffix. Python 3.12 and the isolated pinned Bridge runtime are prepared for its selected labs; ABI and execution still need qualification. |
| Dynamo |1.4.2 | Source 2ecbdfdf192c69c02c6d21e931d20d3b4a0bb64a; vLLM 0.26.0, Torch 2.11 / CUDA 13. Joint NIXL/NIXL-EP 1.3.2 cu13 build from de8115ca97d3f8fb63a4988e9b4d4a038b2e0f72 against the prepared runtime UCX/RDMA stack; SM90. |
| AIPerf |0.12.0 | Source `be53bf2953d30e46c500e6a80fc1f8b6f84bc718`; separate environment, emitted schema checked. |
| Serving model |Qwen/Qwen3-8B BF16 | Revision `b968826d9c46dd6066d109eabc6255188de91218`; fixed tokenizer and weights. |

Use the [Lab Guide](../lab-guide.html#lab-preparation-scripts) to select preparation by course and lab number, including fabric, NCCL Tests and model dependencies. Exact source pins reduce drift; they do not prove that the native dependencies or GPU/network runtime work together. Retain the resolved dependency inventory, actual package versions, driver, CUDA, NCCL, UCX, topology and binary hashes privately after qualification. Retain the resolved native package inventory for a delivered runtime and immutable image digests for explicitly selected container variants.

A Bridge tag name is not its distribution version. The pinned source and editable Megatron-Core submodule are the authority. Its reduced dense-workload dependency selection needs qualification for each new target; do not assert that a generic empty Python environment is equivalent to NVIDIA's prepared training stack.

Validate 16-GPU topology and vendor smoke runs before using their measured comparisons. Systems captures add overhead. Compute replay is restricted to isolated local kernels, not live distributed collectives. The two one-GPU TCP workers cannot qualify this platform's NVLink/NVSwitch or InfiniBand behavior.
