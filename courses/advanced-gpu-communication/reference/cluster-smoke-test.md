# Advanced cluster readiness

Complete [shared environment setup](../../lab-guide.html#lab-preparation-scripts) on the dedicated two-worker eight-H100 cluster. Qualification is a gate, not an optimization. Inspect each job's exit state and completed artifact; submitting a job does not pass this gate.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/01_fabric_topology/logs/%j.out" \
  --error="$PWD/results/01_fabric_topology/logs/%j.err" slurm/01_fabric_topology.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/02_collective_readiness/logs/%j.out" \
  --error="$PWD/results/02_collective_readiness/logs/%j.err" slurm/02_collective_readiness.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/03_training_readiness/logs/%j.out" \
  --error="$PWD/results/03_training_readiness/logs/%j.err" slurm/03_training_readiness.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/04_inference_readiness/logs/%j.out" \
  --error="$PWD/results/04_inference_readiness/logs/%j.err" slurm/04_inference_readiness.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/05_transport_readiness/logs/%j.out" \
  --error="$PWD/results/05_transport_readiness/logs/%j.err" slurm/05_transport_readiness.sbatch --workload small
```

Require sixteen distinct ranks for Lab 01, two participating ranks on distinct workers for the mechanics preflights, full H100 devices, active InfiniBand and the documented correctness checks. Ask the cluster owner to verify NVSwitch/Fabric Manager and driver registration readiness independently. NCCL initialization does not prove every intended transport or every later workload is supported.

Before the vendor labs, record the exact runtime and binary identities and run their small profiles. Opening one valid Systems report, one isolated Compute report and the setup dashboard is required. Keep vendor environment, counter permission, live transport and model-run status separate; any missing gate remains pending.
