# Where to Go Next

These optional reading directions combine
recent developments with established advanced concepts; they do not add
required labs or change the pinned H100 environment. Select a topic that
matches an observed bottleneck, not merely a new product name. Read its
current support requirements before trying it separately; documentation
availability is not course qualification.

## Regional and ahead-of-time compilation

Regional compilation compiles selected reusable parts of a model instead of
treating the whole model as one compilation unit. Ahead-of-time compilation
moves compilation work before deployment. Together, these approaches extend
the course's warm-up discussion into the trade-off between startup cost,
reusable compiled regions and steady-state execution.
**Investigate**: How many executions are needed to repay compilation cost,
and do new shapes invalidate that calculation?
**Scope**: Evolving PyTorch compiler tooling, not Blackwell-only. Export and
backend constraints vary by release; the reading does not upgrade PyTorch or
add a deployment lab.
[Study regional ahead-of-time compilation](https://docs.pytorch.org/tutorials/recipes/regional_aot.html).

## CUPTI range profiling and performance-monitor sampling

CUPTI is NVIDIA's instrumentation interface beneath many profiling tools.
Range profiling collects counters for a selected region of work;
performance-monitor sampling observes counters at intervals over time. These answer
different questions from a single kernel-duration measurement and help
explain why profiler replay and observation overhead matter.
**Investigate**: When would a time series reveal behavior hidden by one
aggregate measurement, and how would you measure the observer's overhead?
**Scope**: Evolving profiling APIs; NVIDIA recommends Range Profiling instead
of the older Profiling API deprecated in CUDA 13.0. Check device, tool and
counter-access requirements; do not change administrator permissions.
[Explore the current CUPTI interfaces](https://docs.nvidia.com/cupti/).

## GPUDirect Storage and the end-to-end input path

GPUDirect Storage can transfer data between storage and GPU memory without an
intermediate CPU-memory copy on supported paths. It extends input-pipeline
analysis beyond workers and pinned buffers to storage, filesystems and
topology. It does not remove application preprocessing or guarantee that every
transfer takes a direct path.
**Investigate**: Is the bottleneck storage bandwidth, preprocessing, transfer
or GPU work, and does the selected path actually avoid CPU staging?
**Scope**: Established advanced technology with evolving support. H100 is not
sufficient by itself: filesystem, driver and topology requirements must be
qualified. Cluster storage changes are outside this course.
[Read the GPUDirect Storage design guide](https://docs.nvidia.com/gpudirect-storage/design-guide/index.html).

## Device-initiated communication and NCCL GIN

NCCL's device API lets CUDA kernels initiate communication instead of relying
only on host-issued collectives. GPU-Initiated Networking (GIN) extends this
idea to supported network paths. This changes where communication is scheduled
and how its progress can overlap computation; it is not simply another
host-side collective setting.
**Investigate**: Which synchronization and buffer-lifetime responsibilities
move into the kernel, and when would that improve end-to-end overlap?
**Scope**: Recent communication interface; device API support begins with
NCCL 2.28, while GIN requires an appropriate later version and supported
transport. Conceptual reading only here: the Python labs and two-node baseline
do not assume the required connectivity or implement device-side primitives.
[Study NCCL device-initiated communication](https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/usage/deviceapi.html).

## Energy per useful result

Energy efficiency measures the energy required to finish useful, correct work,
not just instantaneous power or GPU utilization. NVIDIA Management Library
(NVML) exposes device telemetry, including energy counters on supported
devices. Connecting those measurements to completed work broadens optimization
from elapsed time alone to time, energy and quality constraints.
**Investigate**: Can a lower-power configuration consume more energy because
it runs longer, and what is excluded from a GPU-only measurement?
**Scope**: Established advanced measurement concept, not a new hardware
feature. Check counter support and sampling boundaries; GPU telemetry does
not include the entire host or data center. Keep exploration read-only.
[Consult the NVML device-query reference](https://docs.nvidia.com/deploy/nvml-api/group__nvmlDeviceQueries.html).
