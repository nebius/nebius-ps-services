# Where to Go Next

These optional directions extend the completed labs without adding completion requirements.

- **Scale the workload before scaling the claim**

  Repeat a matched comparison on a larger transformer and representative token lengths after the small run is numerically valid. Inspect whether compute, memory or communication becomes the limiting phase. Long training additionally needs convergence evidence; a short synthetic trajectory does not establish final model quality.

  [Megatron Bridge training documentation](https://docs.nvidia.com/nemo/megatron-bridge/latest/)

- **Study GPU-initiated communication**

  Investigate NVSHMEM and device-initiated communication after establishing the host-initiated baselines. Ask which synchronization and progress work moves to the GPU, what ordering guarantees change, and how to validate data before trusting overlap. These are optional extensions, not installed requirements.

  [NVIDIA NVSHMEM documentation](https://docs.nvidia.com/nvshmem/api/)

- **Extend expert-parallel serving**

  Use the existing expert-routing exercises to distinguish dispatch volume, token imbalance and combine cost. A production MoE backend requires its own model, transport and correctness qualification. Do not transfer dense-model serving conclusions directly to routed models or assume a technique requiring Blackwell hardware works on H100.

  [NVIDIA Dynamo documentation](https://docs.nvidia.com/dynamo/)
