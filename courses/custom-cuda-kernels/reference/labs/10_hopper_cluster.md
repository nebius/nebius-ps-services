# Lab 10: Qualify an optional thread-block-cluster launch

Hopper thread-block clusters introduce an additional execution grouping beyond independent blocks. This optional lab checks that the declared cluster launch can run and produce the expected block IDs; prepare this path with `COURSE_ENABLE_SM90A=ON`.

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
