# Lab 19: Reduce gradients when they become ready

Backward produces parameter gradients at different times, creating opportunities to communicate an earlier gradient while later computation continues. This lab compares serialized reduction with gradient-ready asynchronous reductions across two H100 nodes. Compute captures the local backward companion; Systems captures distributed overlap.

## Run

```bash
# Normal execution
sbatch slurm/19_gradient_overlap.sbatch --workload small

# Optional profiling
sbatch slurm/19_gradient_overlap.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/19_gradient_overlap/jobs/<JOB_ID>/result.json
```

Useful overlap is governed by dependencies and the critical path.
