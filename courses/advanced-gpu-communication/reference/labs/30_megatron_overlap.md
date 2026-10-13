# Lab 30: Overlap Megatron gradient communication

Megatron Bridge connects model configuration to Megatron distributed training. Gradient communication can begin while later backward computation is still running.

## Run

```bash
# Normal execution
sbatch slurm/30_megatron_overlap.sbatch --workload small --overlap off --reference "$TRAINING_REFERENCE"

# Optional profiling
sbatch slurm/30_megatron_overlap.sbatch --workload small --overlap off --reference "$TRAINING_REFERENCE" --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/30_megatron_overlap/jobs/<JOB_ID>/result.json
```

Explain what changed, which observation supports the mechanism, and which alternative explanation remains.
