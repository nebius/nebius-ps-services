# Lab 31: Place hierarchical context-parallel communication

Context parallelism partitions a long token sequence across ranks while preserving attention over the full context. Hierarchical context parallelism uses different communication within and between nodes.

## Run

```bash
# Normal execution
sbatch slurm/31_context_parallel.sbatch --workload small --layout flat --reference "$CONTEXT_REFERENCE"

# Optional profiling
sbatch slurm/31_context_parallel.sbatch --workload small --layout flat --reference "$CONTEXT_REFERENCE" --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/31_context_parallel/jobs/<JOB_ID>/result.json
```

Explain what changed, which observation supports the mechanism, and which alternative explanation remains.
