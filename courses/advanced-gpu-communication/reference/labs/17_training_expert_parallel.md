# Lab 17: Trace expert routing, gradients, and grouped work

Mixture-of-experts training distributes tokens to selected expert computations, then must return outputs and gradients to the correct owners. This lab implements a bounded two-rank example with explicit routing and reference checks.

## Run

```bash
# Normal execution
sbatch slurm/17_training_expert_parallel.sbatch --workload small

# Optional profiling
sbatch slurm/17_training_expert_parallel.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/17_training_expert_parallel/jobs/<JOB_ID>/result.json
```

EP performance depends on routing, balance, communication, and expert kernels together.
