# Lab 06: Compare copy engines and SM peer traffic

NVLink carries peer GPU traffic; NVSwitch connects the eight GPUs within this worker. In this experiment you will measure a baseline, inspect the evidence, and change one control while keeping useful work fixed. The goal is a defensible explanation backed by correctness checks and repeated observations; a candidate is allowed to be slower.

## Before you start

Use the [Lab Guide](../../../lab-guide.html#lab-preparation-scripts) once to prepare this course and lab number before submitting jobs.

**Advanced fabric route:** use the separate Soperator cluster with two eight-H100 workers (16 GPUs), healthy intra-node NVLink/NVSwitch and active inter-node InfiniBand. The base two one-GPU TCP workers are useful for local labs but cannot establish this fabric’s performance.

Reserve one whole eight-GPU worker for this peer matrix. Keep model, software, clocks and other workload activity fixed; preserve private artifacts for both runs.

## Concepts and code path

NVLink carries peer GPU traffic; NVSwitch connects the eight GPUs within this worker. NVIDIA nvbandwidth measures each directed source-to-destination path with data verification enabled. Copy-engine (CE) and streaming-multiprocessor (SM) tests use different execution resources for the same transfer. Eight GPUs have 8 × 7 = 56 off-diagonal directed pairs. The matrix is a set of individual transfers, not a measurement of all pairs saturating the fabric simultaneously. The source writes a completed result only after its correctness checks pass. Measurement and publication run separately, so exporting evidence cannot distort the timed operation.

## Practice

`labs/06_nvlink_bandwidth.py` runs NVIDIA nvbandwidth across all 56 directed GPU peer paths on one eight-GPU node using the selected copy engine. It validates the vendor report and writes bandwidth measurements plus pairwise correctness evidence.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/06_nvlink_bandwidth/logs/%j.out" \
  --error="$PWD/results/06_nvlink_bandwidth/logs/%j.err" --nodes=1 slurm/06_nvlink_bandwidth.sbatch --workload small --engine ce
```

## Check your results

Each new job owns `results/06_nvlink_bandwidth/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/06_nvlink_bandwidth/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Wait for both jobs to complete successfully. Inspect the measured fields and correctness status; a failed check must be resolved before comparing performance.

Record each successful submission's job number. For each job, require `COMPLETED` and exit code `0:0`, then open its own logs and printed result path:

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/06_nvlink_bandwidth/logs/$LAB_JOB_ID.out"
cat "results/06_nvlink_bandwidth/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Slowest directed peer bandwidth | `minimum_GBps` | GBs |
| Mean directed peer bandwidth | `mean_GBps` | GBs |
| Validated directed GPU pairs | `pair_count` | none |

Select the two unprofiled result artifacts. The publisher checks equivalent parameters and allows only the named change. Repeated qualification uses no changed parameter.

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it.

```bash
source tools/course_env.sh 06_nvlink_bandwidth --lab
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 06_nvlink_bandwidth \
  --baseline "${BASELINE_RESULT:?baseline JSON}" --candidate "${CANDIDATE_RESULT:?candidate JSON}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select the workspace and profile. Require **Correctness of selected results** to equal 1 and **Selected comparison generation** to match publication confirmation. Set the time picker from **Experiment start** to **Experiment end**, then select the allocated workers with **GPU worker**, then choose their local indices with **GPU index on selected workers** for telemetry. Summary panels show the selected pair; telemetry describes its actual job window.

## Investigate the behavior

### Workload variations

On the login node, submit the baseline and candidate below. Save both job numbers and printed result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/06_nvlink_bandwidth/logs/%j.out" \
  --error="$PWD/results/06_nvlink_bandwidth/logs/%j.err" --nodes=1 slurm/06_nvlink_bandwidth.sbatch --workload small --engine ce
sbatch --chdir="$PWD" \
  --output="$PWD/results/06_nvlink_bandwidth/logs/%j.out" \
  --error="$PWD/results/06_nvlink_bandwidth/logs/%j.err" --nodes=1 slurm/06_nvlink_bandwidth.sbatch --workload small --engine sm
```

Slurm writes job logs under `results/06_nvlink_bandwidth/logs/<job>.out` and `.err`. A submitted job is not a completed result.

Compare the slowest pair and mean bandwidth, then open the private vendor JSON and locate asymmetric pairs in bandwidth_matrix. A CE-versus-SM difference is a mechanism study; decide which resource your application can spare. Independently repeat the same engine three times. Keep buffer size and sample count fixed. H100 inter-node InfiniBand is not nvbandwidth multi-node NVLink/IMEX; this lab intentionally stays within one worker.

Capture a separate diagnostic run:

```bash
sbatch --nodes=1 --chdir="$PWD" \
  --output="$PWD/results/06_nvlink_bandwidth/logs/%j.out" \
  --error="$PWD/results/06_nvlink_bandwidth/logs/%j.err" slurm/06_nvlink_bandwidth.nsys.sbatch --workload small --engine ce
```

The native Systems command is in `slurm/06_nvlink_bandwidth.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

**Nsight Systems evidence:** Capture the actual vendor executable inside each allocated worker; preserve raw numerical output separately. Open nvbandwidth.nsys-rep beside the private vendor JSON. Compare CUDA GPU memory-copy rows for CE against kernel rows for SM at the same buffer size. NVTX ranges are vendor-owned; no course_measure annotation is promised. Use clean matrix bandwidth for the comparison, not capture timings. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A missing required full GPU (H100 or H200), peer path, InfiniBand port, rank or result is a failed prerequisite. Stop and inspect the per-lab job log. Do not force a transport or change cluster configuration to disguise a failed check. The owner must repair the supported runtime before another trial.

Publication failure is distinct from benchmark failure. Retain valid JSON and republish using the reviewed generation. Missing metrics remain unknown; failed ingestion does not mean the workload failed. A candidate need not be faster to teach a useful result.

## Takeaways and next step

Explain which measured observation supports your hypothesis, which alternative explanation remains, and whether the one-variable change should be kept. Repeat enough clean runs to expose variation, and report topology and workload limits with the conclusion.
