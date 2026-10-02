# Lab 35: Trace fixed-parameter autoregressive inference

This exercise shows how repeated predictions create a token sequence while the model's parameters stay unchanged. It uses an explicit four-token vocabulary and a fixed table of next-token scores, so every decision can be checked by hand. You will inspect logits, probabilities, token feedback and stopping without downloading a language model or launching a server. This is a bigram teaching model, not a transformer, attention implementation or KV-cache benchmark.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Use the inference mechanics PyTorch environment. CPU mode is the default; CUDA mode requires an explicitly selected full H100. No tokenizer package, external dataset, remote code or model weights are fetched. Read the opening definitions of inference, token, logit, greedy decoding and end-of-sequence. The supplied vocabulary is already tokenized: a real application must additionally format and tokenize text. Shared profile and seed metadata do not change this fixed example.

## Concepts and code path

`run_experiment` constructs a four-by-four embedding table, uses `torch.no_grad()` to initialize every score to a declared constant, and snapshots the parameters. Each row represents scores for the next token given one current token. Starting with I, the forward call selects its row; softmax converts scores into probabilities; argmax chooses a highest-probability token. That token becomes the next input unless it is the end marker. `main` validates the output budget, selects the device and writes the standard private result.

The model is in evaluation mode and the loop uses inference mode. Evaluation mode changes the behavior of certain modules such as dropout; it does not itself disable gradient tracking. Inference mode avoids graph recording here, and explicit equality checks prove this program did not change its table. No backward pass or optimizer is present. Per-step transfers to CPU make the trace easy to read and intentionally unsuitable for performance benchmarking.

## Practice

`labs/35_inference_basics.py` traces greedy generation through a fixed four-token bigram model. It records each choice and stop condition, checking the expected sequence, unchanged parameters, and absence of gradients; it needs no model download.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 \
  --chdir="$PWD" \
  --output="$PWD/results/35_inference_basics/logs/%j.out" \
  --error="$PWD/results/35_inference_basics/logs/%j.err" \
  slurm/cpu.sbatch \
  labs/35_inference_basics.py --device cpu
```

## Check your results

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/35_inference_basics/logs/$LAB_JOB_ID.out"
cat "results/35_inference_basics/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

The default `generated_tokens` are like, GPUs and the end marker, with `stop_reason` eos. The one-token budget yields only like and stops with reason length. The recorded sequence includes the end marker for teaching visibility; a user-facing renderer would normally omit it. Each score row contains a 4 and three -2 values. The winning probability is exp(4)/(exp(4) + 3 × exp(-2)), approximately 0.9926; the other probabilities sum to the remainder. Require `parameters_unchanged` true and `gradients_created` false. The program checks the exact expected token sequence, not meaningful language quality.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Correctness of selected results | `correctness` | Boolean pass |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 35_inference_basics \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. CPU runs have no allocated GPU; inspect their artifact fields and job logs. For optional CUDA runs, set the time picker to **Experiment start** through **Experiment end**, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Submit the default sequence and then a shorter output budget. CUDA is an optional alternative for observing the same mechanics on H100. Save each job number for the result checks above.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/35_inference_basics/logs/%j.out" \
  --error="$PWD/results/35_inference_basics/logs/%j.err" slurm/cpu.sbatch labs/35_inference_basics.py --device cpu
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/35_inference_basics/logs/%j.out" \
  --error="$PWD/results/35_inference_basics/logs/%j.err" slurm/cpu.sbatch labs/35_inference_basics.py --device cpu --max-new-tokens 1
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/35_inference_basics/logs/%j.out" \
  --error="$PWD/results/35_inference_basics/logs/%j.err" slurm/single_gpu.sbatch labs/35_inference_basics.py --device cuda
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Follow `steps`: which row was read, which token was chosen, and which input the next forward receives. Explain why changing the output budget changes work without improving a kernel. As a source-edit extension, change one score and predict the resulting sequence; keep the original run as the reference. How would sampling differ from greedy choice? Why does this table fail to model attention over a long prompt? Distinguish those missing mechanisms from the feedback loop it does demonstrate.

Capture a separate diagnostic run:

```bash
srun --nodes=1 --ntasks=1 --gpus-per-task=1 --cpus-per-task=8 --time=00:15:00 --kill-on-bad-exit=1 \
  --chdir="$PWD" --output="results/35_inference_basics/logs/capture-%J-%t.out" \
  --error="results/35_inference_basics/logs/capture-%J-%t.err" \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=nsys \
  nsys profile --trace=cuda,nvtx,osrt \
  --cuda-trace-scope=process-tree --sample=none --cpuctxsw=none \
  --discard-environment=true --force-overwrite=false \
  --duration=300 --kill=none --wait=all \
  --output "results/35_inference_basics/profiles/nsys-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" \
  "${COURSE_PYTHON:?source the course runtime}" labs/35_inference_basics.py --device cuda
```

**Nsight Systems evidence:** The documented --device cuda run executes GPU work; the optional CPU run does not. For --device cuda only, expand lab_workload and CUDA API/GPU rows. Locate embedding, softmax/argmax and host reads in each token step. Correlate them with the generated-token record; this bigram exercise does not measure transformer serving performance. CPU mode has no GPU profiling. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A zero or negative budget is rejected before PyTorch loads. A missing import requires the mechanics environment. CUDA selection fails clearly on a non-H100 device; do not silently reinterpret a CPU result as a GPU run. If edited scores change outputs, revise the declared expected behavior and its test together rather than disabling state checks. Real-model nondeterminism and tokenizer mismatch are outside this fixed-table experiment.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Inference changes request state while using fixed learned parameters. Autoregression feeds an earlier prediction into a later forward call; stopping bounds that loop. The next lesson expands this model into prompt prefill, transformer attention and KV-cached decode, where the first output is chosen from prefill's final-position logits. This lab measures none of those phases, TTFT or serving throughput.
