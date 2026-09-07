# LLM Inference and GPU Optimization on NVIDIA H100

Teach engineers to connect autoregressive model mechanics with KV capacity,
scheduling, latency, throughput, quality, parallel placement, and serving
engine behavior on one or two H100 GPUs.

The course owns vLLM, TensorRT-LLM, Triton, AIPerf, and advanced Dynamo
profiles. Heavy engines fail closed until their exact containers and artifacts
are qualified on the target cluster.

## Beginner entry and advanced progression

The opening lesson introduces the subject, explains why it is useful and describes its main workflow before introducing advanced requirements. Beginners build vocabulary and work through the small example first; experienced readers can use the entry checkpoint and then follow the detailed optimization path. A conceptual CPU exercise, where provided, does not replace the later H100 evidence requirements.

[Lab 36](reference/labs/36_kv_tiering.md) teaches tier capacity, idle TTL, LRU
eviction, restoration versus recomputation, state lost on restart and
cache invalidation after identity changes. It
runs on CPU without an engine or storage access. Its cost model prepares the
questions for a real cache campaign; it does not measure TTFT or qualify GDS.
