# Advanced Labs: Multi-GPUs Multi-Nodes communication optimization

This is an all-labs course for engineers who can already launch Slurm jobs, read a GPU timeline and validate a training or inference result. You will diagnose communication at four boundaries: GPU to GPU within a node, memory to network adapter, collective to application, and inference request to completed response.

Use a dedicated Soperator cluster with **two eight-H100 workers: sixteen GPUs total**, NVLink/NVSwitch within each worker and InfiniBand between workers. The earlier two one-GPU workers use a high-latency TCP/IP path; their multi-node timings cannot qualify this platform. A smaller participating-rank count is intentional in the mechanics labs, not a smaller hardware prerequisite.

Start with [shared environment setup](../README.md#how-to-set-up-the-lab), qualify the fabric in Labs 01–07, and follow the lab route. Each guide contains the needed definitions, commands, correctness checks, dashboard and investigation locally. There are no separate conceptual lessons. Newcomers should first complete GPU Fundamentals, GPU Performance Optimization and the relevant training or inference foundations; experienced learners can start after demonstrating one correct unprofiled run and interpreting its timeline.

The workflow is **measure → inspect → form a hypothesis → change one variable → measure again → explain**. Begin with the guided comparison, then finish the independent investigation in each guide. A defensible negative result is useful: acceptance requires equivalent work and interpretable evidence, not a speedup. Repeat important comparisons in fresh jobs and reverse their order to expose warm-cache and temporal effects.

Lab 34 isolates concurrency with identical transmitted requests and fixed,
server-verified generated-token counts. It accepts differing answer text and
zero goodput when the workload and measurements remain valid. Labs 32 and 33
retain their own output-equivalence requirements.

Use Systems to explain phase overlap and rank skew, PyTorch profiling for framework attribution, Compute for isolated local kernels, and Grafana for selected measured outcomes with job-window device context. Short network operations need measured distributions; sampled GPU utilization alone cannot explain them. Vendor runtime candidates and live qualification limits are explicit in the environment guide.

Learn to distinguish an overloaded GPU from a stalled communication path, quantify the exposed communication cost, and select a justified change for a fixed workload. Training work emphasizes useful tokens per second and the collective tail. Inference work emphasizes first-token latency, streamed delivery and requests meeting latency objectives.

Completion means a reproducible causal report: matching baseline and candidate artifacts, numerical or delivery validity, selected dashboard generation, topology and software identity, relevant rank/server traces, one changed control and a bounded decision. Neither a faster synthetic test nor a configuration flag proves production improvement.

The course owns distributed practical work. The five GPU foundations courses retain conceptual teaching and local exercises; the Soperator introduction supplies Slurm vocabulary. Provisioning and queue time are excluded from the 64-hour guided estimate. Independent experiments and production convergence studies require additional time.

Units matter. GB/s uses decimal billions of bytes; GiB uses powers of 1024. A p99 is a percentile of a stated population: requests, samples or token intervals are different populations. Always name the population and timing boundary.
