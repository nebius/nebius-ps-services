# Declared dependency baseline

The course content was statically reviewed on 2026-09-01 against current official documentation. The examples declare Python 3.12 and PyTorch 2.13.0 as their direct version baseline, with pytest 9.1.1 as the offline test runner. The launch-geometry lab also requires the compatible Triton package installed with the Linux CUDA PyTorch environment. The supplied requirements file is not a complete environment lock: cluster owners must provide an approved lock file or immutable image that resolves Python, PyTorch, Triton, CUDA user-mode libraries, and transitive dependencies together. The labs require an NVIDIA H100 (SM90) and a CUDA-capable PyTorch build supported by the cluster driver.

Cluster CUDA drivers, NCCL, Slurm, and fabric plugins are site-owned. Record their actual versions before benchmarking. Revalidate the course when changing a declared package version or major cluster component; do not assume timings or command-line flags transfer unchanged.
