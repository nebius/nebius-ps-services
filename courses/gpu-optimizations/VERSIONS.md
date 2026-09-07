# Version qualification

| Component | Course target | Evidence status |
| --- | --- | --- |
| Python | 3.12 | Installed locally; cluster parity pending |
| PyTorch | 2.14.0 manifest authority | Clean Linux/H100 install and qualification pending; no fallback approved |
| Nsight Systems and Compute | Site-compatible current tools | Live profiler activation pending |
| GPU | One full non-MIG H100; two nodes for owned distributed labs | Live validation pending |

Exact profiler and framework versions belong in every benchmark record because
trace schemas, kernel dispatch, and compiler behavior can change.

## Networking workshop qualification

NCCL Tests v2.20.0, source commit
`b4d5beebca8a76cf01335f724d154b9b9d394d96`, is the reviewed external benchmark
candidate. Its MPI-enabled build, exact per-node binary hashes and loaded
CUDA/NCCL/MPI libraries still require Linux/H100 qualification. The course
does not install it or change the PyTorch environment. Current official NCCL
documentation describes 2.31.2; this is a research context, not a runtime pin.
Record `torch.cuda.nccl.version()` separately from the external benchmark's
nccl_headers and nccl_library records.

Core Lab 18 accepts only the v2.20.0 float/sum, two-rank standard table, with
one full H100 per node. The site supplies a qualified MPI/Slurm integration.
Optional per-iteration and tuning-report columns are separate diagnostics,
not accepted by the stable parser. RoCE/GDR/QP trials require a qualified
fabric and registration path; they are not implicit core platform features.

## Transfer-pipeline qualification

Labs 19 and 20 use the existing PyTorch candidate environment. Their source
contracts follow the documented transfer and profiling APIs, but CUDA stream
ordering, buffer lifetimes and overlap still require H100 qualification. CPU
control tests check loop logic; they do not establish concurrent device execution.
