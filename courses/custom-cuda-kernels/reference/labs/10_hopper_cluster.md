# Lab 10: Qualify an optional thread-block-cluster launch

Hopper thread-block clusters introduce an additional execution grouping beyond independent blocks. This optional lab checks that the declared cluster launch can run and produce the expected block IDs. Shared setup prepares its separate SM90a build; this GPU job qualifies the optional execution path.

## Run

```bash
# Normal execution
sbatch slurm/10_hopper_cluster.sbatch --workload small

# Optional profiling
sbatch slurm/10_hopper_cluster.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/10_hopper_cluster/jobs/<JOB_ID>/result.json
```

Advanced hardware features need separately scoped activation and correctness evidence.
