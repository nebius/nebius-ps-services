# Advanced cluster readiness

Complete [shared environment setup](../../lab-guide.html#lab-preparation-scripts) on the dedicated two-worker eight-H100 cluster. Qualification is a gate, not an optimization. Inspect each job's exit state and completed artifact; submitting a job does not pass this gate.

For the Fundamentals Lesson 12 assignment, read through that lesson, run Lab 02
and verify its placement and correctness before running Lab 08. This assignment
does not require Optimization, a specialization, Lab 01 or Labs 03–07. Both labs
reserve sixteen GPUs and use one participating rank per worker; their supplied
launchers perform the fabric checks independently.

For the full Advanced route, begin qualification in this order:

```bash
sbatch slurm/01_fabric_topology.sbatch --workload small
sbatch slurm/02_collective_readiness.sbatch --workload small
sbatch slurm/03_training_readiness.sbatch --workload small
sbatch slurm/04_inference_readiness.sbatch --workload small
sbatch slurm/05_transport_readiness.sbatch --workload small
```

Both entry points require full non-MIG H100 devices, local NVLink/NVSwitch, active InfiniBand and the documented correctness checks. Require sixteen distinct ranks for Lab 01 and two participating ranks on distinct workers for the mechanics preflights. Ask the cluster owner to verify NVSwitch/Fabric Manager and driver registration readiness independently. NCCL initialization does not prove every intended transport or every later workload is supported.

Before the vendor labs, record the exact runtime and binary identities and run their small profiles. Opening one valid Systems report, one isolated Compute report and the setup dashboard is required. Keep vendor environment, counter permission, live transport and model-run status separate; any missing gate remains pending.
