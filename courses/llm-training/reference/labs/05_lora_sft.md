# Lab 05: Fine-tune a small model with LoRA adapters

LoRA trains low-rank adapter parameters while leaving most pretrained weights frozen. This lab performs a short supervised fine-tuning exercise on a pinned public model and checks that adapters receive gradients and actually change.

## Run

```bash
# Normal execution
sbatch slurm/05_lora_sft.sbatch --workload small

# Optional profiling
sbatch slurm/05_lora_sft.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/05_lora_sft/jobs/<JOB_ID>/result.json
```

Compare held-out predictions and adapter updates; this in-memory exercise masks padding only and does not save checkpoints.
