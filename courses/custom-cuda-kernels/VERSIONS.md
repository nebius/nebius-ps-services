# Version qualification

| Component | Course target | Evidence status |
| --- | --- | --- |
| C++ language | C++20 | Source contract established |
| CUDA toolkit | CUDA 13.3 development image candidate | Exact image digest and H100 activation pending |
| Required architecture | SM90 | Compilation and runtime pending |
| Optional architecture | SM90a | Disabled; activation pending |
| CMake | Site/toolkit-compatible current release | Configuration pending on Linux target |
| CUB and cuBLAS | Toolkit-provided | Build/runtime pending |
| CUTLASS | 4.6.1 source contract for required Lab 09 | Exact source checkout, CUDA build, and H100 runtime qualification pending |
| GPU | One full non-MIG H100 | Live validation pending |

Do not describe an image digest or CUTLASS revision as qualified until that
configuration passes the relevant target checks. Label untested configurations
as candidates. Report compiler results, sanitizer results and speedups only
from checks actually executed in the declared environment.
