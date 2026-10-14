# Lab 02: Verify the two-node H100 platform

Before interpreting GPU timings, establish that the program is running on the platform you think it is. This lab checks process placement, device visibility, and communication using one rank on each of the two eight-GPU workers. For the Fundamentals assignment, read through Lesson 12 and complete shared preparation and fabric qualification; Optimization, a specialization, Lab 01 and Labs 03–07 are not prerequisites. The supplied launcher still checks the sixteen-H100 fabric. Verify this lab's placement and correctness results before continuing to Lab 08.

## Run

```bash
# Normal execution
sbatch slurm/02_collective_readiness.sbatch --workload small

# Optional profiling
sbatch slurm/02_collective_readiness.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/02_collective_readiness/jobs/<JOB_ID>/result.json
```

You now have an explicit execution topology on which the collective experiment can depend.
