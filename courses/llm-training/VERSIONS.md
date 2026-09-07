# Version qualification

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

Lab 33 uses the existing PyTorch candidate environment and the site-qualified
NCCL stack. Hook support, actual bucket behavior and numerical trajectories
require two-node H100 qualification. The Nsight launcher needs a compatible
profiler on both nodes. CPU reference and launcher tests do not establish these
live behaviors or training convergence.
