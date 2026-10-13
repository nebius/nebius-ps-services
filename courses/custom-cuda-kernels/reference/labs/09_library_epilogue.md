# Lab 09: Compare a library GEMM with a fused epilogue

The matrix multiplication itself may already be well served by a library, while a following bias and activation add extra work. This lab compares cuBLAS plus a separate bias/ReLU kernel with a source-pinned CUTLASS epilogue path.

## Run

```bash
# Normal execution
sbatch slurm/09_library_epilogue.sbatch --workload small

# Optional profiling
sbatch slurm/09_library_epilogue.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/09_library_epilogue/jobs/<JOB_ID>/result.json
```

Preserve strong GEMM implementations when customizing surrounding work.
