# Lab 27: Separate compiled expressions from captured training steps

Compilation and CUDA Graphs optimize different parts of execution and should not be treated as one indistinguishable feature. This lab checks a compiled expression separately from a captured fixed-shape training update.

## Run

```bash
# Normal execution
sbatch slurm/27_fused_graph_trace.sbatch --workload small

# Optional profiling
sbatch slurm/27_fused_graph_trace.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/27_fused_graph_trace/jobs/<JOB_ID>/result.json
```

Keep mechanism, correctness, and measurement scopes aligned.
