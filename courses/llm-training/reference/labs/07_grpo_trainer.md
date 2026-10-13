# Lab 07: Verify the GRPO generation-to-update loop

An objective formula is only one part of reward-guided training: the system must generate completions, score them, compute a loss, propagate gradients, and update parameters. This lab exercises that loop with a small pinned model and LoRA adapters.

## Run

```bash
# Normal execution
sbatch slurm/07_grpo_trainer.sbatch --workload small --steps 3

# Optional profiling
sbatch slurm/07_grpo_trainer.sbatch --workload small --steps 3 --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/07_grpo_trainer/jobs/<JOB_ID>/result.json
```

A systems small test should have a controlled signal and observable state transition.
