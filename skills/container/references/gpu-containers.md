# GPU Containers

Load this reference only for GPU, NVIDIA, CUDA, MIG, CDI, shared-memory, or
device-injection work.

## Contract

- Keep the host NVIDIA driver outside the application image.
- Select only the CUDA runtime and libraries needed by the application.
- Verify framework, CUDA, driver, GPU architecture or compute capability,
  operating system, and CPU architecture compatibility from current NVIDIA and
  framework documentation.
- Use the platform-supported NVIDIA Container Toolkit, CDI, or Kubernetes
  device-allocation interface.
- Record full-GPU versus MIG requirements and expected device count.
- Determine `/dev/shm`, IPC, pinned-memory, huge-page, and host-memory needs.
- Keep compilers and CUDA development tooling out of the final runtime image
  unless the application requires them.
- Do not use privileged mode or broad device mounts merely to expose GPUs.

## Validation

- Confirm CPU architecture and final-image platform.
- Confirm the runtime exposes only the requested GPU devices.
- Run an actual framework or application computation on the GPU and verify the
  result. `nvidia-smi` alone is discovery evidence, not application
  compatibility evidence.
- Exercise memory allocation, shared memory, model/library loading, and clean
  termination where relevant.
- Mark GPU types, MIG layouts, driver ranges, or platforms not actually tested
  as unvalidated.

Local audit and smoke scripts must not grant devices automatically. Device/CDI
options require an explicit GPU test profile and available trusted tooling.

## Official Sources

- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/)
- [NVIDIA CDI support](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/cdi-support.html)
