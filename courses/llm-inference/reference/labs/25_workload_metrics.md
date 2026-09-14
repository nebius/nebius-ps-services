# Lab 25: Explore input length and recurrent operator work

Long inputs and long output sequences stress different parts of an inference workload, but a small operator model must not be mislabeled as a language model. A recurrent update feeds the previous hidden state (the intermediate values retained between steps) into the next computation. Here `tanh`, the hyperbolic tangent, maps each real input smoothly between -1 and 1, providing a bounded nonlinear transformation after a matrix product. This lab projects a batch of input vectors and repeatedly updates that hidden state with matmul and tanh. It teaches workload-shape reasoning while explicitly reporting operator work, not generated tokens or service latency.

## Before you start

**Theory preparation:** Read Lesson 5 for ISL, OSL, recurrence and the difference between parallel input work and dependent updates. The operator fixture and the operations included in its four timing cases are explained below; it does not generate tokens.

Use one H100 in the mechanics environment. The four cases combine input lengths 128/2048 with 32/512 recurrent iterations. The `--concurrency` option controls simultaneous batch rows here, not concurrent HTTP requests.

Shape-dependent Tensor Core efficiency and HBM/KV pressure make H100 performance highly workload-specific. Test full non-MIG allocation and record whether the server shares the device.

## Concepts and code path

The program computes a dense projection over all input positions, selects the last projected state, then applies repeated `tanh(state @ weight)` updates. It times projection, one recurrent step, the recurrence sequence, and the joined projection-plus-recurrence workload independently. There is no attention, KV cache, vocabulary head, sampling, or token output; the recurrence count is not a real model's OSL schedule.

## Practice

Given traffic weights of 60 percent at 128/128, 30 percent at 4,096/32, and 10 percent at 128/1,024, use these weights to construct the actual mixed request stream and measure completed output tokens divided by a common wall interval. Do not take a weighted arithmetic mean of isolated throughputs: batching and queueing couple requests, and even serial rate aggregation generally needs a different model. Change the long-prompt share during a scheduled batch window. Expected observation: TTFT and chunked-prefill policy may need a different qualified profile even when daily-average throughput appears unchanged.

Run Lab 25's four input-length/recurrent-work cells at several batch-row counts. It executes projection and recurrent matmuls, not attention, cache growth, vocabulary sampling, or generated tokens. Its operator rates illustrate shape effects only. Use Lab 18 to compare equivalent real-model padded/bucketed prefill prompts, and use the live engine workload generator for actual ISL × OSL × request-concurrency experiments.

Compare batch-row counts while keeping the four length/work cells fixed. Begin with the smaller batch and retain all cells rather than selecting only a favorable point.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/25_workload_metrics.py --profile smoke --concurrency 1
sbatch slurm/single_gpu.sbatch labs/25_workload_metrics.py --profile smoke --concurrency 4
```

## Check your results

Require four executed cells, declared operator counts, and finite recurrence output. Inspect `projected_input_rows`, `recurrent_row_updates`, phase distributions, joined timing, and row-updates/s. The joined rate uses a directly measured joined interval, not a sum of phase medians. Finiteness is not an independent numerical reference check.

For a separately qualified live-engine workload experiment, record workload distribution, arrival process, completed/failed requests, tokens, latency percentiles, throughput, and memory. These request-level measurements are not outputs of the supplied operator model.

Engine comparisons are meaningful only for the same workload matrix and arrival model.

## Investigate the behavior

Which operation grows with input length and which with iteration count? Why can batching improve matrix efficiency while increasing latency per completed batch? Contrast these simplified costs with real attention and cache growth omitted here.

More cells improve representativeness but increase runtime and model-serving cost. Synthetic fixed-length requests isolate mechanisms; natural prompts capture tokenizer and stop behavior. Both are useful when labeled.

## If something goes wrong

Do not interpret row-updates/s as output tokens/s or one recurrent-step duration as ITL. Non-finite states invalidate the cell. Memory exhaustion requires a smaller declared workload, not silently skipped cells.

Avoid benchmarking one convenient prompt and generalizing to every serving workload.

## Takeaways and next step

Synthetic operators are useful only with honest boundaries. Use Lab 09 for real prefill/continuation semantics and the live streaming/AIPerf workflows for TTFT, token-aware intervals, and output-token throughput.

Publish a representative distribution plus corner cases that isolate prefill, decode, and capacity.

Choose one cell that isolates each phase and one that stresses queueing.
