# Lab 30: Overlap Megatron gradient communication

Megatron Bridge connects model configuration to Megatron distributed training. Gradient communication can begin while later backward computation is still running. In this lab a small real transformer trains on deterministic synthetic tokens across sixteen data-parallel ranks. Change only gradient-reduction overlap, then compare completed step time and every final parameter against a separate baseline reference.

The recipe explicitly selects Bridge's `single` dataloader, which partitions
the seeded synthetic dataset sequentially across data-parallel ranks. Reference,
baseline and overlap-enabled runs keep this sample order fixed.
The attention backend supplies causal masking; the dataset does not create or
transfer a dense sequence-by-sequence mask. Otherwise the unused prefetched
masks can exhaust shared memory at the longer sequence length.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Use the dedicated two-worker, sixteen-H100 cluster prepared in shared environment setup. Verify local NVLink/NVSwitch and inter-node InfiniBand readiness. Keep driver, software, allocation and other workloads fixed; the two one-GPU TCP workers cannot establish this fabric's performance. The `small` and `large` names select workload sizes, not optimization or profiling modes.

Prepare the [joint vendor runtime](../../README.md) once, then source
`env/vendor-environment.sh` in this submission shell. The installer requires
the Bridge, NIXLBench and Dynamo prerequisites together; installation alone does
not qualify both workers for this experiment.

Reference preparation (complete before the measured baseline):

Submit the reference job first and wait for successful completion. Retain its printed job number and reference path.

Select the prepared Bridge runtime before submission and create one reference with the baseline control. Export `TRAINING_REFERENCE` as the printed `final-weights.pt` path after that job completes. Keep that same file for both measured runs.

```bash
export COURSE_PYTHON="$COURSE_BRIDGE_PYTHON"
export COURSE_TORCHRUN="$COURSE_BRIDGE_TORCHRUN"
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/30_megatron_overlap/logs/%j.out" \
  --error="$PWD/results/30_megatron_overlap/logs/%j.err" slurm/fabric.sbatch labs/30_megatron_overlap.py --profile small --reference-only
```

Logs stay under `results/30_megatron_overlap/logs/`. A submission receipt is not a measurement; wait for successful completion before selecting artifacts.

## Concepts and code path

The four-layer BF16 model uses a distributed optimizer, FP32 gradient reduction, fixed global batch and fixed sequence length. Parameter-gather overlap stays disabled. TensorBoard iteration-time is measured in seconds and includes the vendor training loop. The callback checks skipped steps, finite loss and full final weights; a reference mismatch blocks comparison. Synthetic tokens establish execution equivalence, not model quality or production scaling.

## Practice

`labs/30_megatron_overlap.py` trains a small Megatron Bridge transformer with gradient-reduction overlap disabled or enabled. It checks finite, unskipped steps and final weights against a separate baseline reference and writes timing, throughput, loss, and parameter error.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 \
  --chdir="$PWD" \
  --output="$PWD/results/30_megatron_overlap/logs/%j.out" \
  --error="$PWD/results/30_megatron_overlap/logs/%j.err" \
  slurm/fabric.sbatch \
  labs/30_megatron_overlap.py --profile small --overlap off --reference "$TRAINING_REFERENCE"
```

## Check your results

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

For pair publication, confirm both completed job states and inspect the actual JSON paths. Set `BASELINE_RESULT` and `CANDIDATE_RESULT` to those artifacts, never to stdout or profiler reports.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/30_megatron_overlap/logs/$LAB_JOB_ID.out"
cat "results/30_megatron_overlap/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Require `COMPLETED` and exit code `0:0` for each job. Reading JSON is inspection,
not validation: check `lab_id`, `experiment.slurm_job_id`, correctness and
instrumentation fields. Retain every original/aggregate required by this lab.

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it.

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 30_megatron_overlap \
  --baseline "${BASELINE_RESULT:?baseline JSON}" --candidate "${CANDIDATE_RESULT:?candidate JSON}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; reviewed current generation otherwise}"
```

| Dashboard panel | Measurement path | Display unit |
| --- | --- | --- |
| Median training step | `step_seconds` | s |
| Training tokens per second | `tokens_per_second` | tokens/s |
| Maximum parameter error | `parameter_max_abs_error` | none |

Select workspace and profile in Grafana. Require **Correctness of selected results** to equal 1 and **Selected comparison generation** to match confirmation. Set the time picker to **Experiment start** through **Experiment end** and select the allocated workers with **GPU worker**, then choose their local indices with **GPU index on selected workers**. Sampled utilization is context, not a per-kernel explanation or proof of transport selection.

## Investigate the behavior

### Workload variations

Run the candidate after the baseline passes, preserving the same reference weights and profile.

```bash
sbatch labs/30_megatron_overlap.py --profile small --overlap on --reference "$TRAINING_REFERENCE"
```

Capture overlap off and on in separate diagnostic jobs, using the same reference for both. Wait for each capture to finish before submitting the next.

Check exported statistics for every rank, then open representative reports from each worker in Systems and select bridge_training_step. Load large reports in small groups and close them between comparisons. Locate backward compute, gradient collectives and the final exposed communication tail. An overlap flag does not prove useful overlap: check whether concurrent communication slowed compute. Independently repeat the same experiment with the large-profile sequence length and report when exposed communication matters.

```bash
srun --nodes=2 --ntasks=2 --ntasks-per-node=1 --gpus-per-task=8 --cpus-per-task=32 --time=00:15:00 --kill-on-bad-exit=1 \
  --chdir="$PWD" --output="results/30_megatron_overlap/logs/capture-%J-%t.out" \
  --error="results/30_megatron_overlap/logs/capture-%J-%t.err" \
  bash slurm/capture_ranks.sh 8 \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=nsys \
  nsys profile --trace=cuda,nvtx,osrt,nccl \
  --cuda-trace-scope=process-tree --sample=none --cpuctxsw=none \
  --discard-environment=true --force-overwrite=false \
  --duration=300 --kill=none --wait=all \
  --output "results/30_megatron_overlap/profiles/nsys-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{RANK}-%p" \
  "${COURSE_PYTHON:?source the course runtime}" labs/30_megatron_overlap.py --profile small --overlap off --reference "$TRAINING_REFERENCE"
```

Keep diagnostic captures separate from acceptance timings. For distributed work, retain each rank's report and placement record; compare the same application phase across ranks. Nsight Compute replay is inappropriate for live collectives: investigate a separately isolated local kernel when kernel-level evidence is needed.

**Nsight Systems evidence:** Capture inside each participating GPU rank, retaining separate reports for cross-rank correlation. Check exported statistics for every rank, then open representative rank .nsys-rep reports from each worker. Load large reports in small groups and close them between comparisons. Expand CUDA streams, NCCL activity and available NVTX ranges; align collective boundaries and compare arrival, waiting and compute intervals across hosts. Use clock correlation before claiming cross-node overlap. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A missing worker, vendor field, transport, completed request or correctness check is missing evidence, never zero performance. Read the lab's private job logs and fix the failing prerequisite before another trial. Do not change drivers, network configuration or registration modules inside an experiment.

Publication failure is separate from benchmark failure. Retain successful JSON artifacts and republish with the reviewed generation after ingestion is repaired. Vendor versions are qualification candidates until the designated runtime and sixteen-GPU live checks pass.

## Takeaways and next step

Explain what changed, which observation supports the mechanism, and which alternative explanation remains. Repeat matched unprofiled runs and describe variation; retain a slower candidate when it disproves the initial hypothesis. Finish with the independent investigation above and a bounded decision for this workload and topology.
