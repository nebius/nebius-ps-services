# Lab 01: Find the CPU–GPU crossover for vector work

A GPU can execute arithmetic quickly while still losing an application comparison because transferring data and launching work take time. This lab evaluates the same expression, `x * y + x`, on the CPU, on tensors already stored in GPU memory, and on the GPU with input and output transfers included. You will use CPU timers and CUDA events to compare these cases and state exactly which operations each measurement includes.

## Before you start

**Theory preparation:** Read Lesson 1 for CPU timers, CUDA events and the difference between timing GPU operations and timing a complete request with transfers. Complete the README setup and Lab 10 before this experiment. The formula, timing procedure and numerical check are explained below.

Use one H100 and the Fundamentals environment. The smoke profile tests 1,024 and 1,000,000 FP32 elements; the H100 profile adds 32,000,000. A profile selects workload size, not a different correctness standard. No dataset is needed. The two-node Lab 00 belongs to the final collective lesson.

H100 provides enormous parallel and matrix throughput, but it does not remove Python dispatch, launch latency, PCIe or network transfer, or application queueing. Large H100 peak numbers are relevant only after the measured path supplies enough eligible work.

## Concepts and code path

An elementwise operation applies the same formula independently at each position. Here `x * y + x` multiplies corresponding input values and adds `x`. The CPU computes the reference answer; a faster GPU result is useful only if it agrees with that answer.

Floating-point arithmetic rounds values. This lab accepts each GPU value only when `abs(candidate - reference) <= atol + rtol * abs(reference)`, using `atol=1e-6` and `rtol=1e-5`. Absolute tolerance allows a fixed difference near zero; relative tolerance scales with the CPU reference. For reference 2, the allowance is `2.1e-5` (0.000021); for reference zero, it is 0.000001. Every element must pass. A finite result alone does not establish agreement. Pure copies of fixed values can instead require exact equality.

Warm-up executes the GPU operation before steady-state samples so first-use setup is not included. The program creates CPU inputs and copies them to GPU memory once per size for the case with device-resident inputs. It then measures three cases:

| Result field | Timer and included work |
| --- | --- |
| `cpu_median_ms` | `time.perf_counter()` around `x * y + x` on CPU tensors. |
| `gpu_resident` | Timing-enabled CUDA events around the GPU expression, using inputs already in GPU memory. The helper records the start event, submits the expression, records the end event, waits for it with `end.synchronize()`, then reads `start.elapsed_time(end)`. |
| `gpu_with_transfers_median_ms` | `time.perf_counter()` before copying both CPU inputs to the GPU, through the GPU expression and `result_gpu.cpu()`. This output copy completes before the CPU timer stops. |

The CUDA-event interval excludes the initial input copies and does not return the output to the CPU. It can include idle gaps and waits between the events; it is not a sum of active kernel durations. One PyTorch expression can launch multiple kernels. These are three measurements of different amounts of work, not three consecutive execution phases. Repeat measurements to see variation rather than relying on one sample.

## Practice

Slurm assigns cluster resources to jobs. The `sbatch` commands below run the script inside a GPU allocation; they do not execute GPU work on the login host.

For an illustrative calculation, suppose the CPU expression takes 8 microseconds, host submission takes 10 microseconds in total, GPU execution takes 2 microseconds, and each of the two input copies and one output copy takes 7 microseconds. Assume these costs do not overlap and there are no other delays. The GPU computation takes 2 microseconds, but the complete GPU request takes 10 + 2 + 3 × 7 = 33 microseconds. Now suppose millions of values make the CPU take 900 microseconds, while submission and copies total 140 microseconds and GPU execution takes 100 microseconds: 240 microseconds end to end. The preferred processor changes with the workload and included operations. These are assumed numbers, not measured results or a prediction of the CUDA-event samples.

Follow the README environment setup and run Lab 10 as the single-GPU compatibility/preflight check before Lab 01. Interpret its version layers in Lesson 2. Predict the crossover for three tensor sizes, run Lab 01, and explain the result without using the phrase “the GPU is faster.” Reserve the two-node Lab 00 preflight for Lesson 12, before its collective experiment.

Start small, then repeat with the larger profile only after correctness passes. Both profiles run all three timing cases, so no source edit is required for this comparison.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/01_cpu_gpu_crossover.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/01_cpu_gpu_crossover.py --profile h100
```

## Check your results

Require FP32 agreement with the CPU expression at `rtol=1e-5, atol=1e-6`. Compare `cpu_median_ms`, the `gpu_resident` distribution, and `gpu_with_transfers_median_ms` for each element count. No particular crossover or speedup is guaranteed.

Record CPU elapsed time, the CUDA-event interval, end-to-end time including transfers, tensor size, dtype and warm-up count. For each of the two GPU cases, identify the smallest tested size that beats the CPU, or record that none did.

A crossover is a property of the operation, software stack, and system—not a universal tensor size.

## Investigate the behavior

Which measurement represents a pipeline that keeps intermediate tensors on the GPU? Which represents a one-off request with CPU inputs and a CPU output? Explain why enlarging the tensor may amortize launch overhead while increasing transfer time.

Moving work to the GPU can improve throughput while worsening single-request latency or memory pressure. Keeping control-heavy work on the CPU can be correct even when a GPU implementation exists. Choose against the service objective, not a device label.

## If something goes wrong

If small-case times round to nearly zero, increase repetitions and inspect timer resolution rather than reporting an infinite speedup. If memory allocation fails on the large profile, retain smoke results and record the capacity limit.

Stopping a CPU timer immediately after an asynchronous launch measures submission time and omits unfinished GPU work. A CPU timer can measure a complete GPU request when the required synchronization or blocking output copy occurs before the timer stops.

## Takeaways and next step

Placement decisions depend on data lifetime as well as arithmetic speed. Extend the experiment by applying several operations before copying the result back, keeping the same final expression and correctness reference when comparing alternatives.

Prefer the GPU when there is enough parallel work and reuse to repay launches and transfers; keep tiny control-heavy work on the CPU.

For every reported time, name the timer, the included operations and how completion is established.
