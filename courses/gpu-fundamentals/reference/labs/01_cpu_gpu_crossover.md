# Lab 01: Find the CPU–GPU crossover for vector work

A GPU can execute arithmetic quickly while still losing an application comparison because transferring data and launching work take time. This lab evaluates the same expression, `x * y + x`, on the CPU, on already-resident GPU tensors, and with transfers included. Its purpose is to teach you to choose an execution boundary before deciding where work belongs.

## Before you start

**Theory preparation:** Read Lesson 1 for elementwise work, CPU/resident-GPU/transfer-inclusive boundaries, warm-up, CUDA events and absolute/relative reference checks. Complete its single-GPU preflight before comparing the same formula on both devices.

Use one H100 and the Fundamentals environment after the README setup and Lab 10's single-GPU compatibility/preflight check. The two-node Lab 00 acceptance check belongs to the final collective lesson, not this local crossover experiment. Read Lesson 1's warm-up and completed-work timing rules before running. The smoke profile includes 1,024 and 1,000,000 FP32 elements; the H100 profile adds 32,000,000. No input dataset is needed.

H100 provides enormous parallel and matrix throughput, but it does not remove Python dispatch, launch latency, PCIe or network transfer, or application queueing. Large H100 peak numbers are relevant only after the measured path supplies enough eligible work.

## Concepts and code path

Floating-point implementations can differ slightly because of rounding. The `allclose` check compares each observed element with its reference and permits absolute error up to `atol + rtol * abs(reference)`. Absolute tolerance protects values near zero; relative tolerance scales with the reference magnitude. With this lab's FP32 settings and reference value 2, the allowance is 0.000021; for a zero reference it is 0.000001. Every element must satisfy its bound, not just the average error.

The program creates CPU inputs and resident GPU copies once per size. CUDA events measure resident-device work. A separate host timer includes both input copies, the expression, and the output copy back to the CPU. CPU timing measures the expression without a transfer. These are three different application contracts, not interchangeable measurements of one kernel.

## Practice

Given a CPU add that takes 8 microseconds, a GPU launch that costs 10 microseconds, a tiny resident kernel that takes 2 microseconds, and two 7-microsecond copies, resident GPU time is 2 microseconds but transfer-inclusive time is 26 microseconds. Change the input to millions of values so the CPU takes 900 microseconds while launch and copies total 140 microseconds and GPU computation takes another 100 microseconds (240 microseconds end to end). Expected observation: the placement decision flips, and the report must say which boundary produced each number.

Follow the README environment setup and run Lab 10 as the single-GPU compatibility/preflight check before Lab 01. Interpret its version layers in Lesson 2. Predict the crossover for three tensor sizes, run Lab 01, and explain the result without using the phrase “the GPU is faster.” Reserve the two-node Lab 00 preflight for Lesson 12, before its collective experiment.

Start small, then repeat with the larger profile only after correctness passes. Both profiles run all three boundaries, so no source edit is required for this comparison.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/01_cpu_gpu_crossover.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/01_cpu_gpu_crossover.py --profile h100
```

## Check your results

Require FP32 agreement with the CPU expression at `rtol=1e-5, atol=1e-6`. Compare `cpu_median_ms`, the `gpu_resident` distribution, and `gpu_with_transfers_median_ms` for each element count. No particular crossover or speedup is guaranteed.

Record CPU wall time, resident-GPU time, transfer-inclusive GPU time, tensor size, dtype and warm-up count. For each GPU boundary, identify the smallest tested size that beats the CPU, or record that none did.

A crossover is a property of the operation, software stack, and system—not a universal tensor size.

## Investigate the behavior

Which boundary represents a pipeline that keeps intermediate tensors on the GPU? Which represents a one-off CPU request? Explain why enlarging the tensor may amortize launch overhead while increasing the transfer bill.

Moving work to the GPU can improve throughput while worsening single-request latency or memory pressure. Keeping control-heavy work on the CPU can be correct even when a GPU implementation exists. Choose against the service objective, not a device label.

## If something goes wrong

If small-case times round to nearly zero, increase repetitions and inspect timer resolution rather than reporting an infinite speedup. If memory allocation fails on the large profile, retain smoke results and record the capacity limit.

Timing an asynchronous launch with a CPU clock makes GPU work appear complete before it actually finishes.

## Takeaways and next step

Placement decisions depend on data lifetime as well as arithmetic speed. Extend the experiment by applying several operations before copying the result back, keeping the same final expression and correctness reference when comparing alternatives.

Prefer the GPU when there is enough parallel work and reuse to repay launches and transfers; keep tiny control-heavy work on the CPU.

Name every CPU/GPU boundary in the lab before optimizing it.
