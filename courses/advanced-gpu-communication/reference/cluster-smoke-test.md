# Advanced cluster readiness

Complete [shared environment setup](../../README.md#how-to-set-up-the-lab) on the dedicated two-worker eight-H100 cluster. Qualification is a gate, not an optimization. Inspect each job's exit state and completed artifact; submitting a job does not pass this gate.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/01_fabric_topology/logs/%j.out" \
  --error="$PWD/results/01_fabric_topology/logs/%j.err" slurm/fabric.sbatch labs/01_fabric_topology.py --profile small
sbatch --chdir="$PWD" \
  --output="$PWD/results/02_collective_readiness/logs/%j.out" \
  --error="$PWD/results/02_collective_readiness/logs/%j.err" slurm/two_node.sbatch labs/02_collective_readiness.py --profile small
sbatch --chdir="$PWD" \
  --output="$PWD/results/03_training_readiness/logs/%j.out" \
  --error="$PWD/results/03_training_readiness/logs/%j.err" slurm/training_two_rank.sbatch labs/03_training_readiness.py --profile small
sbatch --chdir="$PWD" \
  --output="$PWD/results/04_inference_readiness/logs/%j.out" \
  --error="$PWD/results/04_inference_readiness/logs/%j.err" slurm/two_node.sbatch labs/04_inference_readiness.py --profile small
sbatch --chdir="$PWD" \
  --output="$PWD/results/05_transport_readiness/logs/%j.out" \
  --error="$PWD/results/05_transport_readiness/logs/%j.err" slurm/two_node.sbatch labs/05_transport_readiness.py --profile small
```

Require sixteen distinct ranks for Lab 01, two participating ranks on distinct workers for the mechanics preflights, full H100 devices, active InfiniBand and the documented correctness checks. Ask the cluster owner to verify NVSwitch/Fabric Manager and driver registration readiness independently. NCCL initialization does not prove every intended transport or every later workload is supported.

Before the vendor labs, record the exact runtime and binary identities and run their small profiles. Opening one valid Systems report, one isolated Compute report and the setup dashboard is required. Keep vendor environment, counter permission, live transport and model-run status separate; any missing gate remains pending.
