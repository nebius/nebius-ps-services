# Lab 28: Test NCCL network-adapter selection

A host channel adapter, or HCA, connects a worker to InfiniBand. NCCL selects usable adapters and communication paths from topology.

## Run

```bash
# Normal execution
sbatch slurm/28_nic_selection.sbatch --workload small --server-hca "$SERVER_HCA" --client-hca "$CLIENT_HCA" --hca-policy all

# Optional profiling
sbatch slurm/28_nic_selection.sbatch --workload small --server-hca "$SERVER_HCA" --client-hca "$CLIENT_HCA" --hca-policy all --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/28_nic_selection/jobs/<JOB_ID>/result.json
```

Explain what changed, which observation supports the mechanism, and which alternative explanation remains.
