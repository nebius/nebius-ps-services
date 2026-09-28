# Lab 01: Verify the GPU fabric and rank placement

A rank is one participating process, and its local rank chooses a GPU on its own node. In this experiment you will measure a baseline, inspect the evidence, and change one control while keeping useful work fixed. The goal is a defensible explanation backed by correctness checks and repeated observations; a candidate is allowed to be slower.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/01_fabric_topology.json).

**Advanced fabric route:** use the separate Soperator cluster with two eight-H100 workers (16 GPUs), healthy intra-node NVLink/NVSwitch and active inter-node InfiniBand. The base two one-GPU TCP workers are useful for local labs but cannot establish this fabric’s performance.

Keep model, software, clocks and other workload activity fixed; preserve private artifacts for both runs.

**Login node, from `~/courses/advanced-gpu-communication`:**

The owner supplies CUDA/NCCL/MPI development libraries, `nvcc`, CMake 3.20+, C and C++17 compilers, Git, make, autotools, pkg-config, libibverbs/libibumad/librdmacm, NUMA, and PCI development headers and libraries.

The pinned perftest build needs `pci/pci.h` and `libpci`: these are supplied by `libpci-dev` on Debian/Ubuntu or `pciutils-devel` on RPM systems. The installer checks that they compile and link before creating a build directory; owner-supplied `CC`, `CPPFLAGS`, `CFLAGS`, `LDFLAGS`, and `LIBS` apply to that check.

The pinned nvbandwidth build fetches its pinned argparse dependency through CMake. Drivers, NVSwitch/Fabric Manager, InfiniBand and DMA-BUF or nvidia-peermem must already be qualified. Build user-space tools once in shared storage:

```bash
python3 tools/install_fabric_tools.py --prefix "$COURSE_TOOLS"
source "$COURSE_TOOLS/fabric/environment.sh"
```

Source the fabric environment in each submission shell. It preserves existing library paths and adds the installed perftest library directory so CUDA data validation can load `libperftest_kernels.so`. A successful build alone does not make that plugin discoverable.

Before experiments, verify both workers and all eight GPUs per worker:

```bash
srun --nodes=2 --ntasks=2 --ntasks-per-node=1 --gpus-per-task=8 \
  "$COURSE_PYTHON" tools/fabric_guard.py
```

## Concepts and code path

A rank is one participating process, and its local rank chooses a GPU on its own node. Sixteen ranks must occupy sixteen distinct GPUs, with local ranks 0–7 on each worker. The known all-reduce adds rank values 1 through 16, so every element must become 136. Eight ranks on one node sum to 36. The launcher checks eight full H100s, seven NVLink peer paths per GPU and active InfiniBand ports before starting. The source writes a completed result only after its correctness checks pass. Measurement and publication run separately, so exporting evidence cannot distort the timed operation.

## Practice

On the login node, submit the baseline and candidate below. Save both job numbers and printed result paths.

```bash
python3 tools/submit_lab.py --lab 01_fabric_topology slurm/fabric.sbatch labs/01_fabric_topology.py --profile small
python3 tools/submit_lab.py --lab 01_fabric_topology slurm/fabric.sbatch labs/01_fabric_topology.py --profile small
```

Logs are created before submission under `results/01_fabric_topology/logs/<job>.out` and `.err`. A submitted job is not a completed result.

## Check your results

Wait for both jobs to complete successfully. Inspect the measured fields and correctness status; a failed check must be resolved before comparing performance.

```bash
sacct -j "${LAB_JOB_ID:?job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 01_fabric_topology --job "$LAB_JOB_ID"
```

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Participating ranks | `ranks` | none |
| NVLink peers per GPU | `nvlink_peers_per_gpu` | none |
| Minimum active IB ports per node | `active_ib_ports_per_node_min` | none |
| Collective duration | `all_reduce.median_ms` | s |

Select the two unprofiled result artifacts. The publisher checks equivalent parameters and allows only the named change. Repeated qualification uses no changed parameter.

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 01_fabric_topology \
  --baseline "${BASELINE_RESULT:?baseline JSON}" --candidate "${CANDIDATE_RESULT:?candidate JSON}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select the workspace and profile. Require **Correctness of selected results** to equal 1 and **Selected comparison generation** to match publication confirmation. Set the time picker from **Experiment start** to **Experiment end**, then select the allocated workers with **GPU worker**, then choose their local indices with **GPU index on selected workers** for telemetry. Summary panels show the selected pair; telemetry describes its actual job window.

## Investigate the behavior

Inspect ranks, NVLink peers and active IB ports in Grafana, then the exact collective sum in JSON. Require the cluster owner to check Fabric Manager and NVSwitch health separately: NVLink labels alone do not prove switch health or GPUDirect RDMA. Repeat qualification before and after an owner-approved repair; readiness is a pass/fail result, not a speedup.

Capture a separate diagnostic run:

```bash
python3 tools/submit_lab.py --lab 01_fabric_topology --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/fabric.sbatch labs/01_fabric_topology.py --profile small
```

**Nsight Systems evidence:** Capture inside each participating GPU rank, retaining separate reports for cross-rank correlation. Open all rank reports. Expand CUDA streams and the course_measure NVTX range; confirm the tiny all-reduce appears on every participating rank. This is a readiness trace, not a bandwidth benchmark. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A missing required full GPU (H100 or H200), peer path, InfiniBand port, rank or result is a failed prerequisite. Stop and inspect the per-lab job log. Do not force a transport or change cluster configuration to disguise a failed check. The owner must repair the supported runtime before another trial.

Publication failure is distinct from benchmark failure. Retain valid JSON and republish using the reviewed generation. Missing metrics remain unknown; failed ingestion does not mean the workload failed. A candidate need not be faster to teach a useful result.

## Takeaways and next step

Explain which measured observation supports your hypothesis, which alternative explanation remains, and whether the one-variable change should be kept. Repeat enough clean runs to expose variation, and report topology and workload limits with the conclusion.
