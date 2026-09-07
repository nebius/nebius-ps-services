# Version qualification

| Component | Course target | Evidence status |
| --- | --- | --- |
| Python | 3.12 | Installed locally; target-cluster parity pending |
| PyTorch | 2.14.0 manifest authority | Clean Linux/H100 install and qualification pending; no fallback approved |
| CUDA runtime | PyTorch-qualified H100 runtime | Target activation pending |
| GPU | One full non-MIG NVIDIA H100, compute capability 9.0 | Live validation pending |
| Slurm | Site-supported version | Live validation pending |

Do not infer driver compatibility from a toolkit version alone. Record the
actual framework runtime, driver, GPU, and executed kernel path with results.
